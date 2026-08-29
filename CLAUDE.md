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

Agente de voz en español (LiveKit Agents) que atiende la recepción telefónica
de un hotel: consulta disponibilidad y toma reservas. Es una **demo
comercial**: la prioridad es que la conversación se sienta fluida y natural,
no acumular funcionalidades.

El repo tiene tres ramas: `main` (código original), `pedidos` (la demo de
toma de pedidos de un restaurante) y `reservas` (esta, la del hotel).
`pedidos` y `reservas` comparten arquitectura e interfaz web; si arreglas
algo estructural en una, probablemente aplica también en la otra.

Antes de trabajar en el código, lee:
- [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) — cómo funciona el sistema hoy y por qué.
- [`docs/SPEC.md`](docs/SPEC.md) — qué debe cumplir el sistema (requisitos, contratos de
  las tools, criterios de aceptación). Si vas a cambiar comportamiento, actualiza este
  archivo también.

Convenciones ya establecidas en el código, para mantener consistencia:
- Comentarios y strings de cara al usuario en español.
- La voz del TTS (`aura-2` / `celeste` / `es-CO`) es una decisión de producto
  ya tomada: no se cambia salvo instrucción explícita y nueva.
- El estado de una reserva vive en `session.userdata`, nunca en una variable
  global de módulo (ver `docs/ARQUITECTURA.md` § Estado de la reserva).
- Los errores de negocio esperables (tipo de habitación inexistente, sin
  cupo para esas fechas) se
  devuelven como `{"success": False, "message": "..."}`, no como excepciones.
  Solo se usa `ToolError` (nunca una excepción genérica) para el caso
  inesperado que sí debe llegar al LLM como mensaje en español.
