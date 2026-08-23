import asyncio
import logging
import os

from livekit.agents import RunContext, function_tool

from tools.orders import PedidoEnCurso

logger = logging.getLogger("agent")

# Colchon de seguridad despues de wait_for_playout(), por si el dispositivo
# de salida de audio tiene su propio buffer mas alla de lo que la libreria
# reporta como "ya sono". No es una espera larga a proposito.
_MARGEN_CIERRE_SEGUNDOS = 0.3


@function_tool
async def finalizar_llamada(ctx: RunContext[PedidoEnCurso]):
    """Cierra la llamada despues de despedirte del cliente.

    Usa esta herramienta SOLO como el ultimo paso de la conversacion:
    despues de que el pedido ya se confirmo con confirm_order y el cliente
    dijo explicitamente que no necesita nada mas. Di tu despedida en el
    MISMO turno en el que llamas a esta herramienta (no la llames en un
    turno aparte, despues de ya haberte despedido): la herramienta espera
    a que termine de sonar tu despedida antes de cerrar la llamada, asi
    que no hay riesgo de cortarte a mitad de frase.
    """

    pedido = ctx.userdata

    if pedido.order_id is None:
        return {
            "success": False,
            "message": (
                "Todavia no se ha confirmado ningun pedido en esta llamada. "
                "No cierres la llamada todavia."
            ),
        }

    # ctx.speech_handle es el habla del turno que disparo esta tool (la
    # despedida), no una llamada global: esperar su reproduccion es lo que
    # garantiza que el cliente ya la escucho completa antes de cortar.
    await ctx.speech_handle.wait_for_playout()
    await asyncio.sleep(_MARGEN_CIERRE_SEGUNDOS)

    logger.info("[llamada] cerrando proceso tras confirmar pedido #%s", pedido.order_id)

    # Salida dura a proposito, no un apagado ordenado de la sesion:
    # - En `agent.py console`, termina el propio proceso, que es justo lo
    #   que se pidio (que la terminal se cierre sola al despedirse).
    # - En `dev`/produccion cada llamada corre en su propio subproceso
    #   (JobExecutorType.PROCESS por defecto de LiveKit Agents), asi que
    #   esto solo cuelga ESA llamada; el worker sigue atendiendo las demas.
    os._exit(0)
