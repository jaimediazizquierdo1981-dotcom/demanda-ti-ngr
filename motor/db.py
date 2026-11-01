"""Almacenamiento compartido en PostgreSQL (si existe DATABASE_URL).

Se guarda todo como JSON:
  - config: la configuración compartida (parámetros + equipo, proyectos, vacaciones, feriados, terceros)
  - datasets: tablas cargadas (histórico de requerimientos e incidencias, horas reales)
Si no hay base de datos, la app sigue funcionando con los archivos de /data.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager

URL = os.environ.get("DATABASE_URL", "").strip()


def disponible() -> bool:
    return bool(URL)


@contextmanager
def _conn():
    import psycopg
    with psycopg.connect(URL, autocommit=True) as c:
        yield c


def iniciar() -> None:
    if not disponible():
        return
    with _conn() as c:
        c.execute("""create table if not exists config (
            id int primary key, data jsonb not null, actualizado timestamptz default now(), usuario text)""")
        c.execute("""create table if not exists datasets (
            nombre text primary key, filas jsonb not null, actualizado timestamptz default now(), usuario text)""")


def leer_config() -> dict | None:
    if not disponible():
        return None
    with _conn() as c:
        r = c.execute("select data from config where id = 1").fetchone()
    return r[0] if r else None


def guardar_config(data: dict, usuario: str = "") -> None:
    with _conn() as c:
        c.execute("""insert into config (id, data, actualizado, usuario) values (1, %s, now(), %s)
                     on conflict (id) do update set data = excluded.data, actualizado = now(), usuario = excluded.usuario""",
                  (json.dumps(data, ensure_ascii=False, default=str), usuario))


def leer_dataset(nombre: str) -> list | None:
    if not disponible():
        return None
    with _conn() as c:
        r = c.execute("select filas from datasets where nombre = %s", (nombre,)).fetchone()
    return r[0] if r else None


def guardar_dataset(nombre: str, filas: list, usuario: str = "") -> None:
    with _conn() as c:
        c.execute("""insert into datasets (nombre, filas, actualizado, usuario) values (%s, %s, now(), %s)
                     on conflict (nombre) do update set filas = excluded.filas, actualizado = now(), usuario = excluded.usuario""",
                  (nombre, json.dumps(filas, ensure_ascii=False, default=str), usuario))


def estado() -> dict:
    """Qué hay guardado y cuándo se actualizó (para mostrarlo en Parámetros)."""
    if not disponible():
        return {"base_de_datos": False}
    out = {"base_de_datos": True, "config": None, "datasets": {}}
    with _conn() as c:
        r = c.execute("select actualizado from config where id = 1").fetchone()
        out["config"] = r[0].isoformat() if r else None
        for nombre, act, n in c.execute("select nombre, actualizado, jsonb_array_length(filas) from datasets"):
            out["datasets"][nombre] = {"actualizado": act.isoformat(), "filas": n}
    return out
