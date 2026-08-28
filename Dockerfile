# Imagen del worker del agente de voz (agent.py). Se conecta HACIA AFUERA a
# LiveKit Cloud (WebSocket saliente): no necesita puerto publico ni recibir
# trafico entrante, por eso no hay EXPOSE ni servidor HTTP aqui.
FROM python:3.14-slim

WORKDIR /app

# uv instalado como binario estatico (sin pip ni red aparte): resuelve e
# instala las dependencias exactamente como quedaron fijadas en uv.lock.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY agent.py ./
COPY tools ./tools
COPY database ./database
COPY assets ./assets

# Precarga en el build (no en el primer job real) los modelos que el agente
# necesita en frio: VAD de Silero y, si STT_MODEL cae al camino de respaldo,
# el turn-detector multilingue.
RUN uv run python -m livekit.agents download-files

# "start" es el modo produccion: un subproceso por llamada
# (JobExecutorType.PROCESS), reconecta solo si se cae, sin la UI de consola.
CMD ["uv", "run", "python", "agent.py", "start"]
