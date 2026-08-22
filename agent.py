import logging
import os

from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, room_io, inference
from livekit.agents.voice.events import ConversationItemAddedEvent
from livekit.plugins import noise_cancellation, silero

from tools.products import search_products, build_menu_prompt_block
from tools.orders import (
    PedidoEnCurso,
    add_item_to_order,
    set_item_quantity,
    vaciar_pedido,
    confirm_order,
)

load_dotenv()

logger = logging.getLogger("agent")

# Modelos configurables por .env para poder comparar variantes sin tocar
# codigo (ver scripts/bench_llm.py para elegir LLM_MODEL con datos).
#
# La voz (TTS) NO esta aqui a proposito: aura-2 / celeste / es-CO se deja
# fija en el codigo, mas abajo. No se toca bajo ningun motivo.
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4.1-mini")
STT_MODEL = os.getenv("STT_MODEL", "deepgram/flux-general-multi")

# Aviso de asistente virtual / grabacion que exige la Ley 1581 de 2012 en
# telefonia real. Apagado en la demo para no alargar el saludo; se activa
# con AVISO_LEGAL=true el dia que esto atienda llamadas de verdad.
AVISO_LEGAL = os.getenv("AVISO_LEGAL", "false").lower() == "true"

SALUDO = "¡Hola! Bienvenido, soy el asistente de pedidos. ¿Qué le gustaría ordenar hoy?"
if AVISO_LEGAL:
    SALUDO = (
        "¡Hola! Bienvenido. Le informo que esta llamada es atendida por un "
        "asistente virtual y puede ser grabada para mejorar el servicio. "
        "¿Qué le gustaría ordenar hoy?"
    )


def _build_turn_pipeline():
    """Arma el STT y la deteccion de turno segun STT_MODEL.

    Camino por defecto (deepgram/flux-*): Flux hace transcripcion y
    deteccion de fin de turno en el mismo modelo (<400ms), asi que se usa
    turn_detection="stt" y un endpointing bien ajustado (el default de
    0.5/3.0s es para el modo VAD clasico, no para Flux).

    Camino de respaldo: si STT_MODEL apunta a un modelo Deepgram clasico
    (ej. "deepgram/nova-2"), se vuelve al detector de turno separado
    (MultilingualModel). El import queda dentro de esta rama para no
    cargar un plugin deprecado cuando no se usa.
    """
    if STT_MODEL.startswith("deepgram/flux"):
        stt_component = inference.STT(model=STT_MODEL, language="es")
        turn_detection = "stt"
        endpointing = {"min_delay": 0.1, "max_delay": 2.5}
    else:
        from livekit.plugins.turn_detector.multilingual import MultilingualModel

        stt_component = f"{STT_MODEL}:es"
        turn_detection = MultilingualModel()
        endpointing = {"min_delay": 0.3, "max_delay": 2.5}

    return stt_component, turn_detection, endpointing


# Define your agent's behavior by extending the Agent class
class Assistant(Agent):
    def __init__(self, menu_text: str) -> None:
        super().__init__(
            instructions=f"""
            Eres una tomadora de pedidos de una cadena de restaurantes de pollo.
            Tu función principal es atender a los clientes por teléfono y ayudarlos a realizar sus pedidos.

            MENU (esto es todo lo que existe; no ofrezcas ni inventes nada fuera de esta lista):
            {menu_text}

            OBJETIVO:
            - Escuchar atentamente al cliente.
            - Identificar los productos que desea.
            - Registrar mentalmente los productos y cantidades solicitadas.
            - Preguntar por cualquier información necesaria para completar el pedido.
            - Confirmar el pedido con el cliente antes de finalizarlo.
            - Ser clara, natural y concisa.

            FORMA DE HABLAR:
            - Habla siempre en español.
            - Utiliza un tono amable y profesional.
            - Respuestas cortas: 1-2 frases por turno, como en una llamada real.
            - Nunca leas el menú completo de corrido; menciona 2-3 opciones relevantes y pregunta.
            - No hagas preguntas innecesarias.
            - Haz una pregunta a la vez.
            - No repitas información innecesariamente.

            PEDIDOS:
            - El menú de arriba ya lo conoces: para preguntas generales o por categoría
              ("qué bebidas tienen", "qué combos manejan") respóndelas directo, sin usar
              ninguna herramienta.
            - Nunca inventes productos, precios, disponibilidad o información del restaurante.
            - Cuando el cliente termine de realizar su pedido, repite los productos y cantidades para confirmar que sean correctos.
            - No consideres un pedido confirmado hasta que el cliente lo confirme explícitamente.
            - Cuando el cliente confirme, usa la herramienta confirm_order para guardar el pedido.
            - Si el cliente se corrige o cambia de opinión (ej. "quíteme la gaseosa",
              "mejor que sean tres", "cambie eso"), usa set_item_quantity con la
              cantidad final que debe quedar (0 para quitar el producto por completo).
            - Si el cliente pide cancelar o empezar de nuevo, usa vaciar_pedido.

            BÚSQUEDA DE PRODUCTOS:
            - Si el cliente nombra o describe un producto específico y necesitas confirmar
              su id exacto antes de agregarlo, usa search_products.
            - search_products también tolera errores de transcripción de voz; si el
              cliente repite o corrige lo que dijo, intenta de nuevo con el texto corregido.
            - Si search_products no encuentra nada, dilo con naturalidad en vez de inventar
              un producto.
            - Si hay varias coincidencias razonables, pregunta al cliente cuál quiere
              en vez de asumir.

            IMPORTANTE:
            - Tu función es tomar pedidos, no mantener conversaciones generales.
            - Si el cliente se desvía del proceso de pedido, intenta llevar la conversación nuevamente hacia el pedido.
            """,
            tools=[
                search_products,
                add_item_to_order,
                set_item_quantity,
                vaciar_pedido,
                confirm_order,
            ],
        )


