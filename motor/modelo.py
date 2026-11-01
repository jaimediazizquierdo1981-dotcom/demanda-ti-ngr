"""Motor estadístico de demanda y capacidad del equipo de data.

Calcula, mes a mes para el año del plan:
  - capacidad bruta y neta (feriados, vacaciones, descansos médicos, gestión de jefes)
  - horas comprometidas en proyectos (TI y transformacionales)
  - demanda proyectada de requerimientos (por complejidad) e incidencias (por impacto)
      * Holt-Winters y filtro de Kalman sobre el histórico mensual (o su promedio)
      * Poisson + Monte Carlo para el colchón de variabilidad (percentil de plan)
  - Erlang C: personas mínimas atendiendo incidencias para el nivel de espera objetivo
  - brecha de horas y personas adicionales, con alerta N meses antes

Todo se puede ajustar con una configuración (ver config_base): criterios que se
activan o desactivan, variables numéricas y las tablas de equipo, proyectos,
vacaciones y feriados. Los valores base están en /data.
"""
from __future__ import annotations

import copy
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


# ---------- Configuración ----------
def _historico(data_dir: Path) -> pd.DataFrame:
    hist = pd.concat(
        [pd.read_csv(data_dir / "historico_requerimientos.csv"),
         pd.read_csv(data_dir / "historico_incidencias.csv")],
        ignore_index=True,
    )
    hist["fecha"] = pd.to_datetime(hist["fecha"])
    return hist


def config_base(data_dir: Path = DATA) -> dict:
    """Configuración completa con los valores de /data (lo que muestra la pestaña Parámetros)."""
    p = json.loads((data_dir / "parametros.json").read_text(encoding="utf-8"))
    eq = pd.read_csv(data_dir / "equipo.csv")
    eq["incluir"] = True
    pr = pd.read_csv(data_dir / "proyectos.csv")
    pr["incluir"] = True
    fe = pd.read_csv(data_dir / "feriados.csv")
    fe["incluir"] = True
    va = pd.read_csv(data_dir / "vacaciones.csv")
    p["equipo"] = eq.to_dict(orient="records")
    p["proyectos"] = pr.to_dict(orient="records")
    p["feriados"] = fe.to_dict(orient="records")
    p["vacaciones"] = va.to_dict(orient="records")
    return p


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def preparar(cfg: dict | None = None, data_dir: Path = DATA) -> dict:
    """Combina la configuración recibida con la base y arma los insumos del cálculo."""
    c = _merge(config_base(data_dir), cfg or {})
    crit = c["criterios"]
    eq = pd.DataFrame(c["equipo"])
    eq = eq[eq["incluir"].astype(bool)].copy()
    eq["horas_semana"] = pd.to_numeric(eq["horas_semana"], errors="coerce").fillna(0)
    eq["genero"] = eq["genero"].fillna("Hombre") if "genero" in eq else "Hombre"
    if "dispatcher" not in c:
        c["dispatcher"] = {"tasa_rechazo": 0, "motivos": {}}
    pr = pd.DataFrame(c["proyectos"])
    pr = pr[pr["incluir"].astype(bool)].copy()
    if not crit["proyectos_ti"]:
        pr = pr[pr["tipo"] != "Tecnologia"]
    if not crit["proyectos_transformacionales"]:
        pr = pr[pr["tipo"] != "Transformacional"]
    pr["inicio"], pr["fin"] = pd.to_datetime(pr["inicio"]), pd.to_datetime(pr["fin"])
    pr["participacion"] = pd.to_numeric(pr["participacion"], errors="coerce").fillna(0)
    fe = pd.DataFrame(c["feriados"])
    fe = fe[fe["incluir"].astype(bool)] if crit["feriados"] else fe.iloc[0:0]
    va = pd.DataFrame(c["vacaciones"], columns=["persona", "inicio", "fin"])
    if not crit["vacaciones"]:
        va = va.iloc[0:0]
    va["inicio"], va["fin"] = pd.to_datetime(va["inicio"]), pd.to_datetime(va["fin"])
    return {
        "cfg": c,
        "equipo": eq.reset_index(drop=True),
        "proyectos": pr.reset_index(drop=True),
        "feriados": pd.to_datetime(fe["fecha"]) if len(fe) else pd.Series([], dtype="datetime64[ns]"),
        "vacaciones": va,
        "historico": _historico(data_dir),
    }


# ---------- Capacidad ----------
def dias_utiles(anio: int, feriados: pd.Series) -> pd.DatetimeIndex:
    d = pd.bdate_range(f"{anio}-01-01", f"{anio}-12-31")
    return d[~d.isin(feriados)]


