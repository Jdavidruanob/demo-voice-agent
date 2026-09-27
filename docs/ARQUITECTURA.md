# Arquitectura y estado actual

Este documento describe **cómo funciona el sistema tal como está en esta rama
(`brasa-y-pan`)**, para que cualquiera que retome el proyecto — humano o
agente — entienda el estado real sin tener que releer todo el código.

El repo tiene varias ramas y cada una es una cosa distinta:

| Rama | Qué es |
|---|---|
| `main` | La versión original, sin las optimizaciones de fluidez ni las correcciones de bugs descritas aquí. |
| `pedidos` | La demo de toma de pedidos de un restaurante de pollo, **con base de datos propia**. |
| `reservas` | La demo de reservas de hotel, con la misma arquitectura y la misma interfaz web (en azul). |
| `brasa-y-pan` | Esta rama: la misma demo de pedidos, **conectada al sistema de pedidos real de Brasa & Pan**. |

## Qué es esto

Un agente de voz en español que atiende pedidos de **Brasa & Pan**, una
hamburguesería y asadero, construido sobre
[LiveKit Agents](https://docs.livekit.io/agents/).

**Lo que cambia en esta rama frente a `pedidos`:** el agente ya no tiene base
de datos. El catálogo y los pedidos viven en `demo-delivery-system`, el sistema
que atiende a ese mismo restaurante por WhatsApp, y un pedido cerrado hablando
aparece en el portal del restaurante con su comanda, su cronómetro, su
repartidor y su aviso al cliente — exactamente igual que uno que entró por el
menú web.

### Por qué el agente no tiene base de datos propia

Se evaluó copiar el catálogo de Brasa & Pan a una Postgres del agente, y se
descartó por un caso concreto: marcar un producto como **agotado** en el portal
del restaurante no llegaría nunca a la llamada, y el agente seguiría vendiendo
por teléfono lo que la cocina ya no tiene.

Y hay una segunda razón, más de fondo: **los precios y las promociones se
resuelven en un solo sitio.** El agente manda referencias (SKU, ids de opción,
cantidad) y el sistema de pedidos calcula el total contra su base. Si esa lógica
viviera duplicada acá, la segunda copia se quedaría atrás en silencio — y el
síntoma sería un cliente que oye un precio por teléfono y paga otro.

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
    catalogo · api · call_id (= el room_name de LiveKit)
    │
    ▼
brasa/api.py  —— HTTP ——►  demo-delivery-system (apps/menu)
    GET  /api/internal/catalog      (una vez, al arrancar la sesión)
    POST /api/internal/voice-order  (al cerrar el pedido)
    │
    ▼
Portal del restaurante: la comanda aparece en "Nuevos" con su badge 📞
```

### Por qué el menú ya no es una tool

Antes existía una tool `get_menu` que el LLM tenía que invocar y esperar
(roundtrip completo) para responder algo tan simple como "¿qué bebidas
tienen?". Como el menú (4 productos) no cambia durante una llamada, ahora se
consulta **una sola vez** al arrancar la sesión (`Catalogo.prompt_block()` en
`brasa/catalogo.py`) y se inyecta directo en las `instructions` del
`Assistant`. El agente "ya sabe" el menú desde el primer turno.

El bloque que se inyecta lleva, por producto: el **código (SKU)** entre
corchetes —que es lo que las tools reciben—, el **precio de hoy** ya resuelto
por el servidor, y los grupos de personalización con sus ids. Los obligatorios
van marcados `PREGUNTA SIEMPRE` y los opcionales `solo si lo pide`: leer en voz
alta los extras de 25 productos sería insoportable, pero el agente necesita sus
ids por si el cliente los menciona.

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

## El sistema de pedidos: dos endpoints y un secreto

No hay modelo de datos en este repo. Lo que hay es un contrato, en
`brasa/api.py`, contra la app del menú de `demo-delivery-system`. Los dos
endpoints van protegidos con `INTERNAL_SECRET`, que tiene que ser **exactamente
el mismo valor** que tienen las apps del sistema de pedidos.

| Llamada | Cuándo | Si falla |
|---|---|---|
| `GET /api/internal/catalog` | Una vez, al arrancar la sesión | La llamada **no arranca**: el agente se disculpa, manda al WhatsApp y cuelga |
| `POST /api/internal/voice-order` | Al confirmar el pedido | Dos reintentos; después, el respaldo de WhatsApp (§ Cuando algo falla) |
| `GET /api/health` | Lo consulta la página, antes de dejar llamar | El botón queda deshabilitado con el WhatsApp a la vista |

Tres cosas que no son obvias y que sostienen todo esto:

- **El agente nunca manda un precio.** `PedidoEnCurso.para_el_sistema()` envía
  solo `{sku, optionIds, quantity}`. El total lo calcula el servidor. Un agente
  que se equivoque puede pedir el producto errado; no puede inventar lo que
  cuesta.
- **El total que se le dice al cliente es el de la respuesta**, no el que sumó
  el agente. Si una promoción se vence en medio de la llamada, el número que se
  dice en voz alta y el que quedó en la comanda son el mismo.
- **`call_id` es el `room_name` de LiveKit**, y es lo que hace idempotente la
  confirmación. El modelo invoca `confirm_order` dos veces de vez en cuando; el
  sistema de pedidos tiene un índice único sobre ese campo y devuelve el mismo
  pedido en vez de mandar la comanda repetida a la cocina. **No se regenera
  nunca dentro de una llamada.**

El subtotal parcial que el agente lleva en memoria (`PedidoEnCurso.total`) es
solo para poder ir diciendo por dónde va la cuenta. Aplica la regla del 2x1
—que no baja el precio unitario, baja cuántas unidades se cobran— para que
coincida con el del servidor, pero el que manda siempre es el de
`confirm_order`.

## Cuando algo falla: tres respaldos, todos terminando en WhatsApp

En una llamada no se puede "mostrar un error". Lo único útil es mandar al
cliente a donde sí lo van a atender, y decirle la verdad sobre lo que pasó.

| Cuándo | Qué pasa |
|---|---|
| **Antes de hablar** | La página consulta `GET /api/estado` al cargar. Si el sistema de pedidos no responde, el botón queda deshabilitado y se muestra el WhatsApp. Mejor no dejar entrar a una llamada de cuatro minutos que no puede terminar en un pedido. |
| **Al arrancar la llamada** | Si el catálogo no se pudo traer, el agente **no saluda normal**: se disculpa, dice que el sistema no está disponible, manda al WhatsApp y cuelga (`_despedir_sin_servicio`). |
| **Al confirmar** | Dos reintentos con backoff corto. Si sigue fallando, el agente dice la verdad —"se me cayó el sistema, no quiero dejarte el pedido a medias, escríbenos al…"— y el pedido completo queda logueado como `[pedido-no-guardado]` para poder recuperarlo. |
| **Si LiveKit falla** | Un solo plazo de 12 s cubre pedir el token, conectar por WebRTC y que el agente entre a la sala. Al vencerse, la página muestra el mismo bloque de WhatsApp. |

**`finalizar_llamada` tiene dos caminos para poder colgar**, no uno: pedido
confirmado **o** respaldo ya dado. El candado original (no cerrar sin pedido)
dejaba un hueco: con el sistema de pedidos caído, `order_code` nunca se llena y
el agente se quedaba **sin poder despedirse**, repitiendo el error mientras el
cliente esperaba.

## Telefonía: preparado, no conectado

El código ya captura el número del cliente cuando existe un participante SIP
en la sala (`agent.py:_capturar_telefono_sip`), de forma no bloqueante (no
cuelga si no hay ningún participante SIP, como en console/playground). Pero
**no hay ningún trunk SIP configurado**: hoy el agente solo se puede probar
por `console` o por `dev` + un cliente WebRTC (playground). Conectar un
número real de Colombia (troncal con Claro/Movistar/Tigo, o portabilidad a un
proveedor SIP) es trabajo pendiente, discutido pero no iniciado — ver
`docs/SPEC.md` § Fuera de alcance.

El agente tiene nombre formal: `@server.rtc_session(agent_name=AGENT_NAME)`
en `agent.py`, con `AGENT_NAME = "agente-brasa"`. Es el nombre que un dispatch rule de SIP necesitaría para
enrutar una llamada real específicamente a este agente (paso previo útil para
cuando se conecte la telefonía). Cuando eso se haga, **nada del contrato con el
sistema de pedidos cambia**: el teléfono llegaría solo y `confirm_order` no se
toca. **Efecto secundario importante:** fijar
`agent_name` activa "explicit dispatch" en `livekit-agents` — las salas ya no
disparan el agente automáticamente. `console` no se ve afectado (simula el
job localmente), pero para probar por `dev` + Playground hay que indicar
`agente-brasa` como agent name al conectarse, o el agente simplemente no
entra a la sala.

> **La trampa número uno de este repo.** `AGENT_NAME` está en **dos** archivos:
> `agent.py` y `web/main.py`. Si se cambia en uno y no en el otro, LiveKit
> conecta perfecto y no despacha a nadie: la sala queda vacía, no hay error en
> ningún log, y el navegador se queda en "Conectando…". Lo comprueba
> `scripts/verify_contrato.py`, y el plazo de 12 s de la página lo convierte en
> un mensaje que el cliente puede leer.

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
    - GET /api/estado: ¿el sistema de pedidos está arriba?
    - genera un room_name nuevo por llamada (brasa-xxxxxx = el call_id)
    - firma un AccessToken con RoomAgentDispatch(agent_name="agente-brasa")
    ▼
Navegador conecta por WebRTC directo a LiveKit Cloud con ese token
    │
    ▼
LiveKit Cloud despacha el worker de agent.py a esa sala (mismo agente,
mismo pipeline STT/LLM/TTS que por consola o teléfono)
```

El punto clave es `with_room_config(RoomConfiguration(agents=[RoomAgentDispatch(agent_name=AGENT_NAME)]))`
dentro del propio token: como `@server.rtc_session(agent_name=AGENT_NAME)`
usa despacho explícito (ver § Telefonía más abajo), sin esto la sala quedaría
vacía. No hace falta ninguna llamada aparte a la API de LiveKit para crear el
dispatch — viaja en el token, así que `web/main.py` no necesita mantener una
sesión HTTP hacia LiveKit ni guardar estado: cada request a `/api/token` es
independiente.

`web/` no toca `PedidoEnCurso` ni el pedido; mintea credenciales de sala y
consulta si el servicio está arriba. El estado del pedido lo sigue manejando
por completo `agent.py`, igual que en consola o por teléfono.

**`web/` no importa `brasa/` a propósito.** Se construye con `web/` como Root
Directory en Railway, así que su contexto de build no ve el resto del repo. La
comprobación de salud de `GET /api/estado` repite seis líneas de un GET en vez
de acoplar los dos contextos de build para no repetirlas: el servicio web es
deliberadamente autónomo.

## Variables de entorno

| Variable | Default | Notas |
|---|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | — | Credenciales de LiveKit Cloud |
| `DELIVERY_API_URL` | — | La app del **menú** de Brasa & Pan (la que sirve `/api/internal/*`). Sin esto la llamada no arranca |
| `INTERNAL_SECRET` | — | **El mismo** valor que tienen las apps del sistema de pedidos, o el catálogo responde 401 |
| `BUSINESS_WHATSAPP_NUMBER` | — | El número real, sin `+` ni espacios. Es a dónde se manda al cliente en los tres respaldos |
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
