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

# Aviso de asistente virtual / grabacion que exige la Ley 1581 de 2012 en
# telefonia real. Apagado en la demo para no alargar el saludo; se activa
# con AVISO_LEGAL=true el dia que esto atienda llamadas de verdad.
AVISO_LEGAL = os.getenv("AVISO_LEGAL", "false").lower() == "true"

# Apellidos colombianos frecuentes pero poco comunes en el ingles/generico
# con el que suelen entrenarse los modelos de STT (a los clientes les ha
# costado que el bot entienda "Ruano" o "Burbano", por ejemplo). Se pasan
# como "keyterm" a Deepgram para sesgar la transcripcion hacia ellos.
# Esto no es una lista exhaustiva ni una garantia: solo mejora la
# probabilidad para estos apellidos puntuales. Si el restaurante conoce
# apellidos frecuentes de su propia clientela, vale la pena agregarlos aqui.
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

# Ruido de sala de fondo en bucle (bandeja, murmullo de restaurante) para que
# la llamada no suene a silencio digital perfecto. Volumen deliberadamente
# bajo: nada que compita con la voz del TTS ni con el STT del cliente.
AMBIENCE_AUDIO_PATH = "assets/restaurant_ambience.wav"
AMBIENCE_VOLUME = 0.04

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
            - Ser breve no significa hablar como lista de datos: nunca digas cantidades
              como número crudo ("1 de gaseosa") ni acortes el nombre del producto
              ("pollo" en vez de "Pollo Asado"). Di las cantidades en palabras y usa el
              nombre completo del producto, como lo diría una persona real (ej. "un Pollo
              Asado y una Coca-Cola", no "1 de pollo y 1 de gaseosa").
            - Cuando la cantidad sea mayor a uno, pluraliza el nombre del producto en vez
              de usar la muletilla "X de [producto]" (ej. "dos Coca-Colas", "tres
              hamburguesas", "dos papas medianas"; nunca "2 de Coca-Cola" ni "2 de
              hamburguesa"). Usa "X de [producto]" solo cuando sea gramaticalmente
              indispensable porque el producto no se pluraliza solo de forma natural (ej.
              "dos botellas de agua"), no como regla general.
            - Al leer una lista de productos en voz alta (al agregar o al confirmar),
              que suene como la diría una persona real por teléfono, no como una lectura
              mecánica ítem por ítem: usa comas y "y" de forma natural entre los
              productos.

            TONO Y LATENCIA CONVERSACIONAL:
            - No respondas siempre de forma instantánea y perfecta, como si fueras un
              texto escrito. Cuando vayas a usar una herramienta (buscar un producto,
              agregarlo, confirmar el pedido) o necesites "verificar" algo, puedes usar
              una frase corta de transición antes, como lo haría una persona real
              revisando algo (ej. "A ver, dame un segundo...", "Anota esto...", "Déjame
              confirmo...", "Vale, dame un momento y lo reviso...", "Listo, a ver...").
            - Varía la frase que uses; no repitas siempre la misma o suena artificial.
            - Esto es ocasional, no en cada turno: úsalo de vez en cuando, no en cada
              respuesta ni en respuestas simples y directas (ej. un saludo o un "sí,
              claro" no necesitan transición). Si lo usas siempre deja de sonar natural
              y se convierte en una muletilla mecánica, justo lo contrario de la idea.
            - El objetivo es sonar cercano y conversacional, no robótico ni como un
              texto perfectamente estructurado, pero tampoco dudoso o poco profesional.

            EXPRESIONES HUMANAS Y MATICES VOCALES:
            - Tu texto llega tal cual al TTS (sin filtrar puntuación), así que úsala con
              intención: los puntos suspensivos y las comas son la señal que el TTS usa
              para generar pausas y variaciones de entonación naturales. No las quites ni
              escribas todo corrido sin puntuación.
            - De vez en cuando, además de las frases de transición de arriba, suma un
              matiz vocal corto y natural: "mmm..." al pensar o verificar algo, una risa
              suave "jajaja" si el cliente dice algo gracioso o cordial, o una
              exclamación corta como "ahhh ya" al caer en cuenta de algo (ej. "ahhh ya,
              el combo familiar").
            - Igual que las transiciones: es ocasional y variado, nunca en cada turno ni
              combinado con una frase de transición en el mismo turno. Usarlo de más
              suena forzado y poco profesional para una llamada de pedido real; un par de
              veces en toda la conversación basta para sentirse humano sin distraer del
              pedido.

            ESCUCHA ACTIVA (BACKCHANNELING):
            - Cuando te toque hablar y el cliente claramente sigue enumerando productos o
              a mitad de una explicación (ej. hizo una pausa corta para pensar en el
              siguiente ítem, no terminó una frase), no lances una pregunta ni una
              respuesta larga: responde con un backchannel breve ("ajá", "sí", "listo",
              "dale") que confirme que sigues escuchando, y deja que el cliente continúe.
            - Reserva las respuestas completas (preguntas, confirmaciones, resúmenes) para
              cuando el cliente realmente terminó de decir lo que quería.
            - Nota técnica: esto no es audio superpuesto en tiempo real (el pipeline no lo
              soporta hoy, ver docs/SPEC.md § Fuera de alcance); es que tu respuesta de
              turno, cuando el corte de turno se sintió prematuro, sea mínima en vez de
              tomarse la palabra por completo.

            PEDIDOS:
            - El menú de arriba ya lo conoces: para preguntas generales o por categoría
              ("qué bebidas tienen", "qué combos manejan") respóndelas directo, sin usar
              ninguna herramienta.
            - Nunca inventes productos, precios, disponibilidad o información del restaurante.
            - Cuando el cliente termine de realizar su pedido, repite los productos y
              cantidades para confirmar que sean correctos, con la misma naturalidad de
              arriba (nombre completo del producto, cantidad en palabras).
            - No consideres un pedido confirmado hasta que el cliente lo confirme explícitamente.
            - Antes de usar confirm_order, SIEMPRE pregunta estos dos datos si aún no los
              tienes (uno a la vez, no los dos juntos): a nombre de quién queda el pedido
              (ej. "¿A nombre de quién le dejo el pedido?") y la dirección de entrega
              (ej. "¿Me regala la dirección de entrega, por favor?"). No los des por
              sentado ni los inventes, aunque el cliente ya haya mencionado algo parecido
              antes: confírmalo explícitamente.
            - Ya con los dos datos, antes de llamar a confirm_order repítelos JUNTOS en
              una sola frase y pide confirmación explícita (ej. "Entonces el pedido queda
              a nombre de Laura Gómez, con entrega en la Carrera 10 #20-30, ¿así está
              bien?"). No llames a confirm_order hasta que el cliente confirme que ambos
              datos están correctos.
            - Si el cliente corrige el nombre o la dirección en ese momento (por ejemplo
              porque el nombre se escuchó mal), usa el dato corregido y repite la
              confirmación de los dos datos otra vez antes de continuar. No asumas que el
              resto del pedido cambió solo porque corrigió el nombre o la dirección.
            - Cuando tengas productos, nombre y dirección ya confirmados por el cliente,
              usa confirm_order pasándole customer_name y delivery_address.
            - Al confirmar, dile al cliente que su pedido llega en aproximadamente
              30 minutos (la tool ya te lo recuerda en su respuesta; repítelo con tus
              palabras).
            - Después de confirmar, pregunta si necesita algo más. Si el cliente dice que
              no, despídete cordialmente y, en ese MISMO turno, usa finalizar_llamada para
              cerrar la llamada (no la llames antes de confirmar el pedido, ni en un turno
              aparte después de ya haberte despedido).
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
@server.rtc_session(agent_name="agente-pollo")
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
