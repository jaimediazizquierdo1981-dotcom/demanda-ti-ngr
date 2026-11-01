"""Motor estadístico de demanda y capacidad del equipo de data (etapa 1).

Calcula, mes a mes para el año del plan:
  - capacidad bruta y neta (feriados, vacaciones, descansos médicos)
  - horas comprometidas en proyectos (TI y transformacionales)
  - demanda proyectada de requerimientos (por complejidad) e incidencias (por impacto)
      * Holt-Winters y filtro de Kalman sobre el histórico mensual, promediados
      * Poisson + Monte Carlo para el rango de horas (P50 y percentil de plan)
  - Erlang C: personas mínimas atendiendo incidencias para el nivel de espera objetivo
  - brecha de horas y personas adicionales, con alerta N meses antes
Todos los insumos están en /data y son editables.
"""
from __future__ import annotations

import json
import math
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.structural import UnobservedComponents

warnings.filterwarnings("ignore")
DATA = Path(__file__).resolve().parent.parent / "data"
MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def cargar(data_dir: Path = DATA) -> dict:
    p = json.loads((data_dir / "parametros.json").read_text(encoding="utf-8"))
    hist = pd.concat(
        [pd.read_csv(data_dir / "historico_requerimientos.csv"),
         pd.read_csv(data_dir / "historico_incidencias.csv")],
        ignore_index=True,
    )
    hist["fecha"] = pd.to_datetime(hist["fecha"])
    return {
        "param": p,
        "equipo": pd.read_csv(data_dir / "equipo.csv"),
        "feriados": pd.to_datetime(pd.read_csv(data_dir / "feriados.csv")["fecha"]),
        "vacaciones": pd.read_csv(data_dir / "vacaciones.csv", parse_dates=["inicio", "fin"]),
        "proyectos": pd.read_csv(data_dir / "proyectos.csv", parse_dates=["inicio", "fin"]),
        "historico": hist,
    }


# ---------- Capacidad ----------
def dias_utiles(anio: int, feriados: pd.Series) -> pd.DatetimeIndex:
    d = pd.bdate_range(f"{anio}-01-01", f"{anio}-12-31")
    return d[~d.isin(feriados)]


def capacidad(ins: dict) -> pd.DataFrame:
    p, eq = ins["param"], ins["equipo"]
    anio, hpd = p["anio_plan"], p["horas_por_dia"]
    du = dias_utiles(anio, ins["feriados"])
    total_du = len(du)
    filas = []
    for _, per in eq.iterrows():
        hdia = per["horas_semana"] / 5
        hgest = p.get("horas_gestion_dia_por_rol", {}).get(per["rol"], 0)
        vac = ins["vacaciones"][ins["vacaciones"]["persona"] == per["persona"]]
        dias_vac = pd.DatetimeIndex([])
        for _, v in vac.iterrows():
            dias_vac = dias_vac.union(du[(du >= v["inicio"]) & (du <= v["fin"])])
        for m in range(1, 13):
            dm = du[du.month == m]
            vm = (dias_vac.month == m).sum()
            med = p["dias_descanso_medico_anual"] * len(dm) / total_du
            dias_presente = len(dm) - vm - med
            filas.append({
                "mes": m, "persona": per["persona"], "squad": per["squad"], "rol": per["rol"],
                "dias_utiles": len(dm),
                "horas_brutas": len(dm) * hdia,
                "horas_gestion": dias_presente * hgest,
                "horas_vacaciones": vm * hdia,
                "horas_descanso_medico": med * hdia,
            })
    df = pd.DataFrame(filas)
    df["horas_netas"] = (df["horas_brutas"] - df["horas_vacaciones"]
                         - df["horas_descanso_medico"] - df["horas_gestion"])
    return df


# ---------- Proyectos ----------
def carga_proyectos(ins: dict) -> pd.DataFrame:
    p, eq = ins["param"], ins["equipo"]
    du = dias_utiles(p["anio_plan"], ins["feriados"])
    horas_equipo_dia = eq["horas_semana"].sum() / 5
    horas_persona_dia = eq["horas_semana"].mean() / 5
    filas = []
    for _, pr in ins["proyectos"].iterrows():
        dias = du[(du >= pr["inicio"]) & (du <= pr["fin"])]
        # base "equipo": % de las 6 personas; base "persona": % de un solo recurso
        hdia = horas_persona_dia if str(pr.get("base", "equipo")) == "persona" else horas_equipo_dia
        for m in range(1, 13):
            n = (dias.month == m).sum()
            filas.append({"mes": m, "codigo": pr["codigo"], "proyecto": pr["proyecto"],
                          "tipo": pr["tipo"], "horas": n * hdia * pr["participacion"]})
    return pd.DataFrame(filas)


