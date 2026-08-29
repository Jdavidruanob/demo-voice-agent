# Arquitectura y estado actual

Este documento describe **cómo funciona el sistema tal como está en esta rama
(`reservas`)**, para que cualquiera que retome el proyecto — humano o
agente — entienda el estado real sin tener que releer todo el código.

El repo tiene tres ramas y cada una es una cosa distinta:

| Rama | Qué es |
|---|---|
| `main` | La versión original, sin las optimizaciones de fluidez ni las correcciones de bugs descritas aquí. |
| `pedidos` | La demo de toma de pedidos de un restaurante de pollo. |
| `reservas` | Esta rama: la demo de reservas de hotel. Misma arquitectura y misma interfaz web que `pedidos` (en azul); solo cambia el dominio de negocio. |

## Qué es esto

Un agente de voz en español que atiende la recepción telefónica de un hotel,
construido sobre [LiveKit Agents](https://docs.livekit.io/agents/). El
objetivo del proyecto en su fase actual es **una demo comercial**: lo que se
evalúa es que la conversación se sienta fluida y natural, no la cantidad de
funciones ni la sofisticación del backend.

> Nota de migración: este proyecto era originalmente una demo de toma de
> pedidos para una cadena de restaurantes de pollo. Se reescribió a dominio
> de hotel (disponibilidad y reservas) conservando toda la infraestructura de
> fluidez de voz — pipeline STT/TTS, manejo de turno, latencia, `userdata`
> por llamada — que no dependía del dominio de negocio.

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
    instructions = prompt + catálogo de tipos de habitación (inyectado una vez al iniciar la sesión)
    tools:
      - consultar_disponibilidad  (tools/habitaciones.py)
      - crear_reserva             (tools/reservas.py)
      - finalizar_llamada         (tools/call.py)
    │
    ▼
session.userdata: ReservaEnCurso   (estado de ESTA llamada, no global)
    │
    ▼
Postgres (database/connection.py — pool singleton)
    habitaciones / reservas
```

### Por qué el catálogo de habitaciones ya no es una tool

Igual que el menú en la versión anterior del proyecto: los tipos de
habitación (4, ver § Modelo de datos) y su precio por noche no cambian
durante una llamada, así que se consultan **una sola vez** al arrancar la
sesión (`build_catalogo_prompt_block()` en `tools/habitaciones.py`) y se
inyectan directo en las `instructions` del `Assistant`. El agente "ya sabe"
el catálogo desde el primer turno, y puede responder preguntas generales de
precio o capacidad sin gastar un roundtrip de tool.

Lo que **sí** cambia durante la llamada (y entre llamadas) es la
disponibilidad real para un rango de fechas concreto, así que
`consultar_disponibilidad` se mantiene como tool: consulta cuántos cupos
libres quedan de cada tipo de habitación para las fechas y el número de
huéspedes que pide el cliente, calculando el solapamiento contra `reservas`
en el momento (ver § Modelo de datos).

### Por qué la detección de turno cambió

`deepgram/flux-general-multi` es un modelo de STT que además decide cuándo
el cliente terminó de hablar, en el mismo stream (`turn_handling.turn_detection
= "stt"`). Esto evita el roundtrip extra de un detector de turno separado.

Hay un camino de respaldo si Flux no convence en español-CO: cambiar
`STT_MODEL=deepgram/nova-2` en `.env` vuelve automáticamente al detector de
turno separado (ver `agent.py:_build_turn_pipeline`).

### La fecha de hoy va en el prompt

Las tools exigen fechas en `AAAA-MM-DD`, pero el cliente dice "mañana" o "el
próximo viernes". Un LLM no sabe qué día es hoy: sin decírselo, inventa una
fecha plausible y la reserva queda guardada en el día equivocado sin que
nadie se entere durante la llamada.

Por eso `agent.py:_fecha_de_hoy_texto()` inyecta la fecha actual en las
`instructions` al iniciar cada sesión (no al importar el módulo: un worker
puede quedar días corriendo). La zona horaria es `America/Bogotá` por
defecto, configurable con `ZONA_HORARIA` — el worker en Railway corre en UTC,
así que la hora local del proceso no sirve.

### Reconocimiento de apellidos poco comunes

Al STT (Deepgram, en ambos caminos de `_build_turn_pipeline`) se le pasa
`extra_kwargs={"keyterm": APELLIDOS_A_RECONOCER}` con una lista corta de
apellidos colombianos que un modelo genérico tiende a transcribir mal
("Ruano", "Burbano", etc.) — Deepgram permite sesgar la transcripción hacia
una lista de términos concretos. No es una solución exhaustiva (no hay forma
de anticipar todos los apellidos posibles), por eso se complementa con la
confirmación explícita del nombre antes de guardar la reserva.

### Por qué la voz no aparece como configurable

Decisión explícita del dueño del producto: `aura-2` / `celeste` / `es-CO` es
la voz aprobada para la demo y no se toca bajo ningún motivo. Por eso está
fija en `agent.py`, no en `.env` — para que nadie la cambie por accidente
ajustando una variable de entorno.

### Nombre de la recepcionista y del hotel

`RECEPTIONIST_NAME` y `HOTEL_NAME` (env, con default `"Valentina"` /
`"Hotel Colonial"`) se hablan en el saludo (`agent.py:SALUDO`) y en la
primera línea del prompt. Son los únicos datos de "marca" pensados para
cambiarse sin tocar código — todo lo demás del prompt (estilo, flujo,
tools) es específico de esta demo.

### Ruido de sala de fondo

`agent.py:entrypoint` publica una **segunda pista de audio**, independiente
de la voz del agente, con `livekit.agents.BackgroundAudioPlayer`: reproduce
`assets/hotel_sonido.wav` (ambiente de lobby de hotel) en loop a volumen muy
bajo (`AMBIENCE_VOLUME = 0.04`) durante toda la llamada. No es un hack
casero leyendo el `.wav` a mano — `BackgroundAudioPlayer` ya trae su propio
`AudioSource`/`LocalAudioTrack`, decodifica y resamplea el archivo (soporta
cualquier sample rate/canales de entrada), y se cierra solo con
`ctx.add_shutdown_callback` cuando la llamada termina, incluso si termina de
forma abrupta.

## Estado de la reserva: `session.userdata`, no un global

`tools/reservas.py` define:

```python
@dataclass
class ReservaEnCurso:
    fecha_entrada: str | None
    fecha_salida: str | None
    num_huespedes: int | None
    customer_phone_sip: str | None
```

Cada `AgentSession` tiene su propia instancia (`AgentSession[ReservaEnCurso](userdata=ReservaEnCurso(), ...)`).
Esto importa por dos razones:

1. **Aislamiento entre llamadas.** Dos llamadas concurrentes en el mismo
   proceso worker nunca comparten ni mezclan su reserva.
2. **Es requisito para telefonía.** Un worker de LiveKit atiende múltiples
   llamadas (jobs) en el mismo proceso; sin este aislamiento, telefonía real
   sería inviable.

`fecha_entrada`, `fecha_salida` y `num_huespedes` se llenan cuando el agente
llama `consultar_disponibilidad`, así `crear_reserva` no obliga a
repreguntarlos si ya se establecieron antes en la misma llamada (aunque
`crear_reserva` los sigue recibiendo como parámetros explícitos — ver
§ contrato de las tools en `docs/SPEC.md` para el porqué).

## Las tres tools

| Tool | Qué hace | Validaciones |
|---|---|---|
| `consultar_disponibilidad(fecha_entrada, fecha_salida, num_huespedes)` | Calcula cupos libres por tipo de habitación para un rango de fechas y devuelve hasta 2 opciones con precio por noche y total, más un texto listo para leer | Fechas válidas y `fecha_salida > fecha_entrada`; `num_huespedes >= 1` |
| `crear_reserva(cliente_nombre, cliente_telefono, tipo_habitacion, fecha_entrada, fecha_salida, num_huespedes)` | Persiste la reserva: crea la fila en `reservas` con el precio de esa noche congelado | `cliente_nombre` y `cliente_telefono` no pueden llegar vacíos; el tipo de habitación debe existir y tener capacidad y cupo suficientes; revalida disponibilidad con `SELECT ... FOR UPDATE` dentro de la transacción (protege contra una carrera con otra llamada concurrente) |
| `finalizar_llamada(despedida)` | Dice la despedida en voz alta, espera a que se oiga completa y cierra la sala | Se niega a cerrar si `ReservaEnCurso.reserva_id` sigue en `None` (no se ha confirmado ninguna reserva en esta llamada) |

Ambas reciben `ctx: RunContext[ReservaEnCurso]` como primer parámetro
(LiveKit Agents lo inyecta automáticamente por tipo, no por nombre).

### Confirmación de los datos y cierre automático de la llamada

El prompt (`agent.py: Assistant.__init__`) exige que, una vez el agente tiene
nombre y teléfono, los repita **juntos** en una sola frase junto con las
fechas y espere confirmación explícita del cliente antes de invocar
`crear_reserva`; si el cliente corrige alguno, el agente actualiza y vuelve a
confirmar. Esto es disciplina de prompt, no un contrato de la tool:
`crear_reserva` sigue aceptando el valor final que se le pase.

Después de `crear_reserva` el agente **no** cierra la llamada en ese mismo
turno: da el número de reserva y el total, y pregunta si el cliente necesita
algo más. Solo cuando el cliente dice que no, llama a `finalizar_llamada`.

**La despedida la dice la tool, no el turno del LLM.** Se le pasa como
argumento (`despedida`), y la tool hace `session.say(despedida)` y espera su
reproducción antes de cerrar. Esto no es un rodeo: el modelo tiende a
encadenar `crear_reserva` → `finalizar_llamada` dentro de un mismo turno sin
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
inesperado que puede ocurrir dentro de una transacción (una carrera de
disponibilidad detectada dentro de `crear_reserva`) usa
`raise ToolError("mensaje en español")` explícitamente: ese mensaje sí llega
intacto al LLM. Los errores esperables (fechas inválidas, tipo de habitación
inexistente, capacidad insuficiente) no son excepciones: son un
`{"success"/"disponible": False, "message"/"resumen": "..."}` normal, que el
LLM ve como cualquier otro resultado de tool.

**Caída de Postgres:** a diferencia del resto de errores esperables, esta
sí se contempla explícitamente con un `try/except (asyncpg.PostgresError,
OSError)` alrededor de las consultas en ambas tools, devolviendo un mensaje
de respaldo fijo (`MENSAJE_FALLBACK_DB` en cada módulo: *"En este momento no
puedo acceder al sistema de reservas, pero puedo tomar tus datos..."*) en
vez de dejar que el agente se caiga o se quede en silencio.

## Modelo de datos

```sql
habitaciones   (id, tipo_habitacion, descripcion, capacidad, precio_noche, disponible)
reservas       (id, cliente_nombre, cliente_telefono, fecha_entrada, fecha_salida,
                num_huespedes, tipo_habitacion_id, precio_noche, estado, created_at)
servicios_hotel (id, nombre, detalle)
```

- `habitaciones.disponible` es el **inventario total** de ese tipo de
  habitación (6 tipos de ejemplo: Sencilla, Doble, Triple, Familiar, Suite
  Junior, Suite Presidencial) — el mismo patrón que `products.stock` en la
  versión anterior —, no un booleano por fecha. La disponibilidad real para
  un rango de fechas se calcula restando las reservas que se solapan con ese
  rango: `disponible - COUNT(reservas solapadas y no canceladas)`.
- `habitaciones.descripcion` guarda amenidades en texto libre (vista,
  minibar, jacuzzi, etc.) para que el agente pueda dar detalle cuando el
  cliente pregunta por un tipo en concreto, no solo precio y capacidad. Se
  inyecta en el mismo bloque de catálogo que la tarifa
  (`build_catalogo_prompt_block`).
- `servicios_hotel` es información general del hotel que no depende de
  fechas ni de habitaciones (horarios de check-in/out, desayuno, wifi,
  parqueadero, piscina, mascotas). Se inyecta una sola vez en el prompt con
  `build_servicios_prompt_block`, igual que el catálogo — es estática dentro
  de una llamada, así que no necesita ser una tool.
- `database/schema.sql` trae 5 reservas de ejemplo en fechas de septiembre
  2026 que agotan a propósito el cupo de Suite Presidencial (10-15 sep.) y
  de Familiar (12-14 sep.), para poder probar el escenario de "sin
  disponibilidad" de `consultar_disponibilidad` sin tener que crear reservas
  a mano primero.
- El solapamiento de fechas se evalúa con la condición estándar
  `r.fecha_entrada < salida_pedida AND r.fecha_salida > entrada_pedida`.
- `reservas.precio_noche` congela la tarifa al momento de la reserva: un
  cambio de tarifa futuro no altera reservas ya confirmadas (igual que
  `order_items.unit_price` en la versión anterior).
- `reservas.cliente_telefono` es `NOT NULL`: a diferencia del pedido de
  comida (donde el teléfono venía solo de telefonía SIP), en una reserva de
  hotel el agente siempre lo pide explícitamente como dato de contacto —
  `crear_reserva` lo exige como parámetro obligatorio.
- `reservas.estado` (`confirmada` por defecto) deja espacio para
  `cancelada` a futuro; el cálculo de disponibilidad ya excluye reservas
  canceladas, aunque hoy no existe ninguna tool para cancelar (fuera de
  alcance, ver `docs/SPEC.md` § Fuera de alcance).

## Telefonía: preparado, no conectado

El código ya captura el número del cliente cuando existe un participante SIP
en la sala (`agent.py:_capturar_telefono_sip`, guarda en
`ReservaEnCurso.customer_phone_sip`), de forma no bloqueante (no cuelga si
no hay ningún participante SIP, como en console/playground). Es solo
informativo: el agente igual pide un teléfono de contacto en la
conversación para `crear_reserva`, porque el número que llama no siempre es
el mejor número de contacto para la reserva. Pero **no hay ningún trunk SIP
configurado**: hoy el agente solo se puede probar por `console` o por `dev`
+ un cliente WebRTC (playground o `web/`).

El agente tiene nombre formal:
`@server.rtc_session(agent_name="agente-reservas")` en `agent.py`. Es
el nombre que un dispatch rule de SIP necesitaría para enrutar una llamada
real específicamente a este agente. **Efecto secundario importante:** fijar
`agent_name` activa "explicit dispatch" en `livekit-agents` — las salas ya no
disparan el agente automáticamente. `console` no se ve afectado (simula el
job localmente), pero para probar por `dev` + Playground hay que indicar
`agente-reservas` como agent name al conectarse, o el agente simplemente no
entra a la sala.

`web/` (FastAPI + una página estática, ver README § Interfaz web) resuelve
esto del lado servidor: `web/main.py` firma el access token con
`RoomConfiguration(agents=[RoomAgentDispatch(agent_name="agente-reservas")])`,
que es el equivalente en token de indicar el agent name a mano en Playground.
Es un servicio aparte (su propio `web/requirements.txt` y `web/Dockerfile`,
no toca `pyproject.toml`/`uv.lock`) que solo comparte con `agent.py` el
proyecto de LiveKit (mismas tres credenciales) y la constante `agent_name`
— si cambia una, hay que cambiar la otra, o la sala se crea y el agente
nunca entra.

Los dos servicios (worker y web) se despliegan en Railway; el paso a paso
está en [`DEPLOY_RAILWAY.md`](DEPLOY_RAILWAY.md).

## Variables de entorno

| Variable | Default | Notas |
|---|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | — | Credenciales de LiveKit Cloud |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | `localhost` / `5433` / `admin` / `password` / `hotel_reservas` | `5433` para no chocar con otra Postgres local en `5432` |
| `LLM_MODEL` | `openai/gpt-4.1-mini` | Ver `scripts/bench_llm.py` para comparar candidatos |
| `STT_MODEL` | `deepgram/flux-general-multi` | Cambiar a `deepgram/nova-2` vuelve al camino de respaldo |
| `RECEPTIONIST_NAME` | `Valentina` | Nombre que el agente usa para sí mismo en el saludo y el prompt |
| `HOTEL_NAME` | `Hotel Colonial` | Nombre del hotel en el saludo y el prompt |
| `ZONA_HORARIA` | `America/Bogota` | Con la que el agente resuelve "mañana" o "el viernes" a una fecha real |
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

- Sintaxis y estructura del pipeline: `agent.py`, `tools/habitaciones.py` y
  `tools/reservas.py` importan y componen correctamente (revisar `uv run
  agent.py console` con Postgres real cargado con `database/schema.sql`
  antes de mostrar la demo).

## No verificado / pendiente de tu parte

- Correr `uv run agent.py console` de punta a punta contra Postgres real,
  incluyendo el guion completo de `docs/SPEC.md` § Criterios de aceptación.
- Calidad subjetiva de Flux transcribiendo español colombiano hablado, en
  este dominio nuevo (fechas, números de habitación, nombres de huéspedes).
- Todo lo relacionado a telefonía real (trunk SIP, número colombiano) — ver
  `docs/SPEC.md`.
