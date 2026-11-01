"""API y vistas: Resumen, Oficina y Parámetros."""
import hashlib
import hmac
import os
from pathlib import Path

import pandas as pd
from fastapi import Body, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from motor import cargas, db
from motor.modelo import DATA, _horas_pedido, config_base, ejecutar

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="Demanda TI NGR")


# ---------- Login de la aplicación (usuario, contraseña y captcha) ----------
import secrets
import random
import time

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

APP_USER = os.environ.get("APP_USER", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SECRETO = os.environ.get("SESSION_SECRET", "") or secrets.token_hex(32)
DURACION_SESION = 8 * 3600
LIBRES = {"/login", "/api/auth/login", "/api/auth/captcha", "/static/comun.css", "/favicon.ico"}


def _firmar(texto: str) -> str:
    return hmac.new(SECRETO.encode(), texto.encode(), hashlib.sha256).hexdigest()


def _sesion_valida(cookie: str | None) -> bool:
    if not (APP_USER and APP_PASSWORD):
        return True   # sin credenciales configuradas, la app queda abierta (desarrollo)
    try:
        usuario, vence, firma = (cookie or "").split("|")
        return hmac.compare_digest(firma, _firmar(f"{usuario}|{vence}")) and int(vence) > time.time()
    except ValueError:
        return False


@app.middleware("http")
async def seguridad(request: Request, call_next):
    ruta = request.url.path
    if ruta not in LIBRES and not _sesion_valida(request.cookies.get("sesion")):
        if ruta.startswith("/api/"):
            return JSONResponse({"detail": "Sesión vencida. Vuelve a ingresar."}, status_code=401)
        return RedirectResponse("/login?next=" + ruta, status_code=303)
    resp = await call_next(request)
    # Que el navegador siempre pida la versión publicada más reciente
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


def _captcha_svg(codigo: str) -> str:
    r = random.Random()
    partes = ['<svg xmlns="http://www.w3.org/2000/svg" width="180" height="56" viewBox="0 0 180 56">',
              '<rect width="180" height="56" rx="8" fill="#eef2f8"/>']
    for _ in range(7):   # ruido
        partes.append(f'<line x1="{r.randint(0,180)}" y1="{r.randint(0,56)}" x2="{r.randint(0,180)}" y2="{r.randint(0,56)}" '
                      f'stroke="hsl({r.randint(0,360)},45%,60%)" stroke-width="{r.uniform(1,2):.1f}"/>')
    for _ in range(30):
        partes.append(f'<circle cx="{r.randint(0,180)}" cy="{r.randint(0,56)}" r="{r.uniform(.6,1.6):.1f}" fill="#9aa6b8"/>')
    for i, ch in enumerate(codigo):
        x, y, rot = 22 + i * 28 + r.randint(-3, 3), 38 + r.randint(-5, 5), r.randint(-28, 28)
        partes.append(f'<text x="{x}" y="{y}" font-family="Verdana,Arial" font-size="{r.randint(24,30)}" font-weight="700" '
                      f'fill="hsl({r.randint(200,260)},55%,{r.randint(25,40)}%)" transform="rotate({rot} {x} {y})">{ch}</text>')
    partes.append("</svg>")
    return "".join(partes)


@app.get("/api/auth/captcha")
def api_captcha():
    codigo = "".join(secrets.choice("ABCDEFGHJKLMNPRSTUVWXYZ23456789") for _ in range(5))
    vence = int(time.time()) + 300
    resp = Response(_captcha_svg(codigo), media_type="image/svg+xml")
    resp.set_cookie("captcha", f"{vence}|{_firmar(codigo + '|' + str(vence))}", max_age=300, httponly=True, samesite="lax", secure=True)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/auth/login")
def api_auth_login(request: Request, body: dict = Body(...)):
    cap = request.cookies.get("captcha", "")
    try:
        vence, firma = cap.split("|")
        cap_ok = int(vence) > time.time() and hmac.compare_digest(firma, _firmar(str(body.get("captcha", "")).strip().upper() + "|" + vence))
    except ValueError:
        cap_ok = False
    if not cap_ok:
        return JSONResponse({"detail": "El código de verificación no coincide. Intenta con uno nuevo."}, status_code=400)
    usuario, clave = str(body.get("usuario", "")).strip(), str(body.get("password", ""))
    # credenciales fijas (la contraseña solo se guarda como hash) o las de las variables de entorno
    hash_fijo = "b1fec9a5f44576cdd55736af63f7994ce9eb5611b1a094a5a7c3737ba651a03f"
    ok = (usuario.lower() in (APP_USER.lower(), "demandati")) and (
        hmac.compare_digest(clave, APP_PASSWORD) or hmac.compare_digest(hashlib.sha256(clave.encode()).hexdigest(), hash_fijo))
    if not ok:
        time.sleep(1)   # frena intentos por fuerza bruta
        return JSONResponse({"detail": "Usuario o contraseña incorrectos."}, status_code=401)
    vence_s = int(time.time()) + DURACION_SESION
    resp = JSONResponse({"ok": True})
    resp.set_cookie("sesion", f"{APP_USER}|{vence_s}|{_firmar(f'{APP_USER}|{vence_s}')}", max_age=DURACION_SESION,
                    httponly=True, samesite="lax", secure=True)
    resp.delete_cookie("captcha")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("sesion")
    return resp


@app.get("/login")
def vista_login():
    return FileResponse(STATIC / "login.html")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
db.iniciar()

# ---------- Acceso para guardar y cargar datos ----------
CLAVE = os.environ.get("ADMIN_PASSWORD", "")


def _token(clave: str) -> str:
    return hmac.new(clave.encode(), b"demanda-ti-ngr", hashlib.sha256).hexdigest()


def _autorizar(token: str | None) -> None:
    if CLAVE and not (token and hmac.compare_digest(token, _token(CLAVE))):
        raise HTTPException(401, "Ingresa la contraseña para guardar o cargar datos.")


@app.post("/api/login")
def api_login(body: dict = Body(...)):
    if CLAVE and body.get("password") != CLAVE:
        raise HTTPException(401, "Contraseña incorrecta.")
    return {"token": _token(CLAVE) if CLAVE else "abierto"}


@app.get("/api/estado")
def api_estado():
    return {**db.estado(), "requiere_clave": bool(CLAVE)}


@app.post("/api/config/guardar")
def api_guardar_config(cfg: dict = Body(...), x_token: str | None = Header(default=None)):
    _autorizar(x_token)
    if not db.disponible():
        raise HTTPException(503, "No hay base de datos conectada.")
    db.guardar_config(cfg)
    return {"ok": True}


@app.get("/api/plantilla/{tipo}")
def api_plantilla(tipo: str):
    if tipo not in cargas.TIPOS:
        raise HTTPException(404, "Tipo desconocido")
    return Response(cargas.plantilla(tipo), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="plantilla_{tipo}.xlsx"'})


@app.post("/api/cargar/{tipo}")
async def api_cargar(tipo: str, archivo: UploadFile = File(...), modo: str = "reemplazar", confirmar: bool = False,
                     x_token: str | None = Header(default=None)):
    _autorizar(x_token)
    if tipo not in cargas.TIPOS:
        raise HTTPException(404, "Tipo desconocido")
    try:
        df = cargas.leer(tipo, archivo.filename or "", await archivo.read())
    except Exception as e:  # archivo ilegible
        raise HTTPException(400, f"No se pudo leer el archivo: {e}")
    filas, errores = cargas.validar(tipo, df)
    resp = {"tipo": tipo, "filas_leidas": int(len(df)), "filas_validas": len(filas), "errores": errores,
            "muestra": filas[:5], "guardado": False}
    if not confirmar:
        return resp
    if not db.disponible():
        raise HTTPException(503, "No hay base de datos conectada.")
    if not filas:
        raise HTTPException(400, "No hay filas válidas para cargar.")
    cfg = config_base()
    actual = db.leer_dataset(tipo)
    if actual is None and tipo.startswith("historico_") and modo == "agregar":
        actual = pd.read_csv(DATA / f"{tipo}.csv").drop(columns=["tipo"], errors="ignore").to_dict(orient="records")
    cfg, dataset = cargas.aplicar(tipo, filas, modo, cfg, actual)
    if cargas.TIPOS[tipo]["destino"] == "config":
        db.guardar_config(cfg)
    else:
        db.guardar_dataset(tipo, dataset)
    resp["guardado"] = True
    return resp


@app.get("/api/horas_reales")
def api_horas_reales():
    return db.leer_dataset("horas_reales") or []


def _salida(o: dict) -> dict:
    cap = o["capacidad_persona"].round(1)
    personas = []
    for nombre, g in cap.groupby("persona", sort=False):
        f = g.iloc[0]
        personas.append({"persona": nombre, "squad": f["squad"], "rol": f["rol"], "genero": f["genero"],
                         "meses": g[["mes", "horas_netas", "horas_vacaciones", "horas_gestion"]].to_dict(orient="records")})
    pr = o["proyectos"]
    pr = pr[pr["horas"] > 0].round(1)
    c = o["config"]
    pn = o["pronostico"]
    mezcla = {t: {r["nivel"]: r["mezcla"] for _, r in pn[(pn["tipo"] == t) & (pn["mes"] == 1)].iterrows()}
              for t in ["requerimiento", "incidencia"]}
    horas = {t: {n: _horas_pedido(c, t, n) for n in c["horas_por_nivel"][t]} for t in ["requerimiento", "incidencia"]}
    return {
        "mezcla": mezcla,
        "horas_pedido": horas,
        "dispatcher": c.get("dispatcher", {"tasa_rechazo": 0, "motivos": {}}),
        "prioridad_incidencias": c.get("prioridad_incidencias", {"Alto": 1, "Medio": 2, "Bajo": 3}),
        "horas_mes_tercero": float(c["capacidad"].get("horas_mes_tercero", 160)),
        "rampa_tercero": float(c["capacidad"].get("rampa_primer_mes_tercero", 0.5)),
        "terceros": c.get("terceros", []),
        "resumen": o["resumen"].round(2).to_dict(orient="records"),
        "personas": personas,
        "proyectos": pr.to_dict(orient="records"),
        "pronostico": o["pronostico"].round(3).to_dict(orient="records"),
        "n_personas": o["n_personas"],
    }


@app.get("/api/config/base")
def api_config_base():
    return config_base()


@app.post("/api/calcular")
def api_calcular(cfg: dict = Body(default={})):
    return _salida(ejecutar(cfg))


@app.get("/api/resumen")
def api_resumen():
    return ejecutar()["resumen"].round(2).to_dict(orient="records")


@app.get("/api/oficina")
def api_oficina():
    return _salida(ejecutar())


@app.get("/")
def vista_resumen():
    return FileResponse(STATIC / "index.html")


@app.get("/oficina")
def vista_oficina():
    return FileResponse(STATIC / "oficina.html")


@app.get("/api/historico_mensual")
def api_historico_mensual():
    from motor.modelo import _historico
    h = _historico(DATA)
    m = h.groupby([h["fecha"].dt.to_period("M").astype(str), "tipo"]).size().unstack(fill_value=0).reset_index()
    m.columns = ["mes"] + list(m.columns[1:])
    return m.to_dict(orient="records")


@app.get("/modelos")
def vista_modelos():
    return FileResponse(STATIC / "modelos.html")


@app.get("/parametros")
def vista_parametros():
    return FileResponse(STATIC / "parametros.html")
