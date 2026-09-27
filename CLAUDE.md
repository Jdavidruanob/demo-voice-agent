# Instrucciones para Claude Code en este repo

## Convenciones de Git — regla estricta, sin excepciones

**Nunca te agregues como coautor ni colaborador en ningún commit de este
repositorio.** Concretamente:

- No incluyas líneas `Co-Authored-By: Claude ...` (ni ninguna variante) en
  ningún mensaje de commit, aunque sea la plantilla por defecto de la
  herramienta.
- No cambies el autor ni el committer del commit: deben quedar siempre con
  la identidad de git ya configurada localmente (`git config user.name` /
  `user.email`), nunca con un nombre o correo tuyo.
- No te agregues como colaborador del repositorio en GitHub
  (`collaborators`, invitaciones, etc.) bajo ningún motivo.
- Si en algún momento detectas un commit existente con coautoría o rastro
  tuyo, avisa y ofrece corregirlo (`git commit --amend` + push con
  `--force-with-lease`), pero nunca lo agregues de entrada.

Esto aplica siempre que hagas un `git commit` en este proyecto, sin
necesidad de que te lo recuerden cada vez.

## Sobre este proyecto

Agente de voz en español (LiveKit Agents) que toma pedidos para **Brasa & Pan**
(rama `brasa-y-pan`). Es una **demo comercial**: la prioridad es que la
conversación se sienta fluida y natural, no acumular funcionalidades.

**Este repo no tiene base de datos.** El catálogo y los pedidos viven en
`demo-delivery-system` (`~/code/demo-delivery-system`) y se consultan por HTTP
desde `brasa/api.py`. Un pedido cerrado hablando aparece en el portal de ese
sistema. La decisión de fondo —por qué se permite que un pedido nazca de una
conversación— está en su `docs/DECISIONS.md`, ADR-13.

Antes de trabajar en el código, lee:
- [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) — cómo funciona el sistema hoy y por qué.
- [`docs/SPEC.md`](docs/SPEC.md) — qué debe cumplir el sistema (requisitos, contratos de
  las tools, criterios de aceptación). Si vas a cambiar comportamiento, actualiza este
  archivo también.

Convenciones ya establecidas en el código, para mantener consistencia:
- Comentarios y strings de cara al usuario en español.
- La voz del TTS (`aura-2` / `celeste` / `es-CO`) es una decisión de producto
  ya tomada: no se cambia salvo instrucción explícita y nueva.
- El estado de un pedido vive en `session.userdata`, nunca en una variable
  global de módulo (ver `docs/ARQUITECTURA.md` § Estado del pedido).
- Los errores de negocio esperables (producto inexistente, agotado, falta el
  término de la carne) se devuelven como `{"success": False, "message": "..."}`,
  no como excepciones. Solo se usa `ToolError` (nunca una excepción genérica)
  para el caso inesperado que sí debe llegar al LLM como mensaje en español.

Tres reglas propias de esta rama:

- **El agente nunca manda una cifra al sistema de pedidos.** Solo SKUs, ids de
  opción y cantidades; el precio y el total los resuelve el servidor. Es lo que
  hace aceptable que un pedido nazca de una llamada.
- **Un fallo nunca se convierte en una promesa.** Si el pedido no se guardó, al
  cliente se le dice eso y se le da el WhatsApp del restaurante. Jamás "ya
  quedó".
- **`AGENT_NAME` está en dos archivos** (`agent.py` y `web/main.py`) y tienen
  que ser idénticos. Si no, la sala queda vacía **sin ningún error**. Corre
  `uv run python scripts/verify_contrato.py` antes de cualquier commit.
