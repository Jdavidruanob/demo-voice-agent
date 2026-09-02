import logging
import os

from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    AudioConfig,
    BackgroundAudioPlayer,
    JobContext,
    room_io,
    inference,
)
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

# Nombre del restaurante que se dice en el saludo y en el prompt.
# Configurable por .env para no tener que tocar codigo por un cambio de marca.
RESTAURANT_NAME = os.getenv("RESTAURANT_NAME", "Macadamia")

# Aviso de asistente virtual / grabacion que exige la Ley 1581 de 2012 en
# telefonia real. Apagado en la demo para no alargar el saludo; se activa
# con AVISO_LEGAL=true el dia que esto atienda llamadas de verdad.
AVISO_LEGAL = os.getenv("AVISO_LEGAL", "false").lower() == "true"

# Ruido de sala de fondo en bucle (bandeja, murmullo de restaurante) para que
# la llamada no suene a silencio digital perfecto. Subido de 0.07 a 0.12 a
# pedido explicito del dueno del producto: el ambiente se sentia casi
# inaudible; sigue quedando por debajo del TTS y sin tapar lo que capta el
# STT del cliente, pero ahora se percibe claramente como "sonido de
# restaurante" en vez de un fondo casi plano.
AMBIENCE_AUDIO_PATH = "assets/restaurant_ambience.wav"
AMBIENCE_VOLUME = 0.12

