# Demo — Agente de voz para tomar pedidos

Agente de voz construido con [LiveKit Agents](https://docs.livekit.io/agents/) que atiende
llamadas de una cadena de restaurantes de pollo: escucha al cliente en español, consulta el
menú en Postgres, arma el pedido y lo guarda cuando el cliente lo confirma.

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
                          search_products
                          add_item_to_order
                          set_item_quantity
                          vaciar_pedido
                          confirm_order
```

Detalles de la conversación, pensados para que fluya como una llamada real y no
como un intercambio de turnos con pausas:

- **Detección de turno vía Flux**: el propio STT decide cuándo el cliente terminó de
  hablar (`turn_handling.turn_detection = "stt"`), sin un modelo aparte ni el roundtrip
  extra que eso implicaba. Camino de respaldo con `nova-2` + `MultilingualModel` disponible
  vía `STT_MODEL` si Flux no convence en español-CO (ver `agent.py:_build_turn_pipeline`).
- **`preemptive_tts` activo**: la síntesis de voz arranca antes de que el turno se
  confirme del todo, escondiendo buena parte de la latencia de la voz sin tocarla.
- **Saludo instantáneo** con `session.say()` (sin pasar por el LLM) y **menú inyectado en
  el prompt** al arrancar la sesión: la lista de productos ya no es una tool que el agente
  tenga que invocar y esperar a mitad de llamada.
- **Fillers**: si `search_products` tarda más de 0.6s, el agente dice "dame un momento,
  reviso..." en vez de dejar un silencio muerto.
- **VAD** con Silero y **cancelación de ruido** BVC, pensado para audio de teléfono.
- **Búsqueda tolerante a errores**: `search_products` usa la extensión `pg_trgm` de
  Postgres más una columna de `keywords`, así que entiende sinónimos ("gaseosa" →
  Coca-Cola) y transcripciones imperfectas ("polo asado" → Pollo Asado).
- **Métricas por turno**: cada turno loguea `[latencia]` con el end-to-end real
  (ver la sección de Latencia más abajo).

## Estructura

```
agent.py                 Prompt del agente, sesión, pipeline de voz y métricas
database/connection.py   Pool de conexiones a Postgres (singleton)
database/schema.sql      Tablas, índices trigram y productos de ejemplo
tools/products.py        search_products, build_menu_prompt_block
tools/orders.py          PedidoEnCurso + add_item_to_order, set_item_quantity,
                         vaciar_pedido, confirm_order
scripts/bench_llm.py     Compara TTFT entre LLM candidatos con datos reales
docker-compose.yml       Postgres 17 para desarrollo local
```

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
con los valores que levanta `docker-compose.yml`. `LLM_MODEL` y `STT_MODEL` son opcionales
(traen default); la voz (TTS) no es configurable por env a propósito, ver más abajo.

**3. Levantar Postgres y cargar el esquema**

```bash
docker compose up -d
docker compose exec -T postgres psql -U admin -d chicken_store < database/schema.sql
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
[Agents Playground](https://agents-playground.livekit.io) — usando el mismo proyecto.

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

Mide el time-to-first-token de varios candidatos con un turno representativo de toma de
pedidos (sin voz, solo el LLM).

## Notas

- `.env` está en `.gitignore` a propósito: nunca subas tus claves al repositorio.
- El pedido en curso vive en `session.userdata` (`PedidoEnCurso`, en `tools/orders.py`),
  no en un global de módulo: cada llamada tiene su propio estado, así que dos llamadas
  simultáneas en el mismo proceso nunca se mezclan.
- Las credenciales del `docker-compose.yml` (`admin` / `password`) son solo para
  desarrollo local.
- Si cambiaste de rama y la base ya tenía datos de antes, recarga el esquema (las tablas
  `orders`/`order_items` tienen columnas nuevas: `total`, `customer_phone`, `unit_price`):
  ```bash
  docker compose exec -T postgres psql -U admin -d chicken_store \
    -c "DROP TABLE IF EXISTS order_items, orders, products CASCADE;"
  docker compose exec -T postgres psql -U admin -d chicken_store < database/schema.sql
  ```
