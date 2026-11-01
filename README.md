# Demanda TI NGR

Planificación de la demanda y capacidad del equipo de data de NGR (piloto 2027).

Stack: Python + FastAPI, PostgreSQL y frontend HTML/JS, desplegado en Render.

## Etapa 1: motor estadístico
- `data/`: insumos editables (equipo, feriados, vacaciones, proyectos, parámetros, histórico ficticio).
- `motor/modelo.py`: capacidad neta, carga de proyectos, pronóstico (Holt-Winters + Kalman), Monte Carlo Poisson, Erlang C y brecha con alerta anticipada.
- `app.py`: API FastAPI (`/`, `/api/resumen`, `/api/pronostico`, `/api/capacidad`).

Ejecutar local: `pip install -r requirements.txt && uvicorn app:app --reload`