# ---------- Pronóstico de cantidades ----------
def _serie_mensual(hist: pd.DataFrame, tipo: str) -> pd.Series:
    h = hist[hist["tipo"] == tipo]
    s = h.set_index("fecha").resample("MS").size()
    idx = pd.date_range(hist["fecha"].min().to_period("M").to_timestamp(),
                        hist["fecha"].max().to_period("M").to_timestamp(), freq="MS")
    return s.reindex(idx, fill_value=0).astype(float)


def _holt_winters(s: pd.Series, h: int) -> np.ndarray:
    try:
        m = ExponentialSmoothing(s + 1, trend="add", damped_trend=True, seasonal="add",
                                 seasonal_periods=12, initialization_method="estimated").fit()
        return np.asarray(m.forecast(h)) - 1
    except Exception:
        m = ExponentialSmoothing(s, trend="add", damped_trend=True,
                                 initialization_method="estimated").fit()
        return np.asarray(m.forecast(h))


def _kalman(s: pd.Series, h: int) -> np.ndarray:
    # Nivel y tendencia locales + estacionalidad, estimados con filtro de Kalman
    try:
        m = UnobservedComponents(s, level="local level", freq_seasonal=[{"period": 12, "harmonics": 2}]).fit(disp=False)
    except Exception:
        m = UnobservedComponents(s, level="local level").fit(disp=False)
    return np.asarray(m.forecast(h))


def pronostico(ins: dict) -> pd.DataFrame:
    hist, anio = ins["historico"], ins["param"]["anio_plan"]
    ultimo = hist["fecha"].max().to_period("M")
    meses_hasta = (pd.Period(f"{anio}-12", "M") - ultimo).n
    # Se pronostica el total mensual por tipo (serie más estable) y se reparte
    # según la mezcla histórica de complejidad (requerimientos) o impacto (incidencias).
    filas = []
    fechas = pd.period_range(ultimo + 1, periods=meses_hasta, freq="M")
    for tipo, niveles in ins["param"]["horas_por_nivel"].items():
        s = _serie_mensual(hist, tipo)
        hw, kf = _holt_winters(s, meses_hasta), _kalman(s, meses_hasta)
        tot = np.clip((hw + kf) / 2, 0, None)
        mezcla = hist[hist["tipo"] == tipo]["nivel"].value_counts(normalize=True)
        for f, a, b, c in zip(fechas, hw, kf, tot):
            if f.year != anio:
                continue
            for nivel in niveles:
                w = float(mezcla.get(nivel, 0))
                filas.append({"mes": f.month, "tipo": tipo, "nivel": nivel, "mezcla": w,
                              "total_holt_winters": max(a, 0), "total_kalman": max(b, 0),
                              "total_tipo": c, "lambda": c * w,
                              "promedio_historico_tipo": s.mean()})
    return pd.DataFrame(filas)


# ---------- Monte Carlo (Poisson) ----------
def monte_carlo(ins: dict, prono: pd.DataFrame) -> pd.DataFrame:
    p = ins["param"]
    mc = p["monte_carlo"]
    rng = np.random.default_rng(mc["semilla"])
    n, pct = mc["simulaciones"], mc["percentil_plan"]
    filas = []
    for m in range(1, 13):
        tot = np.zeros(n)
        peor = 0.0
        det = {}
        for _, r in prono[prono["mes"] == m].iterrows():
            lo, hi = p["horas_por_nivel"][r["tipo"]][r["nivel"]]
            k = rng.poisson(r["lambda"], n)
            horas = hi if p["estimacion"] == "peor_escenario" else (lo + hi) / 2
            tot += k * horas
            peor += r["lambda"] * hi
            det[r["tipo"]] = det.get(r["tipo"], 0) + r["lambda"]
        filas.append({"mes": m,
                      "req_esperados": det.get("requerimiento", 0),
                      "inc_esperadas": det.get("incidencia", 0),
                      "horas_req_inc_esperadas": peor,
                      "horas_req_inc_p50": float(np.percentile(tot, 50)),
                      f"horas_req_inc_p{pct}": float(np.percentile(tot, pct))})
    return pd.DataFrame(filas)


