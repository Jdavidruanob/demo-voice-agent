# Spec del producto

Spec en el sentido de "spec-driven development": la referencia que describe
**qué debe hacer el sistema y bajo qué condiciones se considera correcto**,
independiente de cómo esté implementado hoy. Cuando el código y este
documento no coincidan, uno de los dos está desactualizado — corrígelo antes
de seguir construyendo encima. Ver `docs/ARQUITECTURA.md` para el cómo.

## 1. Objetivo

Un agente de voz en español que le muestre a un comprador potencial (dueño de
restaurante) que un bot puede tomar un pedido por teléfono **de forma fluida
y natural**, sin sonar robótico ni tener pausas incómodas. La fluidez es la
métrica de éxito de esta fase, por encima de cobertura de funcionalidades.

## 2. Alcance de esta fase (demo comercial)

**Dentro de alcance:**
- Tomar un pedido de un menú fijo y pequeño (4 productos).
- Permitir que el cliente se corrija a mitad de pedido.
- Confirmar y persistir el pedido.
- Sonar fluido: sin silencios muertos, sin cortes de turno torpes, primera
  respuesta instantánea.

**Fuera de alcance (explícito, no es un olvido):**
- Múltiples sedes/sucursales.
- Reservas de mesa.
- Estados de cocina (`en preparación`, `en camino`) o seguimiento del
  domiciliario. El nombre y la dirección de entrega sí se capturan (ver
  RF-9); lo que queda fuera es el tracking posterior al pedido.
- Pagos.
- Portal/dashboard para el restaurante (se discutió arquitectura — Postgres
  compartida entre agente y portal — pero no se construyó).
- Conexión a un número de teléfono real (troncal SIP, portabilidad).
  Discutido a fondo (ver hilo de decisiones más abajo) pero no iniciado.
- Multi-idioma. Solo español.
- Backchanneling con audio real superpuesto (que el agente diga "ajá" con su
  propia voz *mientras* el cliente sigue hablando, sin cerrar su turno). El
  `AgentSession` de `livekit-agents` es de un solo canal: el LLM solo genera
  una respuesta después de que el turno del usuario se cierra, y una pausa
  más corta que `min_silence_duration` del VAD no emite ningún evento que se
  pueda interceptar. Implementarlo de verdad requeriría esquivar el pipeline
  LLM/TTS con detección de energía sobre audio crudo y un clip pregrabado —
  una feature de audio en tiempo real aparte, no un ajuste de pipeline. Lo
  que sí se implementó como aproximación es a nivel de prompt: cuando el
  agente recibe el turno y el pedido claramente sigue, responde con un
  backchannel corto en vez de una respuesta completa (ver `agent.py` §
  ESCUCHA ACTIVA).

## 3. Requisitos funcionales

Expresados como comportamiento observable, no como implementación.

**RF-1 — Saludo inmediato.** Al iniciar la sesión, el agente saluda sin que
el cliente tenga que hablar primero, y sin una pausa perceptible de roundtrip
de LLM.

**RF-2 — Consulta de menú sin fricción.** Preguntas generales o por
categoría ("¿qué bebidas tienen?", "¿qué combos manejan?") se responden en el
mismo turno, sin necesidad de que el sistema "vaya a buscar" nada.

**RF-3 — Identificación de producto específico.** Si el cliente nombra o
describe un producto puntual —incluso con errores de transcripción de voz o
sinónimos coloquiales— el sistema identifica el producto correcto o, si hay
ambigüedad real, pregunta cuál en vez de asumir.

**RF-4 — Construcción incremental del pedido.** El cliente puede ir
agregando productos uno a uno a lo largo de la conversación.

**RF-5 — Corrección del pedido.** El cliente puede cambiar de opinión en
cualquier momento antes de confirmar: quitar un producto, cambiar una
cantidad, o cancelar el pedido completo y empezar de nuevo. Ninguna de estas
acciones debe fallar, trabarse, ni hacer que el agente mienta sobre el estado
del pedido.

