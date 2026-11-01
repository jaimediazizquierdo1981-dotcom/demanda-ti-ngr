"""API y vistas: Resumen, Oficina y Parámetros."""
from pathlib import Path

from fastapi import Body, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from motor.modelo import _horas_pedido, config_base, ejecutar

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="Demanda TI NGR")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


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
        "horas_mes_tercero": float(c["capacidad"].get("horas_mes_tercero", 160)),
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


@app.get("/parametros")
def vista_parametros():
    return FileResponse(STATIC / "parametros.html")