SALUDO = f"¡Hola! Bienvenido a Restaurante {RESTAURANT_NAME}, ¿qué le gustaría ordenar hoy?"
if AVISO_LEGAL:
    SALUDO = (
        f"¡Hola! Bienvenido a Restaurante {RESTAURANT_NAME}. Le informo que esta "
        "llamada es atendida por un asistente virtual y puede ser grabada para "
        "mejorar el servicio. ¿Qué le gustaría ordenar hoy?"
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
            Eres la toma-pedidos telefónica del restaurante {RESTAURANT_NAME}. Tu objetivo
            es tomar pedidos de forma rápida, amable y muy natural.

            {menu_text}

            ESTILO Y TONO DE VOZ:
            - Respuestas de máximo 15 a 20 palabras por turno para mantener un ritmo
              telefónico fluido.
            - Usa muletillas humanas de forma sutil al inicio o mitad de frase (ej.
              "eh...", "a ver...", "mira,", "listo,", "perfecto..."), de forma ocasional
              y variada, no en cada turno -- usarla siempre suena mecánico.
            - Incluye comas y puntos suspensivos (...) para forzar pausas breves al
              hablar: tu texto llega tal cual al TTS, sin filtrar puntuación, así que
              úsala con intención.
            - Pluraliza y habla de forma natural (ej. "2 lasagnas mixtas", "3 ejecutivos
              de carne asada"; NUNCA digas "2 de lasagna" o "1 de arroz").
            - Hablas, no escribes: no uses listas, viñetas ni formato escrito.
            - De vez en cuando suma un matiz vocal corto y natural ("mmm..." al pensar,
              una risa suave "jajaja" si el cliente dice algo gracioso, "ahhh ya" al caer
              en cuenta de algo). Ocasional y variado, un par de veces en toda la llamada
              basta.

            REGLAS DE DOMINIO DEL MENÚ:
            - Si el cliente pide un Almuerzo Ejecutivo, confirma la proteína elegida y
              recuerda que incluye arroz blanco, papa a la francesa, ensalada y sopa.
              Además pregúntale el principio (frijoles, lentejas o pasta) antes de
              agregarlo al pedido -- nunca lo asumas.
            - Si piden Especiales, verifica si el día actual coincide (Ajiaco solo
              miércoles, Bandeja Paisa solo viernes); el menú de arriba ya te dice si
              cada especial está disponible hoy. Si no coincide, dilo con naturalidad y
              ofrece las otras opciones del menú.
            - Nunca inventes platos, precios o disponibilidad fuera del menú.

            UBICACIÓN:
            - Si el cliente pregunta por la ubicación o dirección del restaurante,
              responde que {RESTAURANT_NAME} está ubicado frente a la Universidad
              Javeriana Cali.

            FLUJO DE ATENCIÓN:
            1. Saludo breve y cálido mencionando "Restaurante {RESTAURANT_NAME}" (ya lo
               hiciste al iniciar la llamada).
            2. Tomar el pedido producto por producto, una pregunta a la vez.
            3. Solicitar el nombre de quien hace el pedido y la dirección de entrega.
            4. Confirmar el resumen del pedido y finalizar.

            TONO Y LATENCIA CONVERSACIONAL:
            - Cuando vayas a usar una herramienta (buscar un plato, agregarlo, confirmar
              el pedido) puedes usar una frase corta de transición antes, como lo haría
              una persona real revisando algo (ej. "A ver, dame un segundo...", "Anota
              esto...", "Déjame confirmo..."). Varía la frase; no la repitas siempre.
              Es ocasional, no en cada turno ni en respuestas simples y directas.

            ESCUCHA ACTIVA (BACKCHANNELING):
            - Cuando te toque hablar y el cliente claramente sigue enumerando platos o a
              mitad de una explicación (una pausa corta para pensar, no terminó la
              frase), no lances una pregunta ni una respuesta larga: responde con un
              backchannel breve ("ajá", "sí", "listo", "dale") y deja que continúe.
              Reserva las respuestas completas para cuando realmente terminó de decir lo
              que quería.

            PEDIDOS:
            - El menú de arriba ya lo conoces: para preguntas generales o por categoría
              respóndelas directo, sin usar ninguna herramienta.
            - Cuando el cliente termine de realizar su pedido, repite los platos y
              cantidades para confirmar que sean correctos, con la naturalidad de arriba.
            - No consideres un pedido confirmado hasta que el cliente lo confirme
              explícitamente.
            - Antes de usar confirm_order, SIEMPRE pregunta nombre y dirección de entrega
              si aún no los tienes (uno a la vez, no los dos juntos). No los des por
              sentado ni los inventes, aunque el cliente ya haya mencionado algo parecido
              antes.
            - Al confirmar, dile al cliente que su pedido llega en aproximadamente
              30 minutos (la tool ya te lo recuerda en su respuesta; repítelo con tus
              palabras).
            - Si el cliente se corrige o cambia de opinión, usa set_item_quantity con la
              cantidad final que debe quedar (0 para quitar el plato por completo).
            - Si el cliente pide cancelar o empezar de nuevo, usa vaciar_pedido.
            - Si el cliente nombra o describe un plato específico y necesitas confirmar
              su id exacto antes de agregarlo, usa search_products; si no encuentra nada,
              dilo con naturalidad en vez de inventar un plato.

            IMPORTANTE:
            - Tu función es tomar pedidos, no mantener conversaciones generales. Si el
              cliente se desvía, lleva la conversación amablemente de vuelta al pedido.
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
@server.rtc_session(agent_name="agente-macadamia")
async def entrypoint(ctx: JobContext):
    # Una sola consulta a Postgres al arrancar la sesion, en vez de una
    # tool que el agente tendria que invocar y esperar en medio de la
    # conversacion solo para listar el menu, que no cambia durante la
    # llamada. De paso, esta llamada crea el pool de conexiones (singleton
    # en database/connection.py), asi que la primera tool real de la
    # llamada ya no paga ese costo de arranque.
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
        # min_speech_duration y activation_threshold subidos del default (0.05s /
        # 0.5) para que un ruido de fondo o una muletilla corta del cliente no
        # corte el audio del agente a mitad de frase.
        vad=silero.VAD.load(
            min_speech_duration=0.35,
            activation_threshold=0.6,
        ),
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

    # Pista de audio de fondo independiente de la del agente: BackgroundAudioPlayer
    # crea su propio AudioSource/LocalAudioTrack, publica en la sala y hace el loop
    # del wav sin bloquear el event loop (decodifica y resamplea via ffmpeg/av).
    # ctx.add_shutdown_callback asegura que se cierre y despublique al colgar,
    # incluso si la llamada termina de forma abrupta.
    background_audio = BackgroundAudioPlayer(
        ambient_sound=AudioConfig(AMBIENCE_AUDIO_PATH, volume=AMBIENCE_VOLUME),
    )
    await background_audio.start(room=ctx.room, agent_session=session)
    ctx.add_shutdown_callback(background_audio.aclose)

    _capturar_telefono_sip(session, ctx.room)

    # Saludo instantaneo: session.say() reproduce texto fijo directo, sin
    # el roundtrip de LLM que tendria generate_reply(). Es el primer
    # momento que escucha el comprador, asi que es el que mas rinde.
    await session.say(SALUDO)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agents.cli.run_app(server)