**RF-6 — Confirmación explícita.** El pedido solo se considera final cuando
el cliente lo confirma de forma explícita después de que el agente repite
productos y cantidades. Nunca se persiste antes de esa confirmación. Esa
repetición debe sonar natural, no como una lectura de datos: cantidades en
palabras, nombre completo del producto, y pluralización cuando la cantidad es
mayor a uno (ej. "dos Coca-Colas y una hamburguesa", no "2 de Coca-Cola y 1
de hamburguesa").

**RF-7 — Rechazo de lo inexistente.** Si el cliente pide algo que no está en
el menú o una cantidad que excede el stock disponible, el sistema lo dice con
naturalidad, sin inventar disponibilidad ni productos.

**RF-8 — Persistencia con integridad.** Al confirmar, el pedido se guarda de
forma atómica (todo o nada), con el total correcto, y el stock se descuenta
de forma consistente incluso si dos llamadas confirman al mismo tiempo sobre
el mismo producto.

**RF-9 — Nombre, dirección y tiempo de entrega.** Antes de confirmar, el
agente siempre pregunta (uno a la vez) a nombre de quién queda el pedido y la
dirección de entrega — nunca los asume ni los inventa, aunque el cliente los
haya mencionado de pasada antes. Una vez tiene ambos, los repite **juntos**
en una sola frase y espera confirmación explícita del cliente antes de
llamar a `confirm_order`; si el cliente corrige el nombre o la dirección en
ese momento, el agente actualiza el dato y repite la confirmación de nuevo
antes de continuar. `confirm_order` exige los dos como parámetros
obligatorios, así que estructuralmente no puede confirmarse un pedido sin
ambos — pero el requisito de repetirlos juntos y esperar confirmación es
disciplina de conversación (prompt), no un contrato de la tool. Al confirmar,
el agente le informa al cliente un tiempo estimado de entrega (30 minutos
fijos en esta demo, no un cálculo real de logística).

**RF-10 — Cierre de llamada tras la despedida.** Una vez el pedido está
confirmado y el cliente indica que no necesita nada más, el agente se
despide y cierra la llamada (`finalizar_llamada`) en el mismo turno de la
despedida, sin cortar el audio a mitad de frase. `finalizar_llamada` rechaza
cerrar si todavía no hay ningún pedido confirmado en la llamada.

## 4. Requisitos no funcionales

**RNF-1 — Fluidez (prioridad máxima de esta fase).** El tiempo entre que el
cliente termina de hablar y el agente empieza a responder (`e2e_latency`,
ver `docs/ARQUITECTURA.md` § Observabilidad) debe mantenerse bajo, y ningún
paso intermedio (consultar el menú, buscar un producto) debe introducir un
silencio perceptible sin que el agente diga algo mientras tanto. Parte de
esto es percepción, no solo latencia real: el agente usa ocasionalmente
muletillas de transición variadas ("a ver, dame un segundo...", "déjame
confirmo...") antes de una búsqueda o al confirmar, para sonar como una
persona pensando en vez de un sistema respondiendo de forma instantánea y
perfecta. Es ocasional y variado a propósito (ver `agent.py` § TONO Y
LATENCIA CONVERSACIONAL) — usarlo en cada turno tendría el efecto contrario
y sonaría mecánico. Por la misma razón, el agente también suma ocasionalmente
matices vocales cortos ("mmm...", "jajaja", "ahhh ya"; ver `agent.py` §
EXPRESIONES HUMANAS Y MATICES VOCALES). El texto del LLM llega sin filtrar al
TTS (sin limpieza de puntuación ni markdown), a propósito: los puntos
suspensivos y comas son la señal que `deepgram/aura-2` usa para variar pausas
y entonación.

**RNF-2 — Voz fija.** La voz (`aura-2` / `celeste` / `es-CO`) es una decisión
de producto ya tomada y aprobada. Ningún cambio futuro debe alterarla salvo
instrucción explícita y nueva del dueño del producto.

**RNF-3 — Idioma.** Toda interacción con el cliente, incluyendo mensajes de
error de las tools que el LLM pueda verbalizar, debe estar en español. (Esto
descarta dejar que una excepción sin capturar llegue al LLM: el mensaje
genérico de error de LiveKit Agents está en inglés — ver
`docs/ARQUITECTURA.md` § Manejo de errores.)

**RNF-4 — Aislamiento entre llamadas.** El estado de un pedido de una llamada
nunca debe ser visible ni modificable desde otra llamada concurrente en el
mismo proceso.

**RNF-5 — Configurabilidad para decidir con datos.** STT y LLM deben poder
cambiarse por variable de entorno, para comparar alternativas sin editar
código (ver `scripts/bench_llm.py`). El TTS es la excepción intencional
(RNF-2).

## 5. Contrato de las tools

Formato: nombre — precondición — postcondición — modo de fallo.

**`search_products(query: str)`**
- Precondición: ninguna.
- Postcondición: devuelve `{"found": bool, "products": [...]}` con hasta 5
  coincidencias ordenadas por similitud.
- Fallo: nunca lanza excepción; `found=False` si no hay coincidencias.

**`add_item_to_order(product_id: int, quantity: int)`**
- Precondición: el producto debe existir y tener stock suficiente contando
  lo que ya llevaba pedido ese mismo producto.
- Postcondición: el item se agrega (o se suma a la cantidad existente si el
  producto ya estaba en el pedido); devuelve el pedido completo y el total
  corriente.
- Fallo: `{"success": False, "message": "..."}` en español si el producto no
  existe o no hay stock — nunca una excepción.

**`set_item_quantity(product_id: int, quantity: int)`**
- Precondición: `quantity >= 0`. Si `quantity > 0` y el producto no estaba en
  el pedido, se comporta como agregarlo (mismas validaciones de stock).
- Postcondición: la cantidad de ese producto en el pedido queda exactamente
  en `quantity`; `quantity=0` lo elimina del pedido.
- Fallo: mensaje en español si `quantity < 0`, si el producto no existe, o si
  no hay stock suficiente.

**`vaciar_pedido()`**
- Precondición: ninguna.
- Postcondición: el pedido en curso queda vacío. No toca la base de datos.
- Fallo: no aplica.

**`confirm_order(customer_name: str, delivery_address: str)`**
- Precondición: el pedido en curso tiene al menos un item; `customer_name` y
  `delivery_address` no pueden llegar vacíos (se valida con `.strip()`) — el
  agente debe haberlos preguntado antes de llamar la tool.
- Postcondición: se crea una fila en `orders` (con `total`, `customer_name`,
  `delivery_address` y `customer_phone` si existe) y una fila por item en
  `order_items` (con `unit_price` congelado); el stock de cada producto queda
  descontado; el pedido en curso se vacía; `userdata.order_id` queda con el
  id de la orden creada; la respuesta incluye `eta_minutos` para que el
  agente se lo diga al cliente.
- Fallo: `{"success": False, ...}` si el pedido está vacío o si falta nombre
  o dirección. `ToolError` (en español) si, al revalidar dentro de la
  transacción, el stock ya no alcanza — caso de carrera con otra llamada
  concurrente.

**`finalizar_llamada()`**
- Precondición: `userdata.order_id` no es `None` (ya se confirmó un pedido en
  esta llamada).
- Postcondición: espera a que termine de sonar el habla del turno que la
  invocó (`ctx.speech_handle.wait_for_playout()`), agrega un colchón fijo de
  0.3s, y termina el proceso (`os._exit(0)`). No hay retorno útil para el
  agente: la llamada ya terminó.
- Fallo: `{"success": False, "message": "..."}` si no hay ningún pedido
  confirmado todavía — no cierra nada en ese caso.

## 6. Modelo de datos (invariantes)

- `order_items.unit_price` es el precio en el momento del pedido, no una
  referencia al precio actual de `products` — un pedido confirmado no cambia
  de valor si el restaurante ajusta precios después.
- `orders.total` siempre es igual a `sum(order_items.quantity * order_items.unit_price)`
  para ese pedido. Se calcula en Python al confirmar, no se recalcula después.
- El stock de un producto nunca debe quedar negativo. Se protege con
  `SELECT ... FOR UPDATE` dentro de la transacción de `confirm_order`.
- `orders.customer_name` y `orders.delivery_address` son `NOT NULL`: un
  pedido confirmado siempre tiene ambos, porque `confirm_order` los exige
  como parámetros y los valida antes de insertar (ver RF-9).

## 7. Criterios de aceptación (escenarios de prueba)

Guion mínimo que cualquier cambio a `agent.py` o `tools/` debe seguir
pasando, por voz (`uv run agent.py console`) y/o contra la base directamente:

1. Preguntar *"¿qué tienen para tomar?"* → responde sin invocar ninguna tool.
2. Pedir *"un combo familiar y una gaseosa"* → dos items en el pedido, con
   precio y total correctos.
3. Pedir *"un polo asado"* (con error de transcripción) → identifica Pollo
   Asado vía `search_products`.
4. Decir *"ay no, quíteme la gaseosa"* → el item se quita; el agente no
   miente ni se traba. **(Este escenario era el bug #1 antes de esta rama.)**
5. Preguntar *"¿cuánto es el total?"* → cifra exacta, calculada, nunca
   estimada de cabeza por el LLM.
6. Pedir una cantidad mayor al stock disponible → rechazo con mensaje
   natural, sin excepción ni silencio.
7. Pedir un producto inexistente (ej. "una hamburguesa") → lo dice con
   naturalidad, sin inventar.
8. Confirmar el pedido → el agente pregunta nombre y dirección antes de
   usar `confirm_order` (no los asume aunque se hayan mencionado antes),
   repite ambos juntos y espera confirmación explícita antes de llamar la
   tool; se guarda en `orders`/`order_items` con
   `customer_name`/`delivery_address`, el stock se descuenta, el agente
   informa un tiempo de entrega estimado, y una segunda consulta a la base
   refleja exactamente lo pedido.
   **(Este escenario era el bug reportado en vivo: el agente confirmaba sin
   pedir nombre ni dirección — corregido haciendo ambos parámetros
   obligatorios de `confirm_order` y exigiendo en el prompt que se repitan
   juntos y se confirmen antes de llamarla.)**
9. Al repetir nombre y dirección juntos, decir *"no, el nombre está mal, es
   [apellido]"* → el agente corrige el dato y vuelve a repetir la
   confirmación con el valor corregido, sin llamar a `confirm_order` todavía.
10. Después de confirmar, decir *"no, eso es todo, gracias"* → el agente se
    despide y, en el mismo turno, cierra la llamada (`finalizar_llamada`) sin
    cortar el audio de la despedida a mitad de frase.

La suite automática usada para verificar 4, 6, 7 y 8 contra Postgres real
(sin voz) vive fuera del repo, en el scratchpad de la sesión que hizo el
cambio; no está commiteada porque depende de un stub de `RunContext` pensado
para debug puntual, no para CI. Si se necesita una suite de regresión real,
es trabajo pendiente (ver § 9).

## 8. Decisiones de producto ya tomadas (no reabrir sin pedirlo explícitamente)

- La voz no se cambia (RNF-2).
- El menú vive en el prompt, no en una tool — mientras siga siendo un menú
  pequeño y estable dentro de una misma llamada.
- `gpt-4.1-mini` sigue siendo el LLM por defecto aunque el benchmark mostró
  candidatos más rápidos; cambiarlo es una decisión pendiente de quien
  compare calidad de respuesta, no solo latencia.
- Ningún dato de reservas ni estados de cocina se agrega a propósito en
  esta fase — mantiene el alcance chico y la demo enfocada. Nombre y
  dirección de entrega sí se capturan (RF-9): son mínimos para que el
  pedido sea entregable, no un dato "de más".
- El tiempo de entrega (30 min) es un estimado fijo de la demo, no un
  cálculo real; cambiarlo por una lógica real de logística es trabajo
  pendiente si se retoma el proyecto en serio (ver § 9).

## 9. Fuera de alcance, pero ya discutido — próximos pasos si se retoma

Estos temas se conversaron en profundidad antes de este spec, y las
decisiones/hallazgos quedan resumidos aquí para no tener que re-investigarlos:

- **Telefonía real (Colombia).** Requiere un trunk SIP (Claro/Movistar/Tigo
  ya ofrecen troncal SIP empresarial con NIT) o portar el número a un
  proveedor SIP. `LiveKit Phone Numbers` (números propios de LiveKit) es
  US-only e inbound-only — no sirve para Colombia. Antes de prometerle esto
  a un cliente, hay que confirmar: tipo de número (móvil vs. fijo),
  contrato (persona natural vs. empresarial con NIT), si es el mismo número
  de WhatsApp Business (no portarlo sin verificar primero), y cuántas
  llamadas simultáneas necesita atender — un celular normal solo atiende una
  a la vez, lo cual anula buena parte del valor del agente en hora pico.
- **Aviso legal / Ley 1581 de 2012.** Ya existe el flag `AVISO_LEGAL` para
  activar el aviso de asistente virtual/grabación en el saludo; falta
  confirmar con un abogado si además se requiere registro ante la SIC.
- **Portal de pedidos.** La arquitectura ya soporta esto sin cambios: el
  portal sería otro cliente leyendo la misma Postgres (o Supabase, si se
  migra por realtime/dashboard gratis). No se requiere una API intermedia.
- **Captura del número del cliente.** Ya implementada de forma no bloqueante
  (`agent.py:_capturar_telefono_sip`); solo falta que exista una llamada SIP
  real para ejercitarla.
