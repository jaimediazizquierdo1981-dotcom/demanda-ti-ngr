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


@app.middleware("http")
async def sin_cache(request, call_next):
    # Que el navegador siempre pida la versión publicada más reciente
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp
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
