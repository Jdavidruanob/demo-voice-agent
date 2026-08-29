import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

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

from tools.habitaciones import (
    consultar_disponibilidad,
    build_catalogo_prompt_block,
    build_servicios_prompt_block,
)
from tools.reservas import ReservaEnCurso, crear_reserva
from tools.call import finalizar_llamada

load_dotenv()

logger = logging.getLogger("agent")

# Modelos configurables por .env para poder comparar variantes sin tocar
# codigo (ver scripts/bench_llm.py para elegir LLM_MODEL con datos).
#
# La voz (TTS) NO esta aqui a proposito: aura-2 / celeste / es-CO se deja
# fija en el codigo, mas abajo. No se toca bajo ningun motivo.
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4.1-mini")
STT_MODEL = os.getenv("STT_MODEL", "deepgram/flux-general-multi")

# Nombre de la recepcionista y del hotel: se hablan en el saludo y en el
# prompt. Configurables por .env para no tener que tocar codigo por un
# cambio de nombre de marca.
RECEPTIONIST_NAME = os.getenv("RECEPTIONIST_NAME", "Valentina")
HOTEL_NAME = os.getenv("HOTEL_NAME", "Hotel Colonial")

# Zona horaria con la que el agente interpreta "mañana", "el viernes", "la
# otra semana". El worker puede estar corriendo en un servidor en UTC (en
# Railway lo esta), asi que no basta con la hora local del proceso.
ZONA_HORARIA = ZoneInfo(os.getenv("ZONA_HORARIA", "America/Bogota"))

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]

# Aviso de asistente virtual / grabacion que exige la Ley 1581 de 2012 en
# telefonia real. Apagado en la demo para no alargar el saludo; se activa
# con AVISO_LEGAL=true el dia que esto atienda llamadas de verdad.
AVISO_LEGAL = os.getenv("AVISO_LEGAL", "false").lower() == "true"

# Apellidos colombianos frecuentes pero poco comunes en el ingles/generico
# con el que suelen entrenarse los modelos de STT (a los clientes les ha
# costado que el bot entienda "Ruano" o "Burbano", por ejemplo). Se pasan
# como "keyterm" a Deepgram para sesgar la transcripcion hacia ellos.
# Esto no es una lista exhaustiva ni una garantia: solo mejora la
# probabilidad para estos apellidos puntuales. Si el hotel conoce apellidos
# frecuentes de su propia clientela, vale la pena agregarlos aqui.
APELLIDOS_A_RECONOCER = [
    "Ruano",
    "Burbano",
    "Bermúdez",
    "Cifuentes",
    "Gutiérrez",
    "Marulanda",
    "Ospina",
    "Quintero",
    "Yepes",
    "Zapata",
]

# Ruido de sala de fondo en bucle (ambiente de lobby de hotel) para que la
# llamada no suene a silencio digital perfecto. Volumen deliberadamente
# bajo: nada que compita con la voz del TTS ni con el STT del cliente.
AMBIENCE_AUDIO_PATH = "assets/hotel_sonido.wav"
AMBIENCE_VOLUME = 0.08

SALUDO = f"¡Hola! Bienvenido a {HOTEL_NAME}, soy {RECEPTIONIST_NAME}. ¿En qué le puedo ayudar?"
if AVISO_LEGAL:
    SALUDO = (
        f"¡Hola! Bienvenido a {HOTEL_NAME}. Le informo que esta llamada es "
        "atendida por un asistente virtual y puede ser grabada para mejorar "
        f"el servicio. Soy {RECEPTIONIST_NAME}. ¿En qué le puedo ayudar?"
    )


