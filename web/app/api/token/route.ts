import { randomUUID } from "node:crypto";
import { NextResponse } from "next/server";
import { AccessToken } from "livekit-server-sdk";
import { RoomAgentDispatch, RoomConfiguration } from "@livekit/protocol";
import type { ConnectionDetails } from "@/lib/types";

// Debe ser idéntico al agent_name="agente-hotel-reservas" registrado en
// @server.rtc_session (agent.py) del lado del agente. Ese registro activa
// "explicit dispatch": una sala nueva ya no dispara el agente sola, así que
// el dispatch de abajo (roomConfig.agents) es lo que reemplaza ese paso.
const AGENT_NAME = "agente-hotel-reservas";

export async function POST() {
  const livekitUrl = process.env.LIVEKIT_URL;
  const apiKey = process.env.LIVEKIT_API_KEY;
  const apiSecret = process.env.LIVEKIT_API_SECRET;

  if (!livekitUrl || !apiKey || !apiSecret) {
    return NextResponse.json(
      {
        error:
          "Faltan LIVEKIT_URL, LIVEKIT_API_KEY o LIVEKIT_API_SECRET en el servidor. Copia .env.example a .env.local y complétalas.",
      },
      { status: 500 },
    );
  }

  const participantIdentity = `cliente-${randomUUID().slice(0, 8)}`;
  const roomName = `agente-hotel-reservas-${randomUUID().slice(0, 8)}`;

  const accessToken = new AccessToken(apiKey, apiSecret, {
    identity: participantIdentity,
    ttl: "10m",
  });
  accessToken.addGrant({
    roomJoin: true,
    room: roomName,
    canPublish: true,
    canSubscribe: true,
    canPublishData: true,
  });
  accessToken.roomConfig = new RoomConfiguration({
    agents: [new RoomAgentDispatch({ agentName: AGENT_NAME })],
  });

  const token = await accessToken.toJwt();

  const details: ConnectionDetails = {
    serverUrl: livekitUrl,
    roomName,
    participantIdentity,
    token,
  };

  return NextResponse.json(details);
}
