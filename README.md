# Demo — Agente de voz para tomar pedidos (Brasa & Pan)

Agente de voz construido con [LiveKit Agents](https://docs.livekit.io/agents/) que atiende
llamadas de **Brasa & Pan**: escucha al cliente en español, arma el pedido y, cuando el
cliente lo confirma, la comanda aparece en el portal del restaurante
(`demo-delivery-system`) igual que una que entró por el menú web.

> **Esta rama no tiene base de datos propia.** El catálogo y los pedidos viven en el
> sistema de pedidos y se consultan por HTTP (`brasa/api.py`). La rama `pedidos` es la
> versión anterior, con su propia Postgres de 4 productos de pollo.

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
Assistant (agent.py) ── function tools ──▶ brasa/api.py ──HTTP──▶ Sistema de pedidos
                          search_products                            GET  /api/internal/catalog
                          add_item_to_order                          POST /api/internal/voice-order
                          set_item_quantity
                          vaciar_pedido                         ──▶ Portal: comanda en "Nuevos" 📞
                          confirm_order
                          finalizar_llamada
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
- **Búsqueda tolerante a errores**: `search_products` busca en el catálogo que ya está
  en memoria (coincidencia literal primero, `difflib` después), así que tolera
  transcripciones imperfectas sin un viaje a la base.
- **El agente nunca calcula un precio**: manda SKUs y cantidades; el total lo resuelve el
  servidor. Si una promoción se vence a mitad de llamada, el número que se dice en voz
  alta y el que queda en la comanda son el mismo.
- **Tres respaldos, todos terminando en WhatsApp**: antes de llamar, al arrancar la
  llamada y al confirmar. Si el pedido no se guardó, al cliente se le dice eso — nunca
  "ya quedó" (ver `docs/ARQUITECTURA.md` § Cuando algo falla).
- **Métricas por turno**: cada turno loguea `[latencia]` con el end-to-end real
  (ver la sección de Latencia más abajo).

## Estructura

```
agent.py                 Prompt del agente, sesión, pipeline de voz y métricas
brasa/api.py             Cliente HTTP del sistema de pedidos (catálogo y confirmación)
brasa/catalogo.py        El catálogo en memoria + el bloque de menú del prompt
tools/products.py        search_products
tools/orders.py          PedidoEnCurso + add_item_to_order, set_item_quantity,
                         vaciar_pedido, confirm_order
scripts/verify_contrato.py  Lo que se rompe en silencio (el AGENT_NAME, sobre todo)
tools/call.py            finalizar_llamada (cierra la llamada tras la despedida)
scripts/bench_llm.py     Compara TTFT entre LLM candidatos con datos reales
docker-compose.yml       Postgres 17 para desarrollo local
Dockerfile               Imagen del worker del agente (para Railway u otro host)
web/                     Interfaz web para hablar con el agente sin teléfono
                         (main.py: FastAPI + token de LiveKit; static/: la página)
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
tu proyecto en LiveKit Cloud (Settings → Keys). `LLM_MODEL` y `STT_MODEL` son opcionales
(traen default); la voz (TTS) no es configurable por env a propósito, ver más abajo.

**3. Apuntar al sistema de pedidos**

En el mismo `.env`:

```
DELIVERY_API_URL=https://<la-app-del-menu>.vercel.app
INTERNAL_SECRET=<el MISMO valor que tienen las apps del sistema de pedidos>
BUSINESS_WHATSAPP_NUMBER=57...
```

`INTERNAL_SECRET` tiene que coincidir exactamente, o el catálogo responde 401 y la llamada
no arranca (el agente lo dice y manda al WhatsApp, no se queda callado). Para trabajar
contra el sistema corriendo en local, `DELIVERY_API_URL=http://localhost:3002`.

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
Como el agente usa despacho explícito (`AGENT_NAME = "agente-brasa"`), indica
ese nombre como agent name al conectarte desde el Playground, o el agente no
entrará a la sala (ver `docs/ARQUITECTURA.md` § Telefonía).

**Antes de cualquier commit**, vale la pena correr:

```bash
uv run python scripts/verify_contrato.py
```

Comprueba las cosas que no dan error pero rompen la demo — sobre todo que el `AGENT_NAME`
de `agent.py` y el de `web/main.py` sigan siendo el mismo. No necesita red ni credenciales.

## Interfaz web (sin teléfono)

`web/` es una página mínima + un backend FastAPI que emite tokens de LiveKit,
para hablar con el agente desde el navegador sin necesidad de número de
teléfono ni del Playground. Para probarla en local, con el agente corriendo
en modo `dev` en otra terminal:

```bash
uv run --with fastapi --with "uvicorn[standard]" --with livekit-api --with python-dotenv \
  uvicorn web.main:app --reload --port 8000
```

Abre `http://localhost:8000`, toca el botón de llamar y permite el micrófono.

En producción las dos piezas van en sitios distintos: **el worker del agente en
Railway** (no puede ir a Vercel: se queda esperando despachos por WebSocket, no
lo dispara un request) y **la página en Vercel**, donde sale gratis. Los pasos
están en [`docs/DEPLOY_RAILWAY.md`](docs/DEPLOY_RAILWAY.md).

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
- El WhatsApp de confirmación que recibe el cliente después de la llamada **solo llega si
  ese número ya le había escrito al bot** (la ventana de 24 h de Meta). Si no, el pedido
  queda igual de completo en el portal. No es una falla: es cómo funciona WhatsApp, y
  conviene decirlo antes de mostrar la demo.
- `AGENT_NAME` vive en dos archivos (`agent.py` y `web/main.py`) y tienen que ser el mismo
  valor. Si no coinciden, la sala queda vacía **sin ningún error**: el navegador se queda
  en "Conectando…" hasta que vence el plazo de 12 s. Es la trampa número uno de este repo.