def capacidad(ins: dict) -> pd.DataFrame:
    c = ins["cfg"]
    crit, cap = c["criterios"], c["capacidad"]
    du = dias_utiles(c["anio_plan"], ins["feriados"])
    total_du = len(du)
    med_anual = cap["dias_descanso_medico_anual"] if crit["descansos_medicos"] else 0
    filas = []
    for _, per in ins["equipo"].iterrows():
        # horas por día de la persona, escaladas a la jornada configurada
        hdia = per["horas_semana"] / 5 * cap["horas_por_dia"] / 8
        hgest = cap["horas_gestion_dia_jefe"] if (crit["gestion_jefes"] and per["rol"] == "Jefe") else 0
        vac = ins["vacaciones"][ins["vacaciones"]["persona"] == per["persona"]]
        dias_vac = pd.DatetimeIndex([])
        for _, v in vac.iterrows():
            dias_vac = dias_vac.union(du[(du >= v["inicio"]) & (du <= v["fin"])])
        for m in range(1, 13):
            dm = du[du.month == m]
            vm = int((dias_vac.month == m).sum())
            med = med_anual * len(dm) / total_du if total_du else 0
            dias_presente = max(0, len(dm) - vm - med)
            filas.append({
                "mes": m, "persona": per["persona"], "squad": per["squad"], "rol": per["rol"],
                "genero": per.get("genero", "Hombre"),
                "dias_utiles": len(dm),
                "horas_brutas": len(dm) * hdia,
                "horas_vacaciones": vm * hdia,
                "horas_descanso_medico": med * hdia,
                "horas_gestion": dias_presente * hgest,
            })
    df = pd.DataFrame(filas, columns=["mes", "persona", "squad", "rol", "genero", "dias_utiles", "horas_brutas",
                                      "horas_vacaciones", "horas_descanso_medico", "horas_gestion"])
    df["horas_netas"] = (df["horas_brutas"] - df["horas_vacaciones"]
                         - df["horas_descanso_medico"] - df["horas_gestion"])
    return df


# ---------- Proyectos ----------
def carga_proyectos(ins: dict) -> pd.DataFrame:
    c, eq = ins["cfg"], ins["equipo"]
    du = dias_utiles(c["anio_plan"], ins["feriados"])
    escala = c["capacidad"]["horas_por_dia"] / 8
    horas_equipo_dia = eq["horas_semana"].sum() / 5 * escala
    horas_persona_dia = (eq["horas_semana"].mean() / 5 * escala) if len(eq) else 0
    horas_jefes_dia = eq[eq["rol"] == "Jefe"]["horas_semana"].sum() / 5 * escala
    filas = []
    for _, pr in ins["proyectos"].iterrows():
        dias = du[(du >= pr["inicio"]) & (du <= pr["fin"])]
        # base "equipo": % de todo el equipo; "jefes": % de los jefes; "persona": % de un solo recurso
        base = str(pr.get("base", "equipo"))
        hdia = {"persona": horas_persona_dia, "jefes": horas_jefes_dia}.get(base, horas_equipo_dia)
        for m in range(1, 13):
            n = int((dias.month == m).sum())
            filas.append({"mes": m, "codigo": pr["codigo"], "proyecto": pr["proyecto"],
                          "tipo": pr["tipo"], "base": base, "horas": n * hdia * pr["participacion"]})
    return pd.DataFrame(filas, columns=["mes", "codigo", "proyecto", "tipo", "base", "horas"])


# ---------- Pronóstico de cantidades ----------
_CACHE_PRONO: dict = {}


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
    # Nivel local + estacionalidad, estimados con filtro de Kalman
    try:
        m = UnobservedComponents(s, level="local level", freq_seasonal=[{"period": 12, "harmonics": 2}]).fit(disp=False)
    except Exception:
        m = UnobservedComponents(s, level="local level").fit(disp=False)
    return np.asarray(m.forecast(h))


def _base_pronostico(hist: pd.DataFrame, anio: int) -> pd.DataFrame:
    """Totales mensuales por tipo con ambos modelos. Es lo más costoso; se guarda en caché."""
    key = (anio, len(hist), str(hist["fecha"].max()))
    if key in _CACHE_PRONO:
        return _CACHE_PRONO[key]
    ultimo = hist["fecha"].max().to_period("M")
    h = (pd.Period(f"{anio}-12", "M") - ultimo).n
    fechas = pd.period_range(ultimo + 1, periods=h, freq="M")
    filas = []
    for tipo in ["requerimiento", "incidencia"]:
        s = _serie_mensual(hist, tipo)
        hw, kf = _holt_winters(s, h), _kalman(s, h)
        for f, a, b in zip(fechas, hw, kf):
            if f.year == anio:
                filas.append({"mes": f.month, "tipo": tipo, "holt_winters": max(a, 0),
                              "kalman": max(b, 0), "promedio_historico": s.mean()})
    df = pd.DataFrame(filas)
    _CACHE_PRONO[key] = df
    return df


