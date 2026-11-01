"""Carga de archivos (Excel o CSV) desde la pestaña Parámetros: plantillas, lectura y validación."""
from __future__ import annotations

import io
import re
import unicodedata

import pandas as pd

NIVELES_REQ = ["Alta", "Media", "Baja"]
NIVELES_INC = ["Alto", "Medio", "Bajo"]

# columnas: (nombre interno, encabezado de la plantilla, obligatoria, ejemplo)
TIPOS = {
    "proyectos_transformacionales": {
        "titulo": "Plan de proyectos transformacionales", "destino": "config",
        "cols": [("codigo", "Código", True, "T1"), ("proyecto", "Proyecto", True, "Cliente Único"),
                 ("inicio", "Inicio", True, "2027-02-01"), ("fin", "Fin", True, "2027-11-30"),
                 ("participacion", "Participación %", True, 15), ("base", "Base (equipo o n° de miembros)", False, "equipo")],
    },
    "proyectos_ti": {
        "titulo": "Plan de proyectos tecnológicos", "destino": "config",
        "cols": [("codigo", "Código", True, "TI1"), ("proyecto", "Proyecto", True, "ONE - Gestión de tienda"),
                 ("inicio", "Inicio", True, "2027-04-05"), ("fin", "Fin", True, "2027-08-20"),
                 ("participacion", "Participación %", True, 15), ("base", "Base (equipo o n° de miembros)", False, "equipo")],
    },
    "historico_requerimientos": {
        "titulo": "Histórico de requerimientos", "destino": "dataset",
        "cols": [("fecha", "Fecha registro", True, "2026-03-15"), ("area", "Área solicitante", False, "Comercial"),
                 ("marca", "Marca", False, "Bembos"), ("squad", "Squad", False, "Comercial, Supply y Marcas"),
                 ("asignado", "Asignado a", False, "Luis Chavez"), ("nivel", "Complejidad (Alta/Media/Baja)", True, "Media"),
                 ("horas_reales", "Horas reales", False, 11)],
    },
    "historico_incidencias": {
        "titulo": "Histórico de incidencias", "destino": "dataset",
        "cols": [("fecha", "Fecha registro", True, "2026-03-15"), ("area", "Área solicitante", False, "Finanzas"),
                 ("marca", "Marca", False, "Popeyes"), ("squad", "Squad", False, "Finanzas, GDH y Sistemas"),
                 ("asignado", "Asignado a", False, "Omar Llontop"), ("nivel", "Impacto (Alto/Medio/Bajo)", True, "Alto"),
                 ("horas_reales", "Horas reales", False, 4)],
    },
    "equipo": {
        "titulo": "Equipo", "destino": "config",
        "cols": [("persona", "Persona", True, "Diana Sebastian"), ("squad", "Squad", True, "Finanzas, GDH y Sistemas"),
                 ("rol", "Rol (Jefe/Equipo)", True, "Equipo"), ("genero", "Género (Hombre/Mujer)", False, "Mujer"),
                 ("cargo", "Cargo", False, "Analytics Engineer - Finanzas"), ("horas_semana", "Horas por semana", True, 40)],
    },
    "vacaciones": {
        "titulo": "Vacaciones", "destino": "config",
        "cols": [("persona", "Persona", True, "Diana Sebastian"), ("inicio", "Inicio", True, "2027-01-04"),
                 ("fin", "Fin", True, "2027-01-18")],
    },
    "feriados": {
        "titulo": "Feriados", "destino": "config",
        "cols": [("fecha", "Fecha", True, "2027-07-28"), ("feriado", "Feriado", True, "Fiestas Patrias")],
    },
    "horas_reales": {
        "titulo": "Horas reales ejecutadas por mes", "destino": "dataset",
        "cols": [("mes", "Mes (1-12)", True, 1), ("tipo", "Tipo (Proyectos/Requerimientos/Incidencias)", True, "Requerimientos"),
                 ("horas", "Horas", True, 120)],
    },
}

# encabezados alternativos aceptados (ya normalizados)
ALIAS = {
    "fecha_registro": "fecha", "area_solicitante": "area", "asignado_a": "asignado",
    "complejidad": "nivel", "impacto": "nivel", "impacto_al_negocio": "nivel", "complejidad_alta_media_baja": "nivel",
    "impacto_alto_medio_bajo": "nivel", "horas": "horas_reales", "participacion_%": "participacion",
    "participacion": "participacion", "base_equipo_o_n_de_miembros": "base", "rol_jefe_equipo": "rol",
    "genero_hombre_mujer": "genero", "horas_por_semana": "horas_semana", "horas_semana": "horas_semana",
    "mes_1_12": "mes", "tipo_proyectos_requerimientos_incidencias": "tipo", "id": "id",
}


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode().lower().strip()
    t = re.sub(r"[()/.,°º]", " ", t)
    t = re.sub(r"\s+", "_", t.strip())
    return t


