# Spec del producto

Spec en el sentido de "spec-driven development": la referencia que describe
**qué debe hacer el sistema y bajo qué condiciones se considera correcto**,
independiente de cómo esté implementado hoy. Cuando el código y este
documento no coincidan, uno de los dos está desactualizado — corrígelo antes
de seguir construyendo encima. Ver `docs/ARQUITECTURA.md` para el cómo.

## 1. Objetivo

Un agente de voz en español que le muestre a un comprador potencial (dueño
de Restaurante Macadamia) que un bot puede tomar pedidos telefónicos **de
forma fluida y natural**, sin sonar robótico ni tener pausas incómodas. La
fluidez es la métrica de éxito de esta fase, por encima de cobertura de
funcionalidades.

## 2. Alcance de esta fase (demo comercial)

**Dentro de alcance:**
- Menú fijo de 19 platos en 5 categorías: LASAGNA (3), SPAGUETTI (4),
  ARROCES (3), ESPECIALES (2, uno por día específico) y ALMUERZO EJECUTIVO
  (7).
- Responder preguntas generales sobre cualquier plato (precio, composición)
  desde el menú fijo, sin necesidad de una tool.
- Buscar un plato puntual quando el cliente lo nombra de forma ambigua o con
  errores de transcripción de voz.
- Tomar pedido: agregar platos con cantidad, corregir cantidades, vaciar el
  pedido.
- Para ALMUERZO EJECUTIVO: exigir que el cliente elija principio (frijoles,
  lentejas o pasta) antes de agregarlo al pedido.
- Para ESPECIALES: verificar que el día pedido coincida con el día en que
  ese especial existe (Ajiaco solo miércoles, Bandeja Paisa solo viernes).
- Confirmar y persistir el pedido con nombre y dirección de entrega.
- Sonar fluido: sin silencios muertos, sin cortes de turno torpes, primera
  respuesta instantánea.

**Fuera de alcance (explícito, no es un olvido):**
- Múltiples sedes de Macadamia.
- Cancelar o modificar un pedido ya confirmado.
- Pagos (el pedido se confirma contra-entrega, sin cobro en la llamada).
- Portal/dashboard para el restaurante.
- Conexión a un número de teléfono real (troncal SIP, portabilidad).
  Discutido a fondo en fases anteriores del proyecto (mismo hallazgo aplica,
  ver hilo de decisiones más abajo) pero no iniciado.
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
  agente recibe el turno y el cliente claramente sigue a mitad de una
  explicación, responde con un backchannel corto en vez de una respuesta
  completa (ver `agent.py` § ESCUCHA ACTIVA).

## 3. Requisitos funcionales

Expresados como comportamiento observable, no como implementación.

**RF-1 — Saludo inmediato.** Al iniciar la sesión, el agente saluda
mencionando "Restaurante Macadamia" sin que el cliente tenga que hablar
primero, y sin una pausa perceptible de roundtrip de LLM.

