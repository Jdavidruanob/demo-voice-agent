# Imagen del servicio web: sirve la pagina (web/static) y emite tokens de
# LiveKit. Liviano a proposito (FastAPI + uvicorn, sin build de frontend):
# es el segundo servicio de Railway, separado del worker del agente.
#
# Se construye con "Root Directory" = web en Railway, asi que el contexto
# de build es esta misma carpeta (main.py, requirements.txt, static/).
FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY static ./static

# Railway inyecta PORT en tiempo de ejecucion; shell form para que se expanda.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
