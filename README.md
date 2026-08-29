# Demo — Agente de voz para reservas de hotel

Agente de voz construido con [LiveKit Agents](https://docs.livekit.io/agents/) que atiende
llamadas de recepción de un hotel: escucha al cliente en español, consulta disponibilidad y
tarifas en Postgres, y guarda la reserva cuando el cliente confirma.

> **Documentación completa:** [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) describe el
> estado actual del sistema y por qué está construido así; [`docs/SPEC.md`](docs/SPEC.md)
> es el spec del producto — qué debe cumplir el sistema, contratos de cada tool y
> criterios de aceptación; [`docs/DEPLOY_RAILWAY.md`](docs/DEPLOY_RAILWAY.md) es la guía
> para ponerlo en línea. Este README es la puesta en marcha rápida.

> **Ramas del repo:** `main` es el código original; `pedidos` es la demo de toma de
> pedidos de un restaurante de pollo; `reservas` (esta rama) es la demo de reservas de
> hotel. `pedidos` y `reservas` comparten arquitectura, interfaz web y todas las
> optimizaciones de fluidez: solo cambia el dominio de negocio y el color de la
> interfaz.

## Cómo funciona

```
Cliente (voz)
    │
    ▼
LiveKit Inference ──  STT: deepgram/flux-general-multi (transcribe Y detecta turno, <400ms)
                      LLM: openai/gpt-4.1-mini (configurable, ver LLM_MODEL)
                      TTS: deepgram/aura-2 (voz "celeste", es-CO — fija, no se cambia)
    │
    ▼
Assistant (agent.py) ── function tools ──▶ Postgres
                          consultar_disponibilidad
                          crear_reserva
                          finalizar_llamada  (dice la despedida y cuelga)
```

Detalles de la conversación, pensados para que fluya como una llamada real y no
como un intercambio de turnos con pausas:

- **Detección de turno vía Flux**: el propio STT decide cuándo el cliente terminó de
  hablar (`turn_handling.turn_detection = "stt"`), sin un modelo aparte ni el roundtrip
  extra que eso implicaba. Camino de respaldo con `nova-2` + `MultilingualModel` disponible
  vía `STT_MODEL` si Flux no convence en español-CO (ver `agent.py:_build_turn_pipeline`).
- **`preemptive_tts` activo**: la síntesis de voz arranca antes de que el turno se
  confirme del todo, escondiendo buena parte de la latencia de la voz sin tocarla.
- **Saludo instantáneo** con `session.say()` (sin pasar por el LLM) y **catálogo de tipos
  de habitación inyectado en el prompt** al arrancar la sesión: sirve para responder
  precio/capacidad sin gastar un roundtrip de tool. La disponibilidad real para fechas
  concretas siempre pasa por `consultar_disponibilidad`.
- **Fillers**: si una consulta a Postgres tarda más de 0.6s, el agente dice "a ver, reviso
  disponibilidad..." en vez de dejar un silencio muerto.
- **VAD** con Silero y **cancelación de ruido** BVC, pensado para audio de teléfono.
- **Cierre de llamada**: cuando la reserva ya quedó guardada y el cliente dice que no
  necesita nada más, `finalizar_llamada` dice la despedida, espera a que se oiga completa
  y cierra la sala — la interfaz web vuelve sola al estado inicial.
- **Fecha actual en el prompt**: el agente sabe qué día es hoy, así que resuelve "mañana"
  o "el próximo viernes" a una fecha real en vez de inventarla.
- **Métricas por turno**: cada turno loguea `[latencia]` con el end-to-end real
  (ver la sección de Latencia más abajo).

## Estructura

```
agent.py                   Prompt del agente, sesión, pipeline de voz y métricas
database/connection.py     Pool de conexiones a Postgres (singleton)
database/schema.sql        Tablas (habitaciones, reservas) y tipos de habitación de ejemplo
tools/habitaciones.py      consultar_disponibilidad, build_catalogo_prompt_block,
                           build_servicios_prompt_block
tools/reservas.py          ReservaEnCurso + crear_reserva
tools/call.py              finalizar_llamada (despedida + cierre de la sala)
tools/formato.py           Pluralización en español para los textos que arman las tools
scripts/bench_llm.py       Compara TTFT entre LLM candidatos con datos reales
docker-compose.yml         Postgres 17 para desarrollo local
Dockerfile                 Imagen del worker del agente (para Railway)
web/main.py                Backend mínimo (FastAPI): sirve la página y emite tokens
web/static/index.html      La interfaz de llamada (una sola página, sin build)
web/Dockerfile             Imagen del servicio web (para Railway)
```

## Interfaz web

`web/` es un servicio aparte y deliberadamente mínimo: FastAPI sirviendo una sola página
estática (`web/static/index.html`, sin build ni framework) y un endpoint `/api/token`.
Sirve para llamar al agente desde el navegador sin depender del Agents Playground, y es lo
que se despliega en Railway para poder mandarle un link a alguien.

```bash
uv run uvicorn main:app --reload --app-dir web
```

Abre `http://localhost:8000`, toca el círculo y acepta el permiso de micrófono.
`web/main.py` firma el access token en el servidor y le agrega el dispatch explícito hacia
`agent_name="agente-reservas"` (ver `docs/ARQUITECTURA.md` § Telefonía) — sin eso, la sala
se crea pero el agente nunca entra. El agente tiene que estar corriendo aparte
(`uv run agent.py dev`).

La interfaz es la misma de la demo de pedidos, en azul y etiquetada "Reservas", para que
las dos demos se vean como parte de un mismo producto y no se confundan al compartir los
links.

## Requisitos

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- Docker (para la base de datos local)
- Una cuenta de [LiveKit Cloud](https://cloud.livekit.io)

## Puesta en marcha

**1. Instalar dependencias**

```bash
uv sync
```

**2. Configurar las variables de entorno**

```bash
cp .env.example .env
```

Completa `LIVEKIT_URL`, `LIVEKIT_API_KEY` y `LIVEKIT_API_SECRET` con las credenciales de
tu proyecto en LiveKit Cloud (Settings → Keys). Las variables de base de datos ya vienen
con los valores que levanta `docker-compose.yml`. `RECEPTIONIST_NAME` y `HOTEL_NAME`
cambian el nombre que dice el agente en el saludo y en el prompt. `LLM_MODEL` y
`STT_MODEL` son opcionales (traen default); la voz (TTS) no es configurable por env a
propósito, ver más abajo.

**3. Levantar Postgres y cargar el esquema**

```bash
docker compose up -d
docker compose exec -T postgres psql -U admin -d hotel_reservas < database/schema.sql
```

**4. Descargar los modelos locales** (VAD y detección de turno, solo la primera vez)

```bash
uv run agent.py download-files
```

**5. Ejecutar el agente**

```bash
# Conversación por terminal, sin necesidad de un frontend
uv run agent.py console

# Modo desarrollo: se conecta a tu proyecto de LiveKit y recarga al guardar
uv run agent.py dev
```

Con `dev`, conéctate desde cualquier cliente de LiveKit — por ejemplo el
[Agents Playground](https://agents-playground.livekit.io) — usando el mismo proyecto e
indicando `agente-reservas` como agent name (ver `docs/ARQUITECTURA.md` § Telefonía), o
usa la interfaz web de arriba, que ya lo hace por ti.

## Latencia

Cada turno queda logueado con el prefijo `[latencia]`:

```
INFO:agent:[latencia] agente   e2e=612ms  llm_ttft=340ms  tts_ttfb=180ms
```

`e2e` es el número que importa: tiempo entre que el cliente dejó de hablar y el agente
empezó a responder. Sirve para comparar objetivamente esta rama contra `main` con el mismo
guion de prueba, en vez de fiarse de la sensación de "se sintió más fluido".

Para elegir `LLM_MODEL` con datos en vez de teoría:

```bash
uv run python scripts/bench_llm.py
```

Mide el time-to-first-token de varios candidatos con un turno representativo de reserva
de hotel (sin voz, solo el LLM).

## Notas

- `.env` está en `.gitignore` a propósito: nunca subas tus claves al repositorio.
- La reserva en curso vive en `session.userdata` (`ReservaEnCurso`, en `tools/reservas.py`),
  no en un global de módulo: cada llamada tiene su propio estado, así que dos llamadas
  simultáneas en el mismo proceso nunca se mezclan.
- Las credenciales del `docker-compose.yml` (`admin` / `password`) son solo para
  desarrollo local.
- Si tenías la base de datos cargada con el esquema anterior (restaurante), bórrala antes
  de cargar el nuevo esquema — las tablas cambiaron de nombre y de forma:
  ```bash
  docker compose exec -T postgres psql -U admin -d hotel_reservas \
    -c "DROP TABLE IF EXISTS order_items, orders, products, reservas, habitaciones CASCADE;"
  docker compose exec -T postgres psql -U admin -d hotel_reservas < database/schema.sql
  ```
