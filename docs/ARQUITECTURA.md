# Arquitectura y estado actual

Este documento describe **cómo funciona el sistema tal como está en esta rama
(`feat/demo-fluidez`)**, para que cualquiera que retome el proyecto — humano o
agente — entienda el estado real sin tener que releer todo el código. La rama
`main` es la versión anterior, sin las optimizaciones de fluidez ni las
correcciones de bugs descritas aquí.

## Qué es esto

Un agente de voz en español que atiende pedidos de una cadena de restaurantes
de pollo, construido sobre [LiveKit Agents](https://docs.livekit.io/agents/).
El objetivo del proyecto en su fase actual es **una demo comercial**: lo que
se evalúa es que la conversación se sienta fluida y natural, no la cantidad de
funciones ni la sofisticación del backend.

## Flujo de una llamada

```
Cliente (voz)
    │
    ▼
LiveKit Inference
    STT:  deepgram/flux-general-multi   (transcribe + detecta fin de turno, <400ms)
    LLM:  openai/gpt-4.1-mini            (configurable via LLM_MODEL)
    TTS:  deepgram/aura-2 "celeste" es-CO (fija, no configurable)
    │
    ▼
agent.py: Assistant
    instructions = prompt + menú (inyectado una vez al iniciar la sesión)
    tools:
      - search_products      (tools/products.py)
      - add_item_to_order    (tools/orders.py)
      - set_item_quantity    (tools/orders.py)
      - vaciar_pedido        (tools/orders.py)
      - confirm_order        (tools/orders.py)
    │
    ▼
session.userdata: PedidoEnCurso   (estado de ESTA llamada, no global)
    │
    ▼
Postgres (database/connection.py — pool singleton)
    products / orders / order_items
```

### Por qué el menú ya no es una tool

Antes existía una tool `get_menu` que el LLM tenía que invocar y esperar
(roundtrip completo) para responder algo tan simple como "¿qué bebidas
tienen?". Como el menú (4 productos) no cambia durante una llamada, ahora se
consulta **una sola vez** al arrancar la sesión (`build_menu_prompt_block()`
en `tools/products.py`) y se inyecta directo en las `instructions` del
`Assistant`. El agente "ya sabe" el menú desde el primer turno.

`search_products` se conserva porque cumple un rol distinto: confirmar el
`id` exacto de un producto cuando el cliente lo describe de forma ambigua o
con errores de transcripción ("polo asado" → Pollo Asado), usando `pg_trgm` +
una columna de sinónimos (`keywords`).

### Por qué la detección de turno cambió

`deepgram/flux-general-multi` es un modelo de STT que además decide cuándo el
cliente terminó de hablar, en el mismo stream (`turn_handling.turn_detection
= "stt"`). Antes esto eran dos componentes separados (`nova-2` +
`MultilingualModel`), lo que sumaba latencia y además `MultilingualModel`
salía deprecado en la versión de `livekit-agents` instalada (1.6.10).

Hay un camino de respaldo si Flux no convence en español-CO: cambiar
`STT_MODEL=deepgram/nova-2` en `.env` vuelve automáticamente al detector de
turno separado (ver `agent.py:_build_turn_pipeline`).

### Por qué la voz no aparece como configurable

Decisión explícita del dueño del producto: `aura-2` / `celeste` / `es-CO` es
la voz aprobada para la demo y no se toca bajo ningún motivo. Por eso está
fija en `agent.py`, no en `.env` — para que nadie la cambie por accidente
ajustando una variable de entorno.

### Ruido de sala de fondo

`agent.py:entrypoint` publica una **segunda pista de audio**, independiente
de la voz del agente, con `livekit.agents.BackgroundAudioPlayer`: reproduce
`assets/restaurant_ambience.wav` en loop a volumen muy bajo (`AMBIENCE_VOLUME
= 0.04`) durante toda la llamada. No es un hack casero leyendo el `.wav` a
mano — `BackgroundAudioPlayer` ya trae su propio `AudioSource`/
`LocalAudioTrack`, decodifica y resamplea el archivo (soporta cualquier
sample rate/canales de entrada), y se cierra solo con `ctx.add_shutdown_callback`
cuando la llamada termina, incluso si termina de forma abrupta. Es puramente
ambiental: no reacciona al estado del agente (a diferencia de
`thinking_sound`, que este proyecto no usa).

## Estado del pedido: `session.userdata`, no un global

`tools/orders.py` define:

```python
@dataclass
class PedidoEnCurso:
    items: list[ItemPedido]
    customer_phone: str | None
    customer_name: str | None
    delivery_address: str | None
```

Cada `AgentSession` tiene su propia instancia (`AgentSession[PedidoEnCurso](userdata=PedidoEnCurso(), ...)`).
Esto importa por dos razones:

1. **Aislamiento entre llamadas.** La versión anterior guardaba el pedido en
   una lista de módulo (`order = []`). Dos llamadas concurrentes en el mismo
   proceso worker se habrían mezclado. Con `userdata`, cada llamada tiene su
   propio estado, sin excepción.
2. **Es requisito para telefonía.** Un worker de LiveKit atiende múltiples
   llamadas (jobs) en el mismo proceso; sin este cambio, telefonía real
   habría sido inviable sin reescribir esto de todas formas.

## Las cinco tools

| Tool | Qué hace | Validaciones |
|---|---|---|
| `search_products(query)` | Busca un producto puntual por nombre/sinónimo/texto con errores de transcripción | — (solo lectura) |
| `add_item_to_order(product_id, quantity)` | Agrega un producto al pedido en curso | Producto existe; hay stock suficiente (sumando lo que ya llevaba pedido) |
| `set_item_quantity(product_id, quantity)` | Corrige la cantidad de un producto ya agregado; `quantity=0` lo quita | Igual que `add_item_to_order`, salvo cuando `quantity=0` |
| `vaciar_pedido()` | Borra todo el pedido en curso sin tocar la base de datos | — |
| `confirm_order(customer_name, delivery_address)` | Persiste el pedido: crea la fila en `orders`, una fila por item en `order_items`, descuenta stock, e informa un tiempo de entrega estimado (`eta_minutos`) | `customer_name` y `delivery_address` no pueden llegar vacíos — es la tool, no el prompt, la que obliga a que el agente los haya preguntado antes. Revalida stock con `SELECT ... FOR UPDATE` dentro de la transacción (protege contra una carrera con otra llamada concurrente) |

Todas menos `search_products` reciben `ctx: RunContext[PedidoEnCurso]` como
primer parámetro (LiveKit Agents lo inyecta automáticamente por tipo, no por
nombre) y operan sobre `ctx.userdata`.

### Manejo de errores: `ToolError`, no excepciones genéricas

Si una tool deja escapar una excepción común (`ValueError`, etc.), LiveKit
Agents la convierte en el mensaje `"An internal error occurred"` — en inglés,
en medio de una llamada en español. Por eso el único camino de error
inesperado que puede ocurrir (una carrera de stock detectada dentro de la
transacción de `confirm_order`) usa `raise ToolError("mensaje en español")`
explícitamente: ese mensaje sí llega intacto al LLM. Los errores esperables
(producto inexistente, stock insuficiente al agregar) no son excepciones:
son un `{"success": False, "message": "..."}` normal, que el LLM ve como
cualquier otro resultado de tool.

## Modelo de datos

```sql
products (id, name, description, price, stock, category, keywords[])
orders   (id, status, customer_phone, customer_name, delivery_address, total, created_at)
order_items (id, order_id, product_id, quantity, unit_price)
```

- `unit_price` en `order_items` congela el precio al momento del pedido: un
  cambio de precio futuro no altera pedidos ya confirmados.
- `total` en `orders` se calcula en Python a partir de `PedidoEnCurso.total`
  (suma de `unit_price * quantity`) y se guarda ya resuelto — el LLM nunca
  tiene que sumar pesos colombianos de cabeza.
- `customer_phone` queda `NULL` en console/playground. Se llena solo si la
  llamada entra por SIP (ver siguiente sección).
- `customer_name` y `delivery_address` son `NOT NULL`: `confirm_order` los
  exige como parámetros obligatorios, así que un pedido confirmado siempre
  los tiene. El agente debe preguntarlos explícitamente antes de confirmar
  (ver el prompt en `agent.py`); no se asumen ni se infieren.
- El tiempo de entrega que se le informa al cliente (`ETA_MINUTOS = 30` en
  `tools/orders.py`) es un valor fijo de la demo, no un cálculo real de
  logística/reparto.
- Búsqueda difusa vía extensión `pg_trgm` + índices GIN trigram sobre `name`
  y `description`, más coincidencia por substring sobre `keywords`.

## Telefonía: preparado, no conectado

El código ya captura el número del cliente cuando existe un participante SIP
en la sala (`agent.py:_capturar_telefono_sip`), de forma no bloqueante (no
cuelga si no hay ningún participante SIP, como en console/playground). Pero
**no hay ningún trunk SIP configurado**: hoy el agente solo se puede probar
por `console` o por `dev` + un cliente WebRTC (playground). Conectar un
número real de Colombia (troncal con Claro/Movistar/Tigo, o portabilidad a un
proveedor SIP) es trabajo pendiente, discutido pero no iniciado — ver
`docs/SPEC.md` § Fuera de alcance.

El agente tiene nombre formal: `@server.rtc_session(agent_name="agente-pollo")`
en `agent.py`. Es el nombre que un dispatch rule de SIP necesitaría para
enrutar una llamada real específicamente a este agente (paso previo útil para
cuando se conecte la telefonía). **Efecto secundario importante:** fijar
`agent_name` activa "explicit dispatch" en `livekit-agents` — las salas ya no
disparan el agente automáticamente. `console` no se ve afectado (simula el
job localmente), pero para probar por `dev` + Playground hay que indicar
`agente-pollo` como agent name al conectarse, o el agente simplemente no
entra a la sala.

## Variables de entorno

| Variable | Default | Notas |
|---|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | — | Credenciales de LiveKit Cloud |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | `localhost` / `5433` / `admin` / `password` / `chicken_store` | `5433` para no chocar con otra Postgres local en `5432` |
| `LLM_MODEL` | `openai/gpt-4.1-mini` | Ver `scripts/bench_llm.py` para comparar candidatos |
| `STT_MODEL` | `deepgram/flux-general-multi` | Cambiar a `deepgram/nova-2` vuelve al camino de respaldo |
| `AVISO_LEGAL` | `false` | Agrega al saludo el aviso de asistente virtual/grabación (Ley 1581 de 2012). Pensado para telefonía real, apagado en demo |

No existe una variable para el TTS: es intencional.

## Observabilidad: métricas por turno

Cada turno queda logueado con el prefijo `[latencia]`:

```
INFO:agent:[latencia] agente   e2e=612ms  llm_ttft=340ms  tts_ttfb=180ms
```

`e2e_latency` es tiempo entre que el cliente dejó de hablar y el agente
empezó a responder — el número que importa para juzgar fluidez. Se registra
suscribiéndose al evento `conversation_item_added` de `AgentSession`
(`agent.py:_registrar_metricas_de_turno`).

## Verificado hasta ahora

- Suite de pruebas contra Postgres real (fuera de la voz): confirma que los
  tres bugs de la versión anterior quedaron corregidos (producto inválido,
  stock insuficiente, imposibilidad de corregir el pedido), y que el total y
  el descuento de stock son correctos.
- `scripts/bench_llm.py` corrido de verdad: con el turno de prueba usado,
  `gemini-2.5-flash-lite` tuvo menor TTFT que `gpt-4.1-mini` (el default
  actual). Queda como dato para decidir, no se cambió el default.
- Una corrida completa de `agent.py console` construye el pipeline entero
  (Flux STT, `TurnHandlingOptions`, interrupciones adaptativas) y reproduce
  el saludo end-to-end con TTS real.

## No verificado / pendiente de tu parte

- Calidad subjetiva de Flux transcribiendo español colombiano hablado (solo
  se probó con la corrida automática, sin micrófono real).
- Todo lo relacionado a telefonía real (trunk SIP, número colombiano, portal
  de pedidos) — ver `docs/SPEC.md`.
