"use client";

import { useState } from "react";
import { LiveKitRoom, RoomAudioRenderer } from "@livekit/components-react";
import { MediaDeviceFailure } from "livekit-client";
import IdleScreen from "@/components/IdleScreen";
import InCallConsole from "@/components/InCallConsole";
import type { ConnectionDetails } from "@/lib/types";

const DEVICE_FAILURE_MESSAGE: Record<MediaDeviceFailure, string> = {
  [MediaDeviceFailure.PermissionDenied]:
    "El navegador no tiene permiso para usar el micrófono. Habilítalo en la configuración del sitio e intenta de nuevo.",
  [MediaDeviceFailure.NotFound]: "No se encontró un micrófono disponible en este dispositivo.",
  [MediaDeviceFailure.DeviceInUse]: "El micrófono ya está siendo usado por otra aplicación.",
  [MediaDeviceFailure.Other]: "No se pudo acceder al micrófono.",
};

export default function CallConsole() {
  const [connectionDetails, setConnectionDetails] = useState<ConnectionDetails | null>(null);
  const [isRequestingToken, setIsRequestingToken] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleStart() {
    setError(null);
    setIsRequestingToken(true);
    try {
      const res = await fetch("/api/token", { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error ?? "No se pudo iniciar la llamada.");
      }
      setConnectionDetails(data as ConnectionDetails);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo iniciar la llamada.");
    } finally {
      setIsRequestingToken(false);
    }
  }

  function handleEnd(message?: string) {
    setConnectionDetails(null);
    if (message) setError(message);
  }

  return (
    <div className="call-console relative w-full max-w-sm overflow-hidden rounded-2xl bg-char text-paper shadow-[0_30px_60px_-15px_rgba(0,0,0,0.6)] ring-1 ring-paper/10">
      <div className="ticket-texture pointer-events-none absolute inset-0" />
      <div className="relative flex flex-col p-6">
        {connectionDetails ? (
          <LiveKitRoom
            serverUrl={connectionDetails.serverUrl}
            token={connectionDetails.token}
            audio
            connect
            onDisconnected={() => handleEnd()}
            onError={(err) => handleEnd(err.message)}
            onMediaDeviceFailure={(failure) =>
              handleEnd(failure ? DEVICE_FAILURE_MESSAGE[failure] : undefined)
            }
          >
            <RoomAudioRenderer />
            <InCallConsole roomCode={connectionDetails.roomName} onHangUp={() => handleEnd()} />
          </LiveKitRoom>
        ) : (
          <IdleScreen onStart={handleStart} isLoading={isRequestingToken} error={error} />
        )}
      </div>
    </div>
  );
}
