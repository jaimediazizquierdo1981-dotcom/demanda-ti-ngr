"""API y vista simple de resultados del motor (etapa 1: solo números)."""
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse

from motor.modelo import ejecutar as _ejecutar

STATIC = Path(__file__).resolve().parent / "static"


@lru_cache(maxsize=1)
def ejecutar():
    # Los insumos son archivos del repo: se calcula una vez por despliegue.
    return _ejecutar()

app = FastAPI(title="Demanda TI NGR — motor estadístico")

COLS = [
    ("mes_nombre", "Mes", "{}"),
    ("horas_netas", "Capacidad neta (h)", "{:,.0f}"),
    ("Tecnologia", "Proyectos TI (h)", "{:,.0f}"),
    ("Transformacional", "Proy. transformacionales (h)", "{:,.0f}"),
    ("req_esperados", "Req. esperados", "{:.1f}"),
    ("inc_esperadas", "Inc. esperadas", "{:.1f}"),
    ("horas_req_inc_p90", "Req. + inc. P90 (h)", "{:,.0f}"),
    ("demanda_total_plan", "Demanda total (h)", "{:,.0f}"),
    ("brecha_horas", "Brecha (h)", "{:,.0f}"),
    ("utilizacion", "Utilización", "{:.0%}"),
    ("personas_adicionales", "Personas adicionales", "{}"),
    ("alertar_en", "Alertar en", "{}"),
    ("personas_min_incidencias", "Personas mín. incidencias (Erlang C)", "{}"),
]


@app.get("/api/resumen")
def resumen():
    return ejecutar()["resumen"].round(2).to_dict(orient="records")


@app.get("/api/pronostico")
def pronostico():
    return ejecutar()["pronostico"].round(3).to_dict(orient="records")


@app.get("/api/capacidad")
def capacidad():
    return ejecutar()["capacidad_persona"].round(2).to_dict(orient="records")


@app.get("/", response_class=HTMLResponse)
def inicio():
    r = ejecutar()["resumen"]
    head = "".join(f"<th>{t}</th>" for _, t, _ in COLS)
    rows = []
    for _, f in r.iterrows():
        cls = ' class="neg"' if f["brecha_horas"] < 0 else ""
        rows.append("<tr>" + "".join(
            f'<td{cls if c == "brecha_horas" else ""}>{fmt.format(f[c])}</td>' for c, _, fmt in COLS) + "</tr>")
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Demanda TI NGR 2027</title>
<style>body{{font-family:Arial,sans-serif;margin:24px;color:#222}}table{{border-collapse:collapse;font-size:13px}}
th,td{{border:1px solid #ddd;padding:6px 8px;text-align:right}}th{{background:#1F3864;color:#fff}}
td:first-child{{text-align:left}}.neg{{color:#c00;font-weight:bold}}</style></head><body>
<h2>Equipo de data NGR — demanda vs. capacidad 2027</h2>
<p>Datos ficticios. Proyección con Holt-Winters + filtro de Kalman, Monte Carlo Poisson (P90, peor escenario de horas) y Erlang C.</p>
<table><tr>{head}</tr>{''.join(rows)}</table>
<p><a href="/oficina"><b>Ver la oficina animada →</b></a></p>
<p>JSON: <a href="/api/resumen">/api/resumen</a> · <a href="/api/pronostico">/api/pronostico</a> · <a href="/api/capacidad">/api/capacidad</a></p>
</body></html>"""


@app.get("/api/oficina")
def oficina():
    o = ejecutar()
    eq = o["capacidad_persona"].round(1)
    personas = []
    for nombre, g in eq.groupby("persona", sort=False):
        f = g.iloc[0]
        personas.append({"persona": nombre, "squad": f["squad"], "rol": f["rol"],
                         "meses": g[["mes", "horas_netas", "horas_vacaciones", "horas_gestion"]].to_dict(orient="records")})
    pr = o["proyectos"]
    pr = pr[pr["horas"] > 0].round(1)
    return {
        "resumen": o["resumen"].round(2).to_dict(orient="records"),
        "personas": personas,
        "proyectos": pr.to_dict(orient="records"),
    }


@app.get("/oficina")
def vista_oficina():
    return FileResponse(STATIC / "oficina.html")
