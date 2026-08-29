# Spec del producto

Spec en el sentido de "spec-driven development": la referencia que describe
**qué debe hacer el sistema y bajo qué condiciones se considera correcto**,
independiente de cómo esté implementado hoy. Cuando el código y este
documento no coincidan, uno de los dos está desactualizado — corrígelo antes
de seguir construyendo encima. Ver `docs/ARQUITECTURA.md` para el cómo.

## 1. Objetivo

Un agente de voz en español que le muestre a un comprador potencial (dueño de
hotel) que un bot puede atender la recepción telefónica **de forma fluida y
natural**, sin sonar robótico ni tener pausas incómodas. La fluidez es la
métrica de éxito de esta fase, por encima de cobertura de funcionalidades.

## 2. Alcance de esta fase (demo comercial)

**Dentro de alcance:**
- Consultar disponibilidad de un catálogo fijo y pequeño de tipos de
  habitación (6, con descripción/amenidades) para un rango de fechas y
  número de huéspedes.
- Informar tarifa por noche y total de la estadía.
- Responder preguntas generales sobre el hotel (check-in/checkout,
  desayuno, wifi, parqueadero, piscina, mascotas) desde un catálogo fijo.
- Confirmar y persistir una reserva.
- Sonar fluido: sin silencios muertos, sin cortes de turno torpes, primera
  respuesta instantánea.

**Fuera de alcance (explícito, no es un olvido):**
- Múltiples sedes/hoteles.
- Cancelar o modificar una reserva ya confirmada (la columna `estado` en
  `reservas` deja el campo listo, pero no hay tool para usarlo).
- Check-in / check-out real, asignación de número de habitación física, o
  seguimiento posterior a la reserva (facturación, servicios adicionales).
- Pagos.
- Portal/dashboard para el hotel (se discutió arquitectura — Postgres
  compartida entre agente y portal — pero no se construyó).
- Conexión a un número de teléfono real (troncal SIP, portabilidad).
  Discutido a fondo en la demo anterior (mismo hallazgo aplica, ver hilo de
  decisiones más abajo) pero no iniciado.
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

**RF-1 — Saludo inmediato.** Al iniciar la sesión, el agente saluda sin que
el cliente tenga que hablar primero, y sin una pausa perceptible de roundtrip
de LLM.