**RF-2 — Consulta de menú sin fricción.** Preguntas generales de precio o
composición de un plato ("¿cuánto cuesta la lasagna mixta?", "¿qué trae el
almuerzo ejecutivo?", "¿qué arroces tienen?") se responden en el mismo
turno, sin necesidad de que el sistema "vaya a buscar" nada — el menú
completo ya está en el prompt.

**RF-3 — Búsqueda de plato puntual.** Si el cliente nombra o describe un
plato específico y el sistema necesita confirmar su id exacto antes de
agregarlo (sinónimo, error de transcripción de voz), usa `search_products`;
si no encuentra nada, lo dice con naturalidad en vez de inventar un plato.

**RF-4 — Toma de pedido.** El sistema agrega platos con cantidad al pedido
en curso, permite corregir la cantidad de un plato ya agregado (o quitarlo
por completo) y permite vaciar todo el pedido si el cliente lo pide
explícitamente.

**RF-5 — Principio del almuerzo ejecutivo.** Antes de agregar cualquier
plato de la categoría ALMUERZO EJECUTIVO al pedido, el sistema pregunta el
principio (frijoles, lentejas o pasta) y lo pasa a la tool; nunca lo asume
ni lo deja vacío. Un principio ausente o distinto de esos tres valores es
rechazado.

**RF-6 — Disponibilidad de especiales por día.** Si el cliente pide Ajiaco
o Bandeja Paisa, el sistema verifica el día actual contra el día en que ese
especial existe, y lo comunica con naturalidad si no coincide, ofreciendo el
resto del menú en vez de inventar disponibilidad.

**RF-7 — Confirmación explícita.** Un pedido solo se considera final cuando
el cliente lo confirma explícitamente. Nunca se persiste antes de esa
confirmación. La presentación del resumen y la confirmación deben sonar
naturales, no como una lectura de datos: cantidades en palabras, nombre
completo del plato, y pluralización correcta (ej. "2 lasagnas bolognesa",
"3 chuletas de cerdo", nunca "2 de lasagna" ni "3 de chuleta").

**RF-8 — Nombre y dirección de entrega.** Antes de confirmar, el agente
siempre pregunta (uno a la vez) a nombre de quién queda el pedido y la
dirección de entrega — nunca los asume ni los inventa, aunque el cliente los
haya mencionado de pasada antes. `confirm_order` los exige como parámetros
obligatorios, así que estructuralmente no puede confirmarse un pedido sin
ambos.

**RF-9 — Persistencia con integridad.** Al confirmar, el pedido se guarda
con el precio de cada plato congelado, el stock se descuenta, y la
disponibilidad se revalida de forma consistente incluso si dos llamadas
confirman al mismo tiempo sobre el mismo plato.

## 4. Requisitos no funcionales

**RNF-1 — Fluidez (prioridad máxima de esta fase).** El tiempo entre que el
cliente termina de hablar y el agente empieza a responder (`e2e_latency`,
ver `docs/ARQUITECTURA.md` § Observabilidad) debe mantenerse bajo, y ningún
paso intermedio (buscar un plato, confirmar el pedido) debe introducir un
silencio perceptible sin que el agente diga algo mientras tanto. Parte de
esto es percepción, no solo latencia real: el agente usa ocasionalmente
muletillas de transición variadas ("a ver, dame un segundo...", "déjame
confirmo...") antes de una consulta o al confirmar, para sonar como una
persona pensando en vez de un sistema respondiendo de forma instantánea y
perfecta. Es ocasional y variado a propósito (ver `agent.py` § TONO Y
LATENCIA CONVERSACIONAL) — usarlo en cada turno tendría el efecto contrario
y sonaría mecánico. Por la misma razón, el agente también suma ocasionalmente
matices vocales cortos ("mmm...", "jajaja", "ahhh ya"). El texto del LLM
llega sin filtrar al TTS (sin limpieza de puntuación ni markdown), a
propósito: los puntos suspensivos y comas son la señal que `deepgram/aura-2`
usa para variar pausas y entonación. Además, las respuestas deben mantenerse
cortas (máx. 15-20 palabras por turno) para sostener un ritmo telefónico.

**RNF-2 — Voz fija.** La voz (`aura-2` / `celeste` / `es-CO`) es una decisión
de producto ya tomada y aprobada. Ningún cambio futuro debe alterarla salvo
instrucción explícita y nueva del dueño del producto.

**RNF-3 — Idioma.** Toda interacción con el cliente, incluyendo mensajes de
error de las tools que el LLM pueda verbalizar, debe estar en español. (Esto
descarta dejar que una excepción sin capturar llegue al LLM: el mensaje
genérico de error de LiveKit Agents está en inglés — ver
`docs/ARQUITECTURA.md` § Manejo de errores.)

**RNF-4 — Aislamiento entre llamadas.** El estado de un pedido de una
llamada nunca debe ser visible ni modificable desde otra llamada concurrente
en el mismo proceso.

**RNF-5 — Resiliencia ante fallas de base de datos.** Si Postgres no
responde (conexión caída, timeout), ninguna tool debe dejar que el agente se
caiga o quede en silencio. `confirm_order` y `search_products` propagan el
error de Postgres tal cual hoy (no tienen un mensaje de respaldo fijo como
sí tenía la demo de hotel); si se retoma este proyecto en serio, agregar ese
manejo explícito (`try/except (asyncpg.PostgresError, OSError)` con un
mensaje en español) queda como pendiente — ver § 9.

**RNF-6 — Configurabilidad para decidir con datos.** STT y LLM deben poder
cambiarse por variable de entorno, para comparar alternativas sin editar
código (ver `scripts/bench_llm.py`). El TTS es la excepción intencional
(RNF-2).

## 5. Contrato de las tools

Formato: nombre — precondición — postcondición — modo de fallo.

**`search_products(query: str)`**
- Precondición: ninguna.
- Postcondición: devuelve `{"found": bool, "products": [...]}` con hasta 5
  coincidencias ordenadas por score de similitud (nombre, descripción o
  `keywords`, vía `pg_trgm` + coincidencia exacta de substring).
- Fallo: `found=False` con lista vacía si no hay coincidencias.

**`add_item_to_order(product_id: int, quantity: int, principio: str | None = None)`**
- Precondición: el plato debe existir y tener stock suficiente; si su
  categoría es `almuerzo_ejecutivo`, `principio` debe venir y ser uno de
  `frijoles`/`lentejas`/`pasta` (sin distinguir mayúsculas).
- Postcondición: agrega el plato al `PedidoEnCurso` en `ctx.userdata` (suma
  cantidad si el mismo plato con el mismo principio ya estaba); devuelve el
  resumen del pedido y el total acumulado.
- Fallo: nunca lanza excepción; `{"success": False, "message": "..."}` si el
  plato no existe, no hay stock suficiente, o el principio falta/es
  inválido para un almuerzo ejecutivo.

**`set_item_quantity(product_id: int, quantity: int)`**
- Precondición: `quantity >= 0`; si `quantity > 0` y el plato no estaba en
  el pedido, debe existir y tener stock suficiente.
- Postcondición: deja `quantity` como la cantidad final de ese plato en el
  pedido (`quantity=0` lo quita por completo).
- Fallo: `{"success": False, ...}` si `quantity < 0`, el plato no existe, no
  hay stock suficiente, o se pide quitar (`quantity=0`) un plato que no
  estaba en el pedido.

**`vaciar_pedido()`**
- Precondición: ninguna.
- Postcondición: `PedidoEnCurso.items` queda vacío. No toca la base de
  datos (el pedido nunca se había persistido).
- Fallo: no aplica.

**`confirm_order(customer_name: str, delivery_address: str)`**
- Precondición: el pedido no puede estar vacío; `customer_name` y
  `delivery_address` no pueden llegar vacíos (se valida con `.strip()`).
- Postcondición: crea una fila en `orders` y una en `order_items` por cada
  plato (con `unit_price` y `notes` congelados), descuenta `stock`, y limpia
  `PedidoEnCurso.items`. La respuesta incluye `order_id`, `total` y
  `eta_minutos` para que el agente lo confirme con sus palabras.
- Fallo: `{"success": False, ...}` si el pedido está vacío o faltan
  nombre/dirección. `ToolError` (en español) si, al revalidar dentro de la
  transacción, el stock de algún plato ya no alcanza — caso de carrera con
  otra llamada concurrente.

## 6. Modelo de datos (invariantes)

- `order_items.unit_price` es el precio del plato en el momento del pedido,
  no una referencia al precio actual de `products` — un pedido confirmado no
  cambia de valor si el restaurante ajusta precios después.
- `products.stock` nunca debe quedar negativo: se revalida dentro de la
  transacción de `confirm_order` antes de descontarlo (protegido con
  `SELECT ... FOR UPDATE`).
- `orders.customer_name` y `orders.delivery_address` son `NOT NULL`: un
  pedido confirmado siempre tiene ambos, porque `confirm_order` los exige
  como parámetros y los valida antes de insertar (ver RF-8).
- `order_items.notes` (principio del almuerzo ejecutivo) es `NULL` para
  cualquier plato que no sea de esa categoría; para los que sí lo son, ya
  fue validado como uno de `frijoles`/`lentejas`/`pasta` antes de llegar
  aquí (RF-5), así que esta columna nunca guarda un valor libre o inválido.

## 7. Criterios de aceptación (escenarios de prueba)

Guion mínimo que cualquier cambio a `agent.py` o `tools/` debe seguir
pasando, por voz (`uv run agent.py console`) y/o contra la base directamente:

1. Preguntar *"¿cuánto cuesta la lasagna mixta?"* → responde sin invocar
   ninguna tool (viene del menú en el prompt).
2. Pedir *"dos spaguettis carbonara y una lasagna bolognesa"* → el agente
   agrega ambos platos y confirma cantidades con pluralización natural
   ("2 spaguettis carbonara", no "2 de spaguetti").
3. Pedir un almuerzo ejecutivo sin decir el principio → el agente pregunta
   el principio (frijoles, lentejas o pasta) antes de agregarlo.
4. Pedir Ajiaco un día que no es miércoles (o Bandeja Paisa un día que no es
   viernes) → el agente lo dice con naturalidad y ofrece el resto del menú,
   sin inventar disponibilidad.
5. Corregir el pedido ("mejor que sean tres", "quíteme la lasagna") → el
   agente usa `set_item_quantity` con la cantidad final correcta.
6. Confirmar un pedido con nombre y dirección → el agente pregunta ambos
   datos uno a la vez (no los asume aunque se hayan mencionado antes) y
   confirma con `confirm_order`, mencionando el tiempo estimado de entrega.
7. Confirmar un pedido → se guarda en `orders`/`order_items` con los precios
   correctos congelados y el stock descontado; una segunda consulta a la
   base refleja exactamente lo pedido (mismos platos, cantidades, principio).
8. Pedir una cantidad de un plato mayor al stock disponible → rechazo con
   mensaje natural, sin inventar disponibilidad.

No existe todavía una suite automática de regresión para este menú (la que
existía era específica del dominio de hotel y no aplica); es trabajo
pendiente si se retoma el proyecto en serio (ver § 9).

## 8. Decisiones de producto ya tomadas (no reabrir sin pedirlo explícitamente)

- La voz no se cambia (RNF-2).
- El menú vive en el prompt, no en una tool — mientras siga siendo un menú
  pequeño y estable dentro de una misma llamada. `search_products` sí es una
  tool, porque confirmar el id exacto de un plato ambiguo no es algo que el
  LLM deba adivinar del texto del prompt.
- `gpt-4.1-mini` sigue siendo el LLM por defecto; cambiarlo es una decisión
  pendiente de quien compare calidad de respuesta, no solo latencia.
- Cancelar/modificar pedidos confirmados y pagos en la llamada se dejan
  fuera a propósito en esta fase — mantiene el alcance chico y la demo
  enfocada. Nombre y dirección de entrega sí se capturan (RF-8): son
  mínimos para que el pedido sea entregable, no un dato "de más".
- Los precios en `database/schema.sql` son valores de referencia inventados
  para la demo, no una lista de precios oficial de Macadamia.

## 9. Fuera de alcance, pero ya discutido — próximos pasos si se retoma

Estos temas se conversaron en profundidad en fases anteriores del proyecto y
las decisiones/hallazgos siguen aplicando igual aquí, porque son de
infraestructura de telefonía o de robustez, no del dominio de negocio:

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
- **Resiliencia ante caída de Postgres.** La demo de hotel sí implementaba
  un mensaje de respaldo fijo (`MENSAJE_FALLBACK_DB`) para no dejar que el
  agente se cayera o quedara en silencio si la base no respondía (RNF-5).
  Esta rama todavía no lo reimplementó para `tools/products.py` ni
  `tools/orders.py` — pendiente antes de un demo en vivo sin red de
  respaldo.
- **Captura del número del cliente.** Ya implementada de forma no bloqueante
  (`agent.py:_capturar_telefono_sip`, guarda en
  `PedidoEnCurso.customer_phone`); solo falta que exista una llamada SIP
  real para ejercitarla, y decidir si `confirm_order` debe exigirlo como
  parámetro obligatorio (hoy no lo hace).
- **Cancelar/modificar pedidos.** `orders.status` deja el campo listo
  (`'confirmed'` por defecto), pero no existe ninguna tool que lo use
  todavía.