def _fecha_de_hoy_texto() -> str:
    """Frase con la fecha de hoy, para inyectar en el prompt.

    Sin esto el modelo no tiene forma de resolver "mañana" o "el próximo
    viernes" a la fecha AAAA-MM-DD que exigen las tools: inventaria una, y
    la reserva quedaria guardada en una fecha equivocada sin que nadie se
    entere durante la llamada. Se calcula al iniciar cada sesion, no al
    importar el modulo, porque un worker puede quedar dias corriendo.
    """
    hoy = datetime.now(ZONA_HORARIA).date()
    return (
        f"Hoy es {_DIAS[hoy.weekday()]} {hoy.day} de {_MESES[hoy.month - 1]} "
        f"de {hoy.year}, es decir {hoy.isoformat()}."
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
        stt_component = inference.STT(
            model=STT_MODEL,
            language="es",
            extra_kwargs={"keyterm": APELLIDOS_A_RECONOCER},
        )
        turn_detection = "stt"
        endpointing = {"min_delay": 0.1, "max_delay": 2.5}
    else:
        from livekit.plugins.turn_detector.multilingual import MultilingualModel

        stt_component = inference.STT(
            model=STT_MODEL,
            language="es",
            extra_kwargs={"keyterm": APELLIDOS_A_RECONOCER},
        )
        turn_detection = MultilingualModel()
        endpointing = {"min_delay": 0.3, "max_delay": 2.5}

    return stt_component, turn_detection, endpointing


# Define your agent's behavior by extending the Agent class
class Assistant(Agent):
    def __init__(self, catalogo_text: str, servicios_text: str, fecha_hoy: str) -> None:
        super().__init__(
            instructions=f"""
            Eres {RECEPTIONIST_NAME}, la recepcionista telefónica de {HOTEL_NAME}. Tu único
            objetivo es ayudar a los clientes a consultar disponibilidad, tarifas y realizar
            reservaciones de forma rápida, amable y muy natural.

            FECHA ACTUAL: {fecha_hoy}
            Úsala para convertir lo que diga el cliente en lenguaje natural ("mañana", "el
            próximo viernes", "el puente", "del 10 al 12") a fechas concretas en formato
            AAAA-MM-DD antes de llamar cualquier herramienta. Nunca inventes el año ni
            asumas otra fecha de hoy. Si lo que dice el cliente es ambiguo (ej. "el viernes"
            cuando podría ser este o el siguiente), pregúntale en vez de adivinar.

            {catalogo_text}
            (Este catálogo es referencia rápida para preguntas generales de precio,
            capacidad o amenidades. Para confirmar disponibilidad real en fechas concretas,
            siempre usa consultar_disponibilidad; no la des por hecha solo por el catálogo.)

            {servicios_text}
            (Responde preguntas sobre estos servicios directo, sin usar ninguna herramienta.)

            ESTILO Y TONO DE VOZ:
            - Habla siempre en español.
            - Respuestas de máximo 15 a 20 palabras por turno, para mantener un ritmo
              telefónico fluido. No hagas preguntas innecesarias y haz una pregunta a la vez.
            - Usa muletillas humanas de forma sutil al inicio o mitad de frase (ej. "eh...",
              "a ver...", "mira,", "listo,", "perfecto..."), de forma ocasional y variada, no
              en cada turno -- usarla siempre suena mecánico, justo lo contrario de la idea.
            - Incluye comas y puntos suspensivos (...) para forzar pausas breves en la
              síntesis de voz: tu texto llega tal cual al TTS, sin filtrar puntuación, así que
              úsala con intención.
            - Pluraliza y habla de forma natural (ej. "2 noches", "3 habitaciones", "2
              adultos"; NUNCA digas "2 de noche" o "1 de habitación"). Usa el nombre completo
              del tipo de habitación (ej. "habitación doble", no "doble").
            - Hablas, no escribes: no uses listas, viñetas ni formato escrito.
            - De vez en cuando suma un matiz vocal corto y natural ("mmm..." al pensar, una
              risa suave "jajaja" si el cliente dice algo gracioso, "ahhh ya" al caer en
              cuenta de algo). Ocasional y variado, un par de veces en toda la llamada basta.

            FLUJO DE CONVERSACIÓN:
            1. Saludo breve y cálido (ya lo hiciste al iniciar la llamada).
            2. Identificar fechas (check-in / check-out o total de noches) y número de
               huéspedes. Convierte lo que diga el cliente a formato AAAA-MM-DD para las
               herramientas; en la voz sigue hablando de fechas de forma natural.
            3. Usa consultar_disponibilidad con esas fechas y huéspedes; presenta máximo 2
               opciones de habitación con precio por noche, con la naturalidad de arriba, no
               como una lectura de datos. Esto es obligatorio: NUNCA ofrezcas ni des por
               buena una habitación sin haber consultado disponibilidad para esas fechas
               exactas, aunque el cliente diga desde el primer momento cuál quiere.
               crear_reserva se niega a guardar si te saltaste este paso.
            4. Cuando el cliente elija una opción, solicita su nombre completo y un teléfono
               de contacto para registrar la reserva -- uno a la vez, no los dos juntos. No
               los des por sentado ni los inventes, aunque el cliente los haya mencionado de
               pasada antes.
            5. Ya con los dos datos, antes de llamar a crear_reserva repítelos JUNTOS en una
               sola frase, con las fechas, y pide confirmación explícita (ej. "Entonces la
               reserva queda a nombre de Laura Gómez, al 300 123 45 67, del 10 al 12 de
               septiembre, ¿así está bien?"). Si el cliente corrige algo (por ejemplo porque
               el nombre se escuchó mal), usa el dato corregido y vuelve a confirmar los dos
               antes de continuar.
            6. Con tipo de habitación, fechas, nombre y teléfono confirmados por el cliente,
               usa crear_reserva. Confírmale el número de reserva y el total con tus palabras.

            DISPONIBILIDAD Y RESERVAS:
            - Nunca inventes disponibilidad, tarifas ni tipos de habitación fuera del catálogo.
            - Si consultar_disponibilidad no encuentra nada para esas fechas o ese número de
              huéspedes, dilo con naturalidad y ofrece intentar con otras fechas.
            - No consideres una reserva confirmada hasta que el cliente elija una opción
              explícitamente y haya dado nombre y teléfono.
            - Si crear_reserva falla porque el cupo se agotó justo antes de confirmar, dilo
              con naturalidad y ofrece revisar otras opciones; no repitas la herramienta con
              los mismos datos esperando que cambie el resultado.

            CIERRE DE LA LLAMADA:
            - Después de usar crear_reserva, NO cierres la llamada en ese mismo turno:
              cuéntale al cliente que la reserva quedó confirmada, dile el número de reserva
              y el total, y pregúntale si necesita algo más. Espera su respuesta.
            - Solo cuando el cliente ya dijo que no necesita nada más, usa finalizar_llamada
              pasándole en el argumento "despedida" la frase con la que te despides (ej.
              "Muchas gracias por su reserva, que tenga un buen día"). Esa herramienta se
              encarga de decirla en voz alta y luego colgar, así que no escribas la despedida
              además por tu cuenta: sonaría dos veces.

            TONO Y LATENCIA CONVERSACIONAL:
            - Cuando vayas a usar una herramienta (consultar disponibilidad, crear la
              reserva) puedes usar una frase corta de transición antes, como lo haría una
              persona real revisando algo (ej. "A ver, dame un segundo...", "Déjame
              confirmo...", "Vale, reviso disponibilidad..."). Varía la frase; no la repitas
              siempre. Es ocasional, no en cada turno ni en respuestas simples y directas.

            ESCUCHA ACTIVA (BACKCHANNELING):
            - Cuando te toque hablar y el cliente claramente sigue a mitad de una explicación
              (una pausa corta para pensar, no terminó la frase), no lances una pregunta ni
              una respuesta larga: responde con un backchannel breve ("ajá", "sí", "listo",
              "dale") y deja que continúe. Reserva las respuestas completas para cuando
              realmente terminó de decir lo que quería.

            IMPORTANTE:
            - Tu función es tomar reservas y responder consultas del hotel, no mantener
              conversaciones generales. Si el cliente se desvía, lleva la conversación
              amablemente de vuelta a la reserva.
            """,
            tools=[
                consultar_disponibilidad,
                crear_reserva,
                finalizar_llamada,
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


def _capturar_telefono_sip(session: AgentSession[ReservaEnCurso], room: rtc.Room) -> None:
    """Guarda el numero del cliente en userdata cuando la llamada es SIP.

    No bloquea el arranque: revisa a los participantes ya conectados y se
    suscribe a los que lleguen despues. En console/playground no hay
    participante SIP, asi que customer_phone_sip simplemente queda en None.
    """

    def _revisar(participant: rtc.RemoteParticipant) -> None:
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            phone = participant.attributes.get("sip.phoneNumber")
            if phone:
                session.userdata.customer_phone_sip = phone

    for p in room.remote_participants.values():
        _revisar(p)
    room.on("participant_connected", _revisar)


# The entrypoint function runs when a participant joins the room
@server.rtc_session(agent_name="agente-reservas")
async def entrypoint(ctx: JobContext):
    # Una sola consulta a Postgres al arrancar la sesion, en vez de una
    # tool que el agente tendria que invocar y esperar en medio de la
    # conversacion solo para listar tipos de habitacion que no cambian
    # durante la llamada. De paso, esta llamada crea el pool de conexiones
    # (singleton en database/connection.py), asi que la primera tool real
    # de la llamada ya no paga ese costo de arranque.
    catalogo_text = await build_catalogo_prompt_block()
    servicios_text = await build_servicios_prompt_block()

    stt_component, turn_detection, endpointing = _build_turn_pipeline()

    session = AgentSession[ReservaEnCurso](
        userdata=ReservaEnCurso(),
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
        agent=Assistant(
            catalogo_text=catalogo_text,
            servicios_text=servicios_text,
            fecha_hoy=_fecha_de_hoy_texto(),
        ),
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