**RF-2 — Consulta de catálogo sin fricción.** Preguntas generales de tarifa,
capacidad o amenidades ("¿cuánto cuesta la suite?", "¿la doble para cuántas
personas es?", "¿la familiar tiene nevera?") y preguntas sobre servicios del
hotel ("¿a qué hora es el check-in?", "¿tienen piscina?", "¿aceptan
mascotas?") se responden en el mismo turno, sin necesidad de que el sistema
"vaya a buscar" nada — el catálogo de habitaciones y la info del hotel ya
están en el prompt.

**RF-3 — Identificación de fechas y huéspedes.** El sistema identifica fecha
de entrada, fecha de salida (o total de noches) y número de huéspedes antes
de consultar disponibilidad; si el cliente da la información de forma
ambigua o incompleta, pregunta en vez de asumir.

**RF-4 — Consulta de disponibilidad.** Con fechas y huéspedes identificados,
el sistema consulta disponibilidad real (no el catálogo estático) y presenta
como máximo 2 opciones de habitación, cada una con tarifa por noche y total
de la estadía.

**RF-5 — Rechazo de lo no disponible.** Si no hay habitaciones para las
fechas o el número de huéspedes pedidos, el sistema lo dice con naturalidad
y puede ofrecer intentar con otras fechas, sin inventar disponibilidad.

**RF-6 — Confirmación explícita.** Una reserva solo se considera final
cuando el cliente elige una de las opciones presentadas de forma explícita.
Nunca se persiste antes de esa elección. La presentación de opciones y la
confirmación deben sonar naturales, no como una lectura de datos: cantidades
en palabras, nombre completo del tipo de habitación, y pluralización
correcta (ej. "3 noches", "habitaciones dobles", no "3 de noche" ni
"habitación doble" al referirse a varias).

**RF-7 — Nombre y teléfono de contacto.** Antes de confirmar, el agente
siempre pregunta (uno a la vez) el nombre completo del huésped y un teléfono
de contacto — nunca los asume ni los inventa, aunque el cliente los haya
mencionado de pasada antes. `crear_reserva` los exige como parámetros
obligatorios, así que estructuralmente no puede confirmarse una reserva sin
ambos.

**RF-8 — Persistencia con integridad.** Al confirmar, la reserva se guarda
con la tarifa de esa noche congelada, y la disponibilidad se revalida de
forma consistente incluso si dos llamadas confirman al mismo tiempo sobre el
mismo tipo de habitación y fechas solapadas.

## 4. Requisitos no funcionales

**RNF-1 — Fluidez (prioridad máxima de esta fase).** El tiempo entre que el
cliente termina de hablar y el agente empieza a responder (`e2e_latency`,
ver `docs/ARQUITECTURA.md` § Observabilidad) debe mantenerse bajo, y ningún
paso intermedio (consultar disponibilidad, crear la reserva) debe introducir
un silencio perceptible sin que el agente diga algo mientras tanto. Parte de
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

**RNF-4 — Aislamiento entre llamadas.** El estado de una reserva de una
llamada nunca debe ser visible ni modificable desde otra llamada concurrente
en el mismo proceso.

**RNF-5 — Resiliencia ante fallas de base de datos.** Si Postgres no
responde (conexión caída, timeout), ninguna tool debe dejar que el agente se
caiga o quede en silencio: debe devolver un mensaje de respaldo en español
que el agente pueda decir tal cual (ver `docs/ARQUITECTURA.md` § Manejo de
errores, "Caída de Postgres").

**RNF-6 — Configurabilidad para decidir con datos.** STT y LLM deben poder
cambiarse por variable de entorno, para comparar alternativas sin editar
código (ver `scripts/bench_llm.py`). El TTS es la excepción intencional
(RNF-2).

## 5. Contrato de las tools

Formato: nombre — precondición — postcondición — modo de fallo.

**`consultar_disponibilidad(fecha_entrada: str, fecha_salida: str, num_huespedes: int)`**
- Precondición: `fecha_entrada`/`fecha_salida` en formato `AAAA-MM-DD`;
  `fecha_salida` posterior a `fecha_entrada`; `num_huespedes >= 1`.
- Postcondición: devuelve `{"disponible": bool, "opciones": [...], "resumen": str}`
  con hasta 2 tipos de habitación ordenados por precio que tengan capacidad y
  cupo suficiente para el rango de fechas (cada opción incluye `descripcion`
  para que el agente pueda dar detalle si el cliente pregunta); `resumen` es
  un texto ya pluralizado y formateado, listo para que el agente lo diga.
  Guarda `fecha_entrada`, `fecha_salida` y `num_huespedes` en `ctx.userdata`.
- Fallo: nunca lanza excepción; `disponible=False` con un mensaje en
  `resumen` si las fechas son inválidas, si no hay cupo, o si Postgres no
  responde (mensaje de respaldo, ver RNF-5).

**`crear_reserva(cliente_nombre: str, cliente_telefono: str, tipo_habitacion: str, fecha_entrada: str, fecha_salida: str, num_huespedes: int)`**
- Precondición: `cliente_nombre` y `cliente_telefono` no pueden llegar
  vacíos (se valida con `.strip()`); fechas válidas; el tipo de habitación
  debe existir y tener capacidad para `num_huespedes`.
- Postcondición: se crea una fila en `reservas` con la tarifa de esa noche
  congelada (`precio_noche`); la respuesta incluye `reserva_id`, `total` y
  `noches` para que el agente se lo confirme al cliente con sus palabras.
- Fallo: `{"success": False, ...}` si faltan datos, el tipo de habitación no
  existe, o la capacidad no alcanza. `ToolError` (en español) si, al
  revalidar dentro de la transacción, el cupo ya no alcanza — caso de
  carrera con otra llamada concurrente. Mensaje de respaldo (no excepción)
  si Postgres no responde (RNF-5).

## 6. Modelo de datos (invariantes)

- `reservas.precio_noche` es la tarifa en el momento de la reserva, no una
  referencia a la tarifa actual de `habitaciones` — una reserva confirmada
  no cambia de valor si el hotel ajusta tarifas después.
- La disponibilidad de un tipo de habitación para un rango de fechas nunca
  debe quedar negativa: `disponible - COUNT(reservas solapadas, no
  canceladas)` se revalida dentro de la transacción de `crear_reserva` antes
  de insertar (protegido con `SELECT ... FOR UPDATE`).
- `reservas.cliente_nombre` y `reservas.cliente_telefono` son `NOT NULL`:
  una reserva confirmada siempre tiene ambos, porque `crear_reserva` los
  exige como parámetros y los valida antes de insertar (ver RF-7).

## 7. Criterios de aceptación (escenarios de prueba)

Guion mínimo que cualquier cambio a `agent.py` o `tools/` debe seguir
pasando, por voz (`uv run agent.py console`) y/o contra la base directamente:

1. Preguntar *"¿cuánto cuesta la suite?"* → responde sin invocar ninguna
   tool (viene del catálogo en el prompt).
2. Pedir disponibilidad para *"del 10 al 13 de marzo, para 2 personas"* →
   el agente convierte las fechas a `AAAA-MM-DD`, llama
   `consultar_disponibilidad` y presenta máximo 2 opciones con precio por
   noche y total.
3. Elegir una de las opciones y dar nombre y teléfono → el agente pregunta
   ambos datos uno a la vez (no los asume aunque se hayan mencionado antes)
   y confirma la reserva con `crear_reserva`.
4. Pedir disponibilidad de Suite Presidencial del 10 al 15 de septiembre de
   2026, o de Familiar del 12 al 14 (fechas que las reservas de ejemplo de
   `database/schema.sql` dejan sin cupo) → rechazo con mensaje natural, sin
   excepción ni silencio, y sin inventar disponibilidad.
5. Pedir una cantidad de huéspedes mayor a la capacidad del tipo de
   habitación elegido → rechazo con mensaje natural.
6. Confirmar una reserva → se guarda en `reservas` con la tarifa correcta
   congelada, y una segunda consulta a la base refleja exactamente lo
   reservado (mismas fechas, mismo tipo de habitación, mismo cliente).
7. Simular una caída de Postgres (parar el contenedor) y consultar
   disponibilidad o crear una reserva → el agente dice el mensaje de
   respaldo en español, no se cae ni se queda en silencio (RNF-5).

No existe todavía una suite automática de regresión para este dominio nuevo
(la que existía era específica del dominio de restaurante y no aplica); es
trabajo pendiente si se retoma el proyecto en serio (ver § 9).

## 8. Decisiones de producto ya tomadas (no reabrir sin pedirlo explícitamente)

- La voz no se cambia (RNF-2).
- El catálogo de tipos de habitación vive en el prompt, no en una tool —
  mientras siga siendo un catálogo pequeño y estable dentro de una misma
  llamada. La disponibilidad por fechas sí es una tool, porque eso cambia
  durante la llamada.
- `gpt-4.1-mini` sigue siendo el LLM por defecto; cambiarlo es una decisión
  pendiente de quien compare calidad de respuesta, no solo latencia.
- Cancelar/modificar reservas, check-in/checkout y pagos se dejan fuera a
  propósito en esta fase — mantiene el alcance chico y la demo enfocada.
  Nombre y teléfono de contacto sí se capturan (RF-7): son mínimos para que
  la reserva sea contactable, no un dato "de más".

## 9. Fuera de alcance, pero ya discutido — próximos pasos si se retoma

Estos temas se conversaron en profundidad en la demo anterior (dominio de
restaurante) y las decisiones/hallazgos siguen aplicando igual aquí, porque
son de infraestructura de telefonía, no del dominio de negocio:

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
- **Portal de reservas.** La arquitectura ya soporta esto sin cambios: el
  portal sería otro cliente leyendo la misma Postgres (o Supabase, si se
  migra por realtime/dashboard gratis). No se requiere una API intermedia.
- **Captura del número del cliente.** Ya implementada de forma no bloqueante
  (`agent.py:_capturar_telefono_sip`, guarda en
  `ReservaEnCurso.customer_phone_sip`); solo falta que exista una llamada
  SIP real para ejercitarla. Es informativa: el agente igual pide un
  teléfono de contacto explícito para la reserva (RF-7).
- **Cancelar/modificar reservas.** `reservas.estado` ya deja el campo listo
  (`confirmada` por defecto, el cálculo de disponibilidad ya excluye
  `cancelada`), pero no existe ninguna tool que lo use todavía.
