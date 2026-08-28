"""Backend minimo para la interfaz web: sirve la pagina estatica y emite
tokens de LiveKit para que un navegador pueda hablar con el agente sin
necesidad de un numero de telefono.

Es un servicio aparte del agente de voz (agent.py), pensado para desplegarse
como un segundo servicio en Railway. No toca Postgres ni el pedido: solo
mintea credenciales de sala.
"""

import os
import secrets
import string
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from livekit import api

load_dotenv()

# Debe coincidir exactamente con el agent_name de @server.rtc_session en
# agent.py. Como ese decorador usa despacho explicito (no automatico), sin
# este nombre en el RoomAgentDispatch del token la sala quedaria vacia: el
# cliente se conectaria pero ningun agente le contestaria.
AGENT_NAME = "agente-pollo"

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI()


def _codigo_corto(n: int = 6) -> str:
    alfabeto = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alfabeto) for _ in range(n))


@app.get("/api/token")
def crear_token():
    """Crea una sala nueva y un token de cliente para esa sala.

    Cada llamada usa una sala nueva (un cliente = una llamada), igual que una
    llamada telefonica real no comparte linea con otra. El RoomAgentDispatch
    incluido en el propio token es lo que le pide a LiveKit que despache
    "agente-pollo" a la sala apenas el cliente entre, sin necesidad de una
    llamada aparte a la API de LiveKit para crear el dispatch.
    """
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    livekit_url = os.getenv("LIVEKIT_URL")
    if not api_key or not api_secret or not livekit_url:
        raise HTTPException(
            status_code=500,
            detail="Faltan LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET en el entorno.",
        )

    room_name = f"pedido-{_codigo_corto()}"
    identity = f"cliente-{_codigo_corto()}"

    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name("Cliente web")
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .with_room_config(
            api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=AGENT_NAME)])
        )
    )

    return {
        "serverUrl": livekit_url,
        "roomName": room_name,
        "participantToken": token.to_jwt(),
    }


# Al final: cualquier ruta que no sea /api/* sirve la pagina estatica
# (index.html incluido via html=True). Debe ir despues de las rutas de API
# para que estas tengan prioridad.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