server = AgentServer()


def _registrar_metricas_de_turno(session: AgentSession) -> None:
    """Loguea la latencia real de cada turno (ver README para leerlas).

    e2e_latency es el numero que importa para la demo: tiempo entre que el
    cliente dejo de hablar y el agente empezo a responder. Sirve para
    comparar objetivamente esta rama contra main con el mismo guion de
    prueba, en vez de fiarse de la sensacion de "se sintio mas fluido".
    """

    def _on_item_added(ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        if getattr(item, "type", None) != "message":
            return

        metrics = getattr(item, "metrics", None) or {}
        if item.role == "user":
            logger.info(
                "[latencia] usuario  eot_delay=%.0fms  transcripcion=%.0fms",
                metrics.get("end_of_turn_delay", 0.0) * 1000,
                metrics.get("transcription_delay", 0.0) * 1000,
            )
        elif item.role == "assistant":
            logger.info(
                "[latencia] agente   e2e=%.0fms  llm_ttft=%.0fms  tts_ttfb=%.0fms",
                metrics.get("e2e_latency", 0.0) * 1000,
                metrics.get("llm_node_ttft", 0.0) * 1000,
                metrics.get("tts_node_ttfb", 0.0) * 1000,
            )

    session.on("conversation_item_added", _on_item_added)


def _capturar_telefono_sip(session: AgentSession[PedidoEnCurso], room: rtc.Room) -> None:
    """Guarda el numero del cliente en userdata cuando la llamada es SIP.

    No bloquea el arranque: revisa a los participantes ya conectados y se
    suscribe a los que lleguen despues. En console/playground no hay
    participante SIP, asi que customer_phone simplemente queda en None.
    """

    def _revisar(participant: rtc.RemoteParticipant) -> None:
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            phone = participant.attributes.get("sip.phoneNumber")
            if phone:
                session.userdata.customer_phone = phone

    for p in room.remote_participants.values():
        _revisar(p)
    room.on("participant_connected", _revisar)


# The entrypoint function runs when a participant joins the room
@server.rtc_session()
async def entrypoint(ctx: JobContext):
    # Una sola consulta a Postgres al arrancar la sesion, en vez de una
    # tool (get_menu) que el agente tendria que invocar y esperar en medio
    # de la conversacion. De paso, esta llamada crea el pool de conexiones
    # (singleton en database/connection.py), asi que la primera tool real
    # de la llamada ya no paga ese costo de arranque.
    menu_text = await build_menu_prompt_block()

    stt_component, turn_detection, endpointing = _build_turn_pipeline()

    session = AgentSession[PedidoEnCurso](
        userdata=PedidoEnCurso(),
        stt=stt_component,
        llm=LLM_MODEL,
        # La voz no se toca: aura-2 / celeste / es-CO tal cual estaba.
        tts=inference.TTS(
            model="deepgram/aura-2",
            voice="celeste",
            language="es-CO",
        ),
        vad=silero.VAD.load(),
        turn_handling={
            "turn_detection": turn_detection,
            "endpointing": endpointing,
            # Arranca la sintesis de voz antes de que el turno se confirme
            # del todo: esconde buena parte de la latencia de aura-2 sin
            # cambiar la voz.
            "preemptive_generation": {"enabled": True, "preemptive_tts": True},
            # Que un "ajá"/"sí" del cliente no corte al agente a mitad de
            # frase; muy notorio en español si no se ajusta.
            "interruption": {"mode": "adaptive", "min_duration": 0.4},
        },
    )

    _registrar_metricas_de_turno(session)

    await session.start(
        agent=Assistant(menu_text=menu_text),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVC(),
            ),
        ),
    )

    _capturar_telefono_sip(session, ctx.room)

    # Saludo instantaneo: session.say() reproduce texto fijo directo, sin
    # el roundtrip de LLM que tendria generate_reply(). Es el primer
    # momento que escucha el comprador, asi que es el que mas rinde.
    await session.say(SALUDO)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agents.cli.run_app(server)
