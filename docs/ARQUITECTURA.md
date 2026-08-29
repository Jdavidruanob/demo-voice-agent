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
      - finalizar_llamada    (tools/call.py)
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

### Reconocimiento de apellidos poco comunes

Al STT (Deepgram, en ambos caminos de `_build_turn_pipeline`) se le pasa
`extra_kwargs={"keyterm": APELLIDOS_A_RECONOCER}` con una lista corta de
apellidos colombianos que un modelo genérico tiende a transcribir mal
("Ruano", "Burbano", etc.) — Deepgram permite sesgar la transcripción hacia
una lista de términos concretos. No es una solución exhaustiva (no hay forma
de anticipar todos los apellidos posibles), por eso se complementa con la
confirmación explícita descrita abajo: si el nombre quedó mal transcrito, el
cliente lo corrige ahí antes de que se guarde.

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
    order_id: int | None
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

## Las seis tools

| Tool | Qué hace | Validaciones |
|---|---|---|
| `search_products(query)` | Busca un producto puntual por nombre/sinónimo/texto con errores de transcripción | — (solo lectura) |
| `add_item_to_order(product_id, quantity)` | Agrega un producto al pedido en curso | Producto existe; hay stock suficiente (sumando lo que ya llevaba pedido) |
| `set_item_quantity(product_id, quantity)` | Corrige la cantidad de un producto ya agregado; `quantity=0` lo quita | Igual que `add_item_to_order`, salvo cuando `quantity=0` |
| `vaciar_pedido()` | Borra todo el pedido en curso sin tocar la base de datos | — |
| `confirm_order(customer_name, delivery_address)` | Persiste el pedido: crea la fila en `orders`, una fila por item en `order_items`, descuenta stock, e informa un tiempo de entrega estimado (`eta_minutos`); deja `order_id` en `userdata` | `customer_name` y `delivery_address` no pueden llegar vacíos — es la tool, no el prompt, la que obliga a que el agente los haya preguntado antes. Revalida stock con `SELECT ... FOR UPDATE` dentro de la transacción (protege contra una carrera con otra llamada concurrente) |
| `finalizar_llamada(despedida)` | Dice la despedida en voz alta y luego cierra la sala (desconecta a todos) | Falla (sin cerrar nada) si `userdata.order_id` sigue en `None`, es decir, si no se confirmó ningún pedido en la llamada |

Todas menos `search_products` reciben `ctx: RunContext[PedidoEnCurso]` como
primer parámetro (LiveKit Agents lo inyecta automáticamente por tipo, no por
nombre) y operan sobre `ctx.userdata`.

### Confirmación de nombre y dirección, y cierre automático de la llamada

El prompt (`agent.py: Assistant.__init__`) exige que, una vez el agente tiene
nombre y dirección, los repita **juntos** en una sola frase y espere
confirmación explícita del cliente antes de invocar `confirm_order`; si el
cliente corrige alguno de los dos, el agente actualiza y vuelve a confirmar.
Esto es disciplina de prompt, no un contrato de la tool: `confirm_order`
sigue aceptando el valor final que se le pase, tal como antes.

Después de `confirm_order` el agente **no** cierra la llamada en ese mismo
turno: informa el tiempo estimado y pregunta si el cliente necesita algo
más. Solo cuando el cliente dice que no, llama a `finalizar_llamada`.

**La despedida la dice la tool, no el turno del LLM.** Se le pasa como
argumento (`despedida`), y la tool hace `session.say(despedida)` y espera su
reproducción antes de cerrar. Esto no es un rodeo: el modelo tiende a
encadenar `confirm_order` → `finalizar_llamada` dentro de un mismo turno sin
emitir texto, y entonces el cliente no oía despedida alguna (la despedida
aparecía recién en el turno siguiente, que ya no ocurre porque la sala se
cerró). Pasándola como argumento sigue siendo el modelo quien la redacta
—natural y variada— pero ya no puede saltársela.

Luego la tool espera un colchón fijo de 1.5s y cierra la sala con
`job_ctx.delete_room()`. Los dos detalles importan y ambos se descubrieron
fallando en una llamada web real:

- **El colchón**: `wait_for_playout()` resuelve cuando el agente terminó de
  *entregar* el audio al servidor, no cuando el cliente terminó de *oírlo*
  (`AudioSource.wait_for_playout` espera a que se drene su cola). Entre medio
  hay red y el jitter buffer del navegador. En consola no se nota, porque el
  audio sale por el dispositivo local.
- **Cerrar la sala en vez de matar el proceso**: antes esto era `os._exit(0)`,
  que mataba el subproceso de la llamada sin avisarle al servidor. El
  navegador se quedaba "en llamada" (micrófono activo, ondas de voz
  moviéndose) hasta que LiveKit notara el timeout del participante.
  `delete_room()` desconecta a todos los participantes, así que el cliente
  recibe `Disconnected` al instante y la interfaz vuelve sola a su estado
  inicial.

En `agent.py console` no hay sala real que borrar: `delete_room()` lo
detecta (`is_fake_job`) y no hace nada más que avisar en el log.

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

## Interfaz web: hablar con el agente sin teléfono

`web/` es un segundo servicio, independiente del agente (`agent.py`), pensado
para desplegarse aparte (ver `docs/DEPLOY_RAILWAY.md`). Es deliberadamente
mínimo: FastAPI + una sola página estática con JS plano y el SDK de
`livekit-client` por CDN, sin build de frontend.

```
Navegador (web/static/index.html)
    │  GET /api/token
    ▼
web/main.py (FastAPI)
    - genera un room_name nuevo por llamada (pedido-xxxxxx)
    - firma un AccessToken con RoomAgentDispatch(agent_name="agente-pollo")
    ▼
Navegador conecta por WebRTC directo a LiveKit Cloud con ese token
    │
    ▼
LiveKit Cloud despacha el worker de agent.py a esa sala (mismo agente,
mismo pipeline STT/LLM/TTS que por consola o teléfono)
```

El punto clave es `with_room_config(RoomConfiguration(agents=[RoomAgentDispatch(agent_name="agente-pollo")]))`
dentro del propio token: como `@server.rtc_session(agent_name="agente-pollo")`
usa despacho explícito (ver § Telefonía más abajo), sin esto la sala quedaría
vacía. No hace falta ninguna llamada aparte a la API de LiveKit para crear el
dispatch — viaja en el token, así que `web/main.py` no necesita mantener una
sesión HTTP hacia LiveKit ni guardar estado: cada request a `/api/token` es
independiente.

`web/` no toca `PedidoEnCurso` ni Postgres directamente; solo mintea
credenciales de sala. El estado del pedido lo sigue manejando por completo
`agent.py`, igual que en consola o por teléfono.

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