def pronostico(ins: dict) -> pd.DataFrame:
    c, hist = ins["cfg"], ins["historico"]
    crit, dem = c["criterios"], c["demanda"]
    base = _base_pronostico(hist, c["anio_plan"])
    filas = []
    for _, r in base.iterrows():
        tipo = r["tipo"]
        activo = crit["requerimientos"] if tipo == "requerimiento" else crit["incidencias"]
        if not crit["tendencia_y_estacionalidad"]:
            total = r["promedio_historico"]
        elif dem["metodo"] == "holt_winters":
            total = r["holt_winters"]
        elif dem["metodo"] == "kalman":
            total = r["kalman"]
        else:
            total = (r["holt_winters"] + r["kalman"]) / 2
        total = total * float(dem["factor_ajuste"]) if activo else 0.0
        mezcla = hist[hist["tipo"] == tipo]["nivel"].value_counts(normalize=True)
        for nivel in c["horas_por_nivel"][tipo]:
            w = float(mezcla.get(nivel, 0))
            filas.append({"mes": r["mes"], "tipo": tipo, "nivel": nivel, "mezcla": w,
                          "total_holt_winters": r["holt_winters"], "total_kalman": r["kalman"],
                          "total_tipo": total, "lambda": total * w})
    return pd.DataFrame(filas)


def _horas_pedido(c: dict, tipo: str, nivel: str) -> float:
    lo, hi = c["horas_por_nivel"][tipo][nivel]
    return float(hi) if c["demanda"]["estimacion_horas"] == "peor_escenario" else (float(lo) + float(hi)) / 2


# ---------- Monte Carlo (Poisson) ----------
def monte_carlo(ins: dict, prono: pd.DataFrame) -> pd.DataFrame:
    c = ins["cfg"]
    rk = c["riesgo"]
    rng = np.random.default_rng(int(rk["semilla"]))
    n, pct = int(rk["simulaciones"]), float(rk["percentil_plan"])
    filas = []
    for m in range(1, 13):
        tot = np.zeros(n)
        esp = {"requerimiento": 0.0, "incidencia": 0.0}
        cant = {"requerimiento": 0.0, "incidencia": 0.0}
        for _, r in prono[prono["mes"] == m].iterrows():
            h = _horas_pedido(c, r["tipo"], r["nivel"])
            tot += rng.poisson(r["lambda"], n) * h
            esp[r["tipo"]] += r["lambda"] * h
            cant[r["tipo"]] += r["lambda"]
        esperado = esp["requerimiento"] + esp["incidencia"]
        plan = float(np.percentile(tot, pct)) if c["criterios"]["colchon_variabilidad"] else esperado
        filas.append({"mes": m,
                      "req_esperados": cant["requerimiento"], "inc_esperadas": cant["incidencia"],
                      "horas_req": esp["requerimiento"], "horas_inc": esp["incidencia"],
                      "horas_req_inc_esperadas": esperado,
                      "colchon": max(0.0, plan - esperado),
                      "horas_req_inc_plan": max(plan, esperado)})
    return pd.DataFrame(filas)


# ---------- Erlang C ----------
def erlang_c(agentes: int, carga: float) -> float:
    if agentes <= carga:
        return 1.0
    s = sum(carga ** k / math.factorial(k) for k in range(agentes))
    top = carga ** agentes / math.factorial(agentes) * agentes / (agentes - carga)
    return top / (s + top)


def personas_incidencias(ins: dict, prono: pd.DataFrame, cap: pd.DataFrame) -> pd.DataFrame:
    c = ins["cfg"]
    obj = float(c["riesgo"]["objetivo_prob_espera"])
    filas = []
    for m in range(1, 13):
        inc = prono[(prono["mes"] == m) & (prono["tipo"] == "incidencia")]
        llegadas = inc["lambda"].sum()
        capm = cap[cap["mes"] == m]
        if llegadas <= 0 or capm.empty:
            filas.append({"mes": m, "personas_min_incidencias": 0, "prob_espera": 0.0, "carga_erlangs": 0.0})
            continue
        horas_serv = sum(r["lambda"] * _horas_pedido(c, "incidencia", r["nivel"]) for _, r in inc.iterrows()) / llegadas
        horas_mes = max(1, capm["dias_utiles"].iloc[0]) * c["capacidad"]["horas_por_dia"]
        carga = llegadas / horas_mes * horas_serv  # erlangs
        a = 1
        while erlang_c(a, carga) > obj and a < 50:
            a += 1
        filas.append({"mes": m, "personas_min_incidencias": a, "prob_espera": erlang_c(a, carga),
                      "carga_erlangs": carga})
    return pd.DataFrame(filas)