# ---------- Erlang C ----------
def erlang_c(agentes: int, carga: float) -> float:
    if agentes <= carga:
        return 1.0
    s = sum(carga ** k / math.factorial(k) for k in range(agentes))
    top = carga ** agentes / math.factorial(agentes) * agentes / (agentes - carga)
    return top / (s + top)


def personas_incidencias(ins: dict, prono: pd.DataFrame, cap: pd.DataFrame) -> pd.DataFrame:
    p = ins["param"]
    obj = p["erlang_c"]["objetivo_prob_espera"]
    filas = []
    for m in range(1, 13):
        inc = prono[(prono["mes"] == m) & (prono["tipo"] == "incidencia")]
        llegadas = inc["lambda"].sum()
        if llegadas <= 0:
            filas.append({"mes": m, "personas_min_incidencias": 0, "prob_espera": 0.0})
            continue
        horas_serv = sum(r["lambda"] * p["horas_por_nivel"]["incidencia"][r["nivel"]][1]
                         for _, r in inc.iterrows()) / llegadas
        horas_mes = cap[cap["mes"] == m]["dias_utiles"].iloc[0] * p["horas_por_dia"]
        carga = llegadas / horas_mes * horas_serv  # erlangs
        a = 1
        while erlang_c(a, carga) > obj:
            a += 1
        filas.append({"mes": m, "personas_min_incidencias": a, "prob_espera": erlang_c(a, carga),
                      "carga_erlangs": carga})
    return pd.DataFrame(filas)


# ---------- Resultado ----------
def ejecutar(data_dir: Path = DATA) -> dict:
    ins = cargar(data_dir)
    p = ins["param"]
    pct = p["monte_carlo"]["percentil_plan"]
    cap = capacidad(ins)
    proy = carga_proyectos(ins)
    prono = pronostico(ins)
    mc = monte_carlo(ins, prono)
    erl = personas_incidencias(ins, prono, cap)

    capm = cap.groupby("mes")[["horas_brutas", "horas_vacaciones", "horas_descanso_medico",
                               "horas_gestion", "horas_netas"]].sum()
    proym = proy.pivot_table(index="mes", columns="tipo", values="horas", aggfunc="sum").fillna(0)
    res = capm.join(proym).join(mc.set_index("mes")).join(erl.set_index("mes")).reset_index()
    res["horas_proyectos"] = res[["Tecnologia", "Transformacional"]].sum(axis=1)
    res["demanda_total_plan"] = res["horas_proyectos"] + res[f"horas_req_inc_p{pct}"]
    res["brecha_horas"] = res["horas_netas"] - res["demanda_total_plan"]
    res["utilizacion"] = res["demanda_total_plan"] / res["horas_netas"]
    horas_netas_persona = res["horas_netas"] / len(ins["equipo"])
    res["personas_adicionales"] = np.where(res["brecha_horas"] < 0,
                                           np.ceil(-res["brecha_horas"] / horas_netas_persona), 0).astype(int)
    ant = p["anticipacion_meses"]
    res["alertar_en"] = [
        (MESES[m - 1 - ant] + (f" {p['anio_plan'] - 1}" if m - ant < 1 else "")) if n > 0 else ""
        for m, n in zip(res["mes"], res["personas_adicionales"])
    ]
    res.insert(1, "mes_nombre", [MESES[m - 1] for m in res["mes"]])
    return {"resumen": res, "capacidad_persona": cap, "proyectos": proy, "pronostico": prono}


if __name__ == "__main__":
    r = ejecutar()["resumen"]
    cols = ["mes_nombre", "horas_netas", "Tecnologia", "Transformacional", "req_esperados", "inc_esperadas",
            "horas_req_inc_esperadas", "horas_req_inc_p90", "brecha_horas", "utilizacion",
            "personas_adicionales", "alertar_en", "personas_min_incidencias"]
    pd.set_option("display.width", 250)
    print(r[cols].round(1).to_string(index=False))
