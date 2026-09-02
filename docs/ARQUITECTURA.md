# Arquitectura y estado actual

Este documento describe **cómo funciona el sistema tal como está en esta rama
(`macadamia`)**, para que cualquiera que retome el proyecto — humano o
agente — entienda el estado real sin tener que releer todo el código.

## Qué es esto

Un agente de voz en español que toma pedidos telefónicos para
**Restaurante Macadamia** (lasagnas, spaguettis, arroces, especiales del día
y almuerzos ejecutivos), construido sobre [LiveKit Agents](https://docs.livekit.io/agents/).
El objetivo del proyecto en su fase actual es **una demo comercial**: lo que
se evalúa es que la conversación se sienta fluida y natural, no la cantidad
de funciones ni la sofisticación del backend.

> Nota de migración: este proyecto pasó por una fase de demo de reservas de
> hotel (`hotelreservas`). Se reescribió de vuelta a dominio de restaurante
> (toma de pedidos) para **Macadamia**, conservando toda la infraestructura
> de fluidez de voz — pipeline STT/TTS, manejo de turno, latencia,
> `userdata` por llamada — que no dependía del dominio de negocio.

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
    instructions = prompt + menú completo (inyectado una vez al iniciar la sesión)
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

Los platos de Macadamia (19 en total, ver § Modelo de datos) y sus precios
no cambian durante una llamada, así que se consultan **una sola vez** al
arrancar la sesión (`build_menu_prompt_block()` en `tools/products.py`) y se
inyectan directo en las `instructions` del `Assistant`. El agente "ya sabe"
el menú completo desde el primer turno, y puede responder preguntas
generales de precio o composición de un plato sin gastar un roundtrip de
tool.

Lo que sí requiere una tool es **buscar un plato puntual** cuando el cliente
lo describe de forma ambigua o con errores de transcripción de voz
(`search_products`, con búsqueda difusa vía `pg_trgm` + sinónimos en
`keywords`) y **todo lo que modifica el estado del pedido en curso**
(`add_item_to_order`, `set_item_quantity`, `vaciar_pedido`, `confirm_order`).

### Especiales por día de la semana

`ESPECIALES` (Ajiaco los miércoles, Bandeja Paisa los viernes) usan la
columna `products.dia_disponible`. `tools/products.py:_dia_actual()` calcula
el día de hoy en Python (`date.today().weekday()`) y `build_menu_prompt_block`
le anota a cada especial si está disponible hoy o no, directo en el bloque
de menú que ve el LLM — deliberado: no se le pide al modelo que calcule qué
día es o si coincide, porque es el tipo de razonamiento que falla o alucina
fácil en una llamada de voz. El LLM solo lee la anotación y responde.

### Principio del almuerzo ejecutivo

Los 7 platos de `ALMUERZO EJECUTIVO` comparten guarnición fija (arroz
blanco, papa a la francesa, ensalada y sopa) pero el cliente elige el
principio: frijoles, lentejas o pasta. Esto se modela como un parámetro
opcional `principio` en `add_item_to_order`, validado en
`tools/orders.py:_validar_principio` contra `PRINCIPIOS_VALIDOS`
(obligatorio solo si `products.category == "almuerzo_ejecutivo"`; el prompt
le indica al LLM que siempre lo pregunte antes de llamar la tool para un
almuerzo ejecutivo). Se guarda en `ItemPedido.nota` y termina en
`order_items.notes` al confirmar.

### Por qué la detección de turno cambió

`deepgram/flux-general-multi` es un modelo de STT que además decide cuándo
el cliente terminó de hablar, en el mismo stream (`turn_handling.turn_detection
= "stt"`). Esto evita el roundtrip extra de un detector de turno separado.

Hay un camino de respaldo si Flux no convence en español-CO: cambiar
`STT_MODEL=deepgram/nova-2` en `.env` vuelve automáticamente al detector de
turno separado (ver `agent.py:_build_turn_pipeline`).

### Por qué la voz no aparece como configurable

Decisión explícita del dueño del producto: `aura-2` / `celeste` / `es-CO` es
la voz aprobada para la demo y no se toca bajo ningún motivo. Por eso está
fija en `agent.py`, no en `.env` — para que nadie la cambie por accidente
ajustando una variable de entorno.

### Nombre del restaurante

`RESTAURANT_NAME` (env, default `"Macadamia"`) se habla en el saludo
(`agent.py:SALUDO`) y en la primera línea del prompt. Es el único dato de
"marca" pensado para cambiarse sin tocar código — todo lo demás del prompt
(estilo, flujo, tools) es específico de esta demo.

### Ruido de sala de fondo

`agent.py:entrypoint` publica una **segunda pista de audio**, independiente
de la voz del agente, con `livekit.agents.BackgroundAudioPlayer`: reproduce
`assets/restaurant_ambience.wav` (ambiente de restaurante) en loop durante
toda la llamada, a `AMBIENCE_VOLUME = 0.07` — un poco más alto que en la
demo de hotel (`0.04`) porque este ambiente se percibía casi inaudible bajo
la voz; sigue quedando claramente por debajo del TTS y sin interferir con lo
que capta el STT del cliente. No es un hack casero leyendo el `.wav` a
mano — `BackgroundAudioPlayer` ya trae su propio `AudioSource`/
`LocalAudioTrack`, decodifica y resamplea el archivo, y se cierra solo con
`ctx.add_shutdown_callback` cuando la llamada termina, incluso si termina de
forma abrupta.

## Estado del pedido: `session.userdata`, no un global

`tools/orders.py` define:

```python
@dataclass
class ItemPedido:
    product_id: int
    name: str
    unit_price: int
    quantity: int
    nota: str | None = None   # principio del almuerzo ejecutivo, si aplica

@dataclass
class PedidoEnCurso:
    items: list[ItemPedido]
    customer_phone: str | None
    customer_name: str | None
    delivery_address: str | None
```

Cada `AgentSession` tiene su propia instancia (`AgentSession[PedidoEnCurso](userdata=PedidoEnCurso(), ...)`).
Esto importa por dos razones:

1. **Aislamiento entre llamadas.** Dos llamadas concurrentes en el mismo
   proceso worker nunca comparten ni mezclan su pedido.
2. **Es requisito para telefonía.** Un worker de LiveKit atiende múltiples
   llamadas (jobs) en el mismo proceso; sin este aislamiento, telefonía real
   sería inviable.

## Las tools

| Tool | Qué hace | Validaciones |
|---|---|---|
| `search_products(query)` | Búsqueda difusa de un plato puntual (sinónimos + tolerancia a errores de transcripción de voz, vía `pg_trgm`) | Ninguna; devuelve `found=False` si no hay coincidencias |
| `add_item_to_order(product_id, quantity, principio=None)` | Agrega (o suma cantidad a) un plato en el pedido en curso | El producto debe existir y tener stock suficiente; `principio` obligatorio y válido (`frijoles`/`lentejas`/`pasta`) si la categoría es `almuerzo_ejecutivo` |
| `set_item_quantity(product_id, quantity)` | Corrige la cantidad final de un plato ya agregado (0 lo quita) | Igual que arriba; `quantity < 0` rechazado |
| `vaciar_pedido()` | Borra todo el pedido en curso sin tocar la base de datos | — |
| `confirm_order(customer_name, delivery_address)` | Persiste el pedido: crea `orders` + `order_items` con el precio y la nota (`notes`) de cada plato | `customer_name`/`delivery_address` no vacíos; el pedido no puede estar vacío; revalida stock con `SELECT ... FOR UPDATE` dentro de la transacción (protege contra una carrera con otra llamada concurrente) |

Todas reciben `ctx: RunContext[PedidoEnCurso]` como primer parámetro
(LiveKit Agents lo inyecta automáticamente por tipo, no por nombre).

### Manejo de errores: `ToolError`, no excepciones genéricas

Si una tool deja escapar una excepción común (`ValueError`, etc.), LiveKit
Agents la convierte en el mensaje `"An internal error occurred"` — en inglés,
en medio de una llamada en español. Por eso el único camino de error
inesperado que puede ocurrir dentro de una transacción (el stock se agotó
justo antes de confirmar, detectado dentro de `confirm_order`) usa
`raise ToolError("mensaje en español")` explícitamente: ese mensaje sí llega
intacto al LLM. Los errores esperables (plato inexistente, sin stock,
principio faltante o inválido) no son excepciones: son un
`{"success": False, "message": "..."}` normal, que el LLM ve como cualquier
otro resultado de tool.

## Modelo de datos

```sql
products      (id, name, description, price, stock, category, dia_disponible, keywords)
orders        (id, status, customer_phone, customer_name, delivery_address, total, created_at)
order_items   (id, order_id, product_id, quantity, unit_price, notes)
```

- `products.category` es un slug en minúscula (`lasagna`, `spaguetti`,
  `arroces`, `especiales`, `almuerzo_ejecutivo`); `tools/products.py:_CATEGORIAS`
  lo mapea a los encabezados en mayúscula del menú, **en un orden fijo**
  (no alfabético) que sigue el orden natural de una carta de restaurante.
- `products.dia_disponible` es `NULL` para todo lo que no sea un especial;
  para Ajiaco es `'miercoles'` y para Bandeja Paisa `'viernes'` (sin
  tildes, para comparar directo contra `date.today().weekday()` mapeado a
  texto — ver `tools/products.py:_DIAS_ES`).
- `products.keywords` guarda sinónimos y formas comunes de pedir un plato
  por voz (ej. `"espagueti"` para Spaguetti, `"la paisa"` para Bandeja
  Paisa), usados por `search_products` además de similitud trigram
  (`pg_trgm`) sobre nombre y descripción.
- `products.stock` es el inventario disponible de cada plato — mismo patrón
  que la demo de pollo original; se descuenta al confirmar un pedido dentro
  de la misma transacción que revalida stock (protegido con `FOR UPDATE`).
- `order_items.unit_price` congela el precio del plato al momento del
  pedido: un cambio de precio futuro no altera pedidos ya confirmados.
- `order_items.notes` guarda la customización libre del ítem — hoy solo se
  usa para el principio del almuerzo ejecutivo (`frijoles`/`lentejas`/
  `pasta`); `NULL` para cualquier otro plato.
- `orders.customer_phone` se llena desde telefonía SIP
  (`agent.py:_capturar_telefono_sip`) cuando aplica; en console/playground
  queda `NULL`. `customer_name` y `delivery_address` son `NOT NULL`:
  `confirm_order` los exige como parámetros obligatorios y los valida antes
  de insertar.
- `database/schema.sql` inserta los 19 platos del menú de Macadamia
  (3 lasagnas, 4 spaguettis, 3 arroces, 2 especiales, 7 almuerzos
  ejecutivos) con stock inicial, sin pedidos de ejemplo (a diferencia de la
  demo de hotel, que sí precargaba reservas para poder probar el escenario
  de "sin disponibilidad" sin crear datos a mano).

## Telefonía: preparado, no conectado

El código ya captura el número del cliente cuando existe un participante SIP
en la sala (`agent.py:_capturar_telefono_sip`, guarda en
`PedidoEnCurso.customer_phone`), de forma no bloqueante (no cuelga si no hay
ningún participante SIP, como en console/playground). Es solo informativo:
el agente igual pide un teléfono... — nota: hoy `confirm_order` no exige
teléfono como parámetro (solo nombre y dirección); si se necesita capturarlo
siempre, hay que agregarlo al contrato de la tool. Pero **no hay ningún
trunk SIP configurado**: hoy el agente solo se puede probar por `console` o
por `dev` + un cliente WebRTC (playground o `web/`).

El agente tiene nombre formal:
`@server.rtc_session(agent_name="agente-macadamia")` en `agent.py`. Es el
nombre que un dispatch rule de SIP necesitaría para enrutar una llamada real
específicamente a este agente. **Efecto secundario importante:** fijar
`agent_name` activa "explicit dispatch" en `livekit-agents` — las salas ya no
disparan el agente automáticamente. `console` no se ve afectado (simula el
job localmente), pero para probar por `dev` + Playground hay que indicar
`agente-macadamia` como agent name al conectarse, o el agente simplemente no
entra a la sala.

`web/` (Next.js, ver README § Cliente web de prueba) es un cliente WebRTC de
prueba que resuelve esto del lado servidor: `web/app/api/token/route.ts`
firma el access token con `roomConfig.agents = [RoomAgentDispatch({agentName:
"agente-macadamia"})]`, que es el equivalente en token de indicar el agent
name a mano en Playground. Es un proyecto npm independiente (propio
`package.json`, no toca `pyproject.toml`/`uv.lock`) que solo comparte el
proyecto de LiveKit (mismas tres credenciales) y la constante `agent_name`
con `agent.py` — si cambia una, hay que cambiar la otra.

## Variables de entorno

| Variable | Default | Notas |
|---|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | — | Credenciales de LiveKit Cloud |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | `localhost` / `5433` / `admin` / `password` / `macadamia` | `5433` para no chocar con otra Postgres local en `5432` |
| `LLM_MODEL` | `openai/gpt-4.1-mini` | Ver `scripts/bench_llm.py` para comparar candidatos |
| `STT_MODEL` | `deepgram/flux-general-multi` | Cambiar a `deepgram/nova-2` vuelve al camino de respaldo |
| `RESTAURANT_NAME` | `Macadamia` | Nombre del restaurante en el saludo y el prompt |
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

- Sintaxis y estructura del pipeline: `agent.py`, `tools/products.py` y
  `tools/orders.py` importan y componen correctamente.
- `uv run agent.py dev` arranca sin errores y registra el worker
  (`agent_name=agente-macadamia`) contra LiveKit Cloud.
- Contra Postgres real (`database/schema.sql` cargado): `build_menu_prompt_block`
  arma el menú completo con la anotación de especiales del día correcta;
  `add_item_to_order` valida el principio del almuerzo ejecutivo (rechaza
  si falta o es inválido, acepta y guarda si es válido); `confirm_order`
  descuenta stock y persiste `orders`/`order_items` con `notes` correcto;
  `search_products` resuelve sinónimos y errores de transcripción comunes
  ("espagueti" → Spaguetti, "la paisa" → Bandeja Paisa).
- `tools/formato.py:formatear_platos` pluraliza solo la primera palabra del
  nombre del plato (ej. "2 Lasagnas Bolognesa", "3 Chuletas de Cerdo", "3
  Arroces con Pollo"), nunca la forma "X de [plato]".

## No verificado / pendiente de tu parte

- Correr `uv run agent.py console` de punta a punta por voz, incluyendo el
  guion completo de `docs/SPEC.md` § Criterios de aceptación.
- Calidad subjetiva de Flux transcribiendo español colombiano hablado para
  los nombres de plato de este menú (ej. "spaguetti", "bandeja paisa").
- Todo lo relacionado a telefonía real (trunk SIP, número colombiano) — ver
  `docs/SPEC.md`.
- Los precios del menú son valores de referencia para la demo (no vienen
  del negocio real); ajustarlos en `database/schema.sql` si Macadamia da
  una lista de precios oficial.