# ---------- Resultado ----------
def ejecutar(cfg: dict | None = None, data_dir: Path = DATA) -> dict:
    ins = preparar(cfg, data_dir)
    c = ins["cfg"]
    rk = c["riesgo"]
    cap = capacidad(ins)
    proy = carga_proyectos(ins)
    prono = pronostico(ins)
    mc = monte_carlo(ins, prono)
    erl = personas_incidencias(ins, prono, cap)

    meses = pd.DataFrame({"mes": range(1, 13)})
    capm = cap.groupby("mes")[["horas_brutas", "horas_vacaciones", "horas_descanso_medico",
                               "horas_gestion", "horas_netas"]].sum().reset_index()
    proym = (proy.pivot_table(index="mes", columns="tipo", values="horas", aggfunc="sum")
             .reindex(columns=["Tecnologia", "Transformacional"]).fillna(0).reset_index())
    res = (meses.merge(capm, on="mes", how="left").merge(proym, on="mes", how="left")
           .merge(mc, on="mes", how="left").merge(erl, on="mes", how="left").fillna(0))
    res["horas_proyectos"] = res["Tecnologia"] + res["Transformacional"]
    res["demanda_total_plan"] = res["horas_proyectos"] + res["horas_req_inc_plan"]
    res["brecha_horas"] = res["horas_netas"] - res["demanda_total_plan"]
    res["utilizacion"] = np.where(res["horas_netas"] > 0, res["demanda_total_plan"] / res["horas_netas"].replace(0, np.nan), 0)
    n_pers = max(1, len(ins["equipo"]))
    horas_netas_persona = (res["horas_netas"] / n_pers).replace(0, np.nan)
    tol = float(rk.get("tolerancia_brecha_horas", 0))
    res["personas_adicionales"] = np.where(
        res["brecha_horas"] < -tol, np.ceil(-res["brecha_horas"] / horas_netas_persona), 0).astype(int)
    ant = int(rk["anticipacion_meses"])

    def alerta(m: int, n: int) -> str:
        if n <= 0:
            return ""
        k = m - 1 - ant
        return MESES[k % 12] + (f" {c['anio_plan'] - 1}" if k < 0 else "")

    res["alertar_en"] = [alerta(m, n) for m, n in zip(res["mes"], res["personas_adicionales"])]

    # Arrastre: lo que no se resuelve en un mes pasa al siguiente (sin terceros)
    arrastrar = c["criterios"].get("arrastre_pendientes", True)
    entrante, pend = [], []
    acum = 0.0
    for dem, cap_m in zip(res["demanda_total_plan"], res["horas_netas"]):
        entrante.append(acum)
        resto = max(0.0, dem + acum - cap_m)
        pend.append(resto)
        acum = resto if arrastrar else 0.0
    res["arrastre_entrante"] = entrante
    res["pendiente_cierre"] = pend
    # Terceros: cubren cada mes lo que excede la capacidad del equipo, así no se acumula pendiente
    h3 = float(c["capacidad"].get("horas_mes_tercero", 160)) or 160.0
    res["exceso_horas"] = np.maximum(0, res["demanda_total_plan"] - res["horas_netas"])
    res["exceso_horas"] = np.where(res["exceso_horas"] > tol, res["exceso_horas"], 0)
    res["terceros"] = np.ceil(res["exceso_horas"] / h3).astype(int)
    res.insert(1, "mes_nombre", [MESES[m - 1] for m in res["mes"]])
    res = res.replace([np.inf, -np.inf], 0)
    return {"resumen": res, "capacidad_persona": cap, "proyectos": proy, "pronostico": prono,
            "config": c, "n_personas": len(ins["equipo"])}


if __name__ == "__main__":
    r = ejecutar()["resumen"]
    cols = ["mes_nombre", "horas_netas", "Tecnologia", "Transformacional", "horas_req", "horas_inc", "colchon",
            "demanda_total_plan", "brecha_horas", "utilizacion", "personas_adicionales", "alertar_en"]
    pd.set_option("display.width", 250)
    print(r[cols].round(1).to_string(index=False))
