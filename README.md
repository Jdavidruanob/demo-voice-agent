# Demo — Agente de voz para reservas de hotel

Agente de voz construido con [LiveKit Agents](https://docs.livekit.io/agents/) que atiende
llamadas de recepción de un hotel: escucha al cliente en español, consulta disponibilidad y
tarifas en Postgres, y guarda la reserva cuando el cliente confirma.

> **Documentación completa:** [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) describe el
> estado actual del sistema y por qué está construido así; [`docs/SPEC.md`](docs/SPEC.md)
> es el spec del producto — qué debe cumplir el sistema, contratos de cada tool y
> criterios de aceptación. Este README es la puesta en marcha rápida.

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
tools/formato.py           Pluralización en español para los textos que arman las tools
scripts/bench_llm.py       Compara TTFT entre LLM candidatos con datos reales
docker-compose.yml         Postgres 17 para desarrollo local
web/                       Cliente web de prueba (Next.js) para llamar al agente por navegador
```

## Cliente web de prueba

`web/` es una app Next.js aparte (App Router + Tailwind) para hacer llamadas de prueba al
agente desde el navegador, sin depender de Agents Playground. Usa el mismo proyecto de
LiveKit que el agente (mismas tres credenciales, ver `web/.env.example`).

```bash
cd web
npm install
cp .env.example .env.local   # completa LIVEKIT_URL / API_KEY / API_SECRET
npm run dev
```

Abre `http://localhost:3000`, dale a "Iniciar llamada" y acepta el permiso de micrófono.
`app/api/token/route.ts` firma el access token en el servidor y agrega el dispatch
explícito hacia `agent_name="agente-hotel-reservas"` (ver `docs/ARQUITECTURA.md` §
Telefonía) — sin eso, la sala se crea pero el agente nunca entra.

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
indicando `agente-hotel-reservas` como agent name (ver `docs/ARQUITECTURA.md` §
Telefonía), o usa el cliente web de prueba de arriba, que ya lo hace por ti.

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