def plantilla(tipo: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    d = TIPOS[tipo]
    wb = Workbook()
    ws = wb.active
    ws.title = "Datos"
    for j, (_, enc, oblig, ej) in enumerate(d["cols"], 1):
        c = ws.cell(1, j, enc)
        c.font = Font(name="Arial", bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3864" if oblig else "6B7280")
        ws.cell(2, j, ej).font = Font(name="Arial")
        ws.column_dimensions[c.column_letter].width = max(16, len(enc) + 4)
    ins = wb.create_sheet("Instrucciones")
    lineas = [d["titulo"], "", "La fila 2 es un ejemplo: reemplázala con tus datos.",
              "Columnas azules = obligatorias; grises = opcionales.",
              "Fechas en formato AAAA-MM-DD (o fecha de Excel).",
              "Al cargar puedes elegir Reemplazar (borra lo anterior) o Agregar."]
    for i, t in enumerate(lineas, 1):
        ins.cell(i, 1, t).font = Font(name="Arial", bold=(i == 1))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def leer(tipo: str, nombre_archivo: str, contenido: bytes) -> pd.DataFrame:
    if nombre_archivo.lower().endswith((".xlsx", ".xlsm", ".xls")):
        hojas = pd.read_excel(io.BytesIO(contenido), sheet_name=None)
        # usa la hoja cuyo nombre se parezca al tipo; si no, la primera
        clave = {"historico_requerimientos": "requer", "historico_incidencias": "incid"}.get(tipo, "datos")
        hoja = next((n for n in hojas if clave in _norm(n)), next(iter(hojas)))
        df = hojas[hoja]
    else:
        texto = contenido.decode("utf-8-sig", errors="replace")
        sep = ";" if texto.count(";") > texto.count(",") else ","
        df = pd.read_csv(io.StringIO(texto), sep=sep)
    df.columns = [ALIAS.get(_norm(c), _norm(c)) for c in df.columns]
    return df.dropna(how="all")


def validar(tipo: str, df: pd.DataFrame) -> tuple[list[dict], list[str]]:
    """Devuelve (filas válidas, errores)."""
    d = TIPOS[tipo]
    errores: list[str] = []
    faltan = [enc for col, enc, ob, _ in d["cols"] if ob and col not in df.columns]
    if faltan:
        return [], ["Faltan columnas obligatorias: " + ", ".join(faltan)]
    filas = []
    for i, r in df.iterrows():
        n = i + 2  # fila en Excel (con encabezado)
        f, err = {}, []
        for col, enc, oblig, _ in d["cols"]:
            v = r.get(col)
            vacio = v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == ""
            if vacio:
                if oblig:
                    err.append(f"'{enc}' vacío")
                continue
            if col in ("fecha", "inicio", "fin"):
                fecha = pd.to_datetime(v, errors="coerce", dayfirst=isinstance(v, str) and "/" in v)
                if pd.isna(fecha):
                    err.append(f"'{enc}' no es una fecha válida ({v})")
                else:
                    f[col] = fecha.strftime("%Y-%m-%d")
            elif col in ("participacion", "horas_reales", "horas_semana", "horas", "mes"):
                try:
                    x = float(str(v).replace("%", "").replace(",", "."))
                except ValueError:
                    err.append(f"'{enc}' no es un número ({v})")
                    continue
                if col == "participacion" and x > 1:
                    x = x / 100
                if col == "mes" and not 1 <= x <= 12:
                    err.append("'Mes' debe estar entre 1 y 12")
                f[col] = int(x) if col == "mes" else x
            elif col == "nivel":
                ok = NIVELES_REQ if tipo == "historico_requerimientos" else NIVELES_INC
                s = str(v).strip().capitalize()
                if s not in ok:
                    err.append(f"'{enc}' debe ser {'/'.join(ok)} ({v})")
                f[col] = s
            elif col == "rol":
                s = str(v).strip().capitalize()
                if s not in ("Jefe", "Equipo"):
                    err.append(f"'Rol' debe ser Jefe o Equipo ({v})")
                f[col] = s
            elif col == "tipo":
                s = str(v).strip().capitalize()
                if s not in ("Proyectos", "Requerimientos", "Incidencias"):
                    err.append(f"'Tipo' debe ser Proyectos, Requerimientos o Incidencias ({v})")
                f[col] = s
            elif col == "base":
                s = str(v).strip().lower()
                f[col] = "equipo" if s in ("", "equipo", "nan") else str(int(float(s))) if s.replace(".", "").isdigit() else "equipo"
            else:
                f[col] = str(v).strip()
        if "inicio" in f and "fin" in f and f["fin"] < f["inicio"]:
            err.append("'Fin' es anterior a 'Inicio'")
        if err:
            if len(errores) < 50:
                errores.append(f"Fila {n}: " + "; ".join(err))
        else:
            filas.append(f)
    return filas, errores


def aplicar(tipo: str, filas: list[dict], modo: str, cfg: dict, actual_dataset: list | None) -> tuple[dict, list | None]:
    """Incorpora las filas a la configuración o al dataset. modo: 'reemplazar' o 'agregar'."""
    if tipo.startswith("proyectos_"):
        t = "Transformacional" if tipo == "proyectos_transformacionales" else "Tecnologia"
        nuevos = [{"incluir": True, "base": "equipo", **f, "tipo": t} for f in filas]
        otros = [p for p in cfg.get("proyectos", []) if p.get("tipo") != t]
        mismos = [p for p in cfg.get("proyectos", []) if p.get("tipo") == t]
        if modo == "agregar":
            cod = {f["codigo"] for f in nuevos}
            mismos = [p for p in mismos if p.get("codigo") not in cod] + nuevos
        else:
            mismos = nuevos
        cfg["proyectos"] = mismos + otros
        return cfg, None
    if tipo in ("equipo", "vacaciones", "feriados"):
        extra = {"incluir": True} if tipo in ("equipo", "feriados") else {}
        nuevos = [{**extra, **f} for f in filas]
        if tipo == "equipo":
            nuevos = [{"genero": "Hombre", "cargo": "", **f} for f in nuevos]
        cfg[tipo] = (cfg.get(tipo, []) + nuevos) if modo == "agregar" else nuevos
        return cfg, None
    # datasets
    base = actual_dataset or []
    if tipo.startswith("historico_"):
        pref = "REQ" if tipo == "historico_requerimientos" else "INC"
        filas = [{"id": f"{pref}-{i:05d}", **f} for i, f in enumerate(filas, len(base) + 1)]
    return cfg, (base + filas) if modo == "agregar" else filas
