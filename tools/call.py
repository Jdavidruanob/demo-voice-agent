import asyncio
import logging

from livekit.agents import RunContext, function_tool, get_job_context

from tools.reservas import ReservaEnCurso

logger = logging.getLogger("agent")

# Margen despues de wait_for_playout() y antes de cerrar la sala.
#
# wait_for_playout() resuelve cuando el agente termino de ENTREGAR el audio al
# servidor (AudioSource.wait_for_playout espera a que se drene su cola), no
# cuando el cliente termino de OIRLO: entre medio hay red y el jitter buffer
# del navegador, que van unos cientos de ms atras. Sin este margen la
# despedida se corta justo al final en una llamada por web, aunque en consola
# (donde el audio sale por el dispositivo local, sin red) se oiga completa.
_MARGEN_CIERRE_SEGUNDOS = 1.5


@function_tool
async def finalizar_llamada(ctx: RunContext[ReservaEnCurso], despedida: str):
    """Dice tu despedida al cliente y cierra la llamada.

    Usa esta herramienta SOLO como el ultimo paso de la conversacion:
    despues de que la reserva ya se guardo con crear_reserva y el cliente
    dijo explicitamente que no necesita nada mas.

    Args:
        despedida: La frase completa con la que te despides, tal cual quieres
            que el cliente la escuche (ej. "Listo, lo esperamos el 10 de
            septiembre. Muchas gracias por llamar, que tenga buen dia."). La
            dice esta herramienta, asi que NO la digas ademas por tu cuenta:
            seria decirla dos veces.
    """

    reserva = ctx.userdata

    if reserva.reserva_id is None:
        return {
            "success": False,
            "message": (
                "Todavia no se ha confirmado ninguna reserva en esta llamada. "
                "No cierres la llamada todavia."
            ),
        }

    # La despedida la dice la tool, no el turno del LLM, porque el modelo
    # tiende a encadenar crear_reserva -> finalizar_llamada en un mismo turno
    # sin hablar: el cliente se quedaba sin oir despedida alguna. Pasandola
    # como argumento sigue siendo el modelo quien la redacta (natural y
    # variada), pero ya no puede saltarsela.
    #
    # El handle de say() es distinto del handle de esta tool, asi que
    # esperarlo no crea la espera circular que si crearia
    # ctx.speech_handle.wait_for_playout().
    handle = ctx.session.say(despedida)
    await handle.wait_for_playout()
    await asyncio.sleep(_MARGEN_CIERRE_SEGUNDOS)

    try:
        job_ctx = get_job_context()
    except RuntimeError:
        # Fuera de un job (por ejemplo en una prueba que instancia la sesion
        # a mano) no hay sala que cerrar: la despedida ya se dijo, que es lo
        # que importa.
        logger.warning("[llamada] sin JobContext: no hay sala que cerrar")
        return None

    logger.info("[llamada] cerrando la sala tras confirmar la reserva #%s", reserva.reserva_id)

    # Cerrar la sala, no matar el proceso. delete_room desconecta a todos los
    # participantes, asi que el navegador recibe el evento Disconnected al
    # instante y su interfaz vuelve sola al estado inicial.
    #
    # Antes esto era os._exit(0), y tenia dos problemas en una llamada real
    # por web: cortaba el final de la despedida que todavia iba en camino, y
    # mataba el subproceso sin avisarle al servidor, asi que el navegador se
    # quedaba "en llamada" (mostrando el micro activo) hasta que LiveKit
    # notara el timeout del participante.
    await job_ctx.delete_room()
    return None
