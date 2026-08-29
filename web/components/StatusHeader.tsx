"use client";

import { useEffect, useState } from "react";
import { useConnectionState } from "@livekit/components-react";
import { ConnectionState } from "livekit-client";

const STATUS_LABEL: Record<ConnectionState, string> = {
  [ConnectionState.Disconnected]: "Desconectado",
  [ConnectionState.Connecting]: "Conectando…",
  [ConnectionState.Connected]: "En llamada",
  [ConnectionState.Reconnecting]: "Reconectando…",
  [ConnectionState.SignalReconnecting]: "Reconectando…",
};

function formatElapsed(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60)
    .toString()
    .padStart(2, "0");
  const seconds = (totalSeconds % 60).toString().padStart(2, "0");
  return `${minutes}:${seconds}`;
}

export default function StatusHeader({ roomCode }: { roomCode: string }) {
  const connectionState = useConnectionState();
  const [elapsedMs, setElapsedMs] = useState(0);
  const isLive = connectionState === ConnectionState.Connected;

  useEffect(() => {
    if (!isLive) return;
    const startedAt = Date.now();
    const id = setInterval(() => setElapsedMs(Date.now() - startedAt), 1000);
    return () => clearInterval(id);
  }, [isLive]);

  return (
    <header>
      <div className="flex items-center justify-between">
        <span className="font-mono text-xs tracking-[0.2em] text-smoke">AGENTE·POLLO</span>
        <span className="flex items-center gap-1.5 font-mono text-xs tracking-[0.2em] text-smoke">
          <span
            className={`h-1.5 w-1.5 rounded-full ${isLive ? "bg-paprika animate-pulse" : "bg-smoke/50"}`}
          />
          {isLive ? "LIVE" : "—"}
        </span>
      </div>
      <div className="mt-4 border-t border-dashed border-paper/15 pt-4">
        <p className="font-display text-3xl uppercase tracking-wide text-paper">
          {STATUS_LABEL[connectionState]}
        </p>
        <p className="mt-1 font-mono text-xs text-smoke">
          {formatElapsed(elapsedMs)} · #{roomCode.slice(-4).toUpperCase()}
        </p>
      </div>
    </header>
  );
}
