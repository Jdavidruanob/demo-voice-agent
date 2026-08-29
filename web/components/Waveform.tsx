"use client";

import { BarVisualizer, useVoiceAssistant } from "@livekit/components-react";

const STATE_LABEL: Partial<Record<string, string>> = {
  disconnected: "conectando…",
  connecting: "conectando…",
  "pre-connect-buffering": "conectando…",
  initializing: "iniciando…",
  idle: "en espera",
  listening: "escuchando",
  thinking: "pensando",
  speaking: "hablando",
  failed: "error de conexión",
};

export default function Waveform() {
  const { state, audioTrack } = useVoiceAssistant();

  return (
    <div className="flex flex-col items-center gap-2 py-6">
      <BarVisualizer
        state={state}
        track={audioTrack}
        barCount={11}
        options={{ minHeight: 14, maxHeight: 100 }}
        className="h-16 w-full max-w-2xs gap-1"
      />
      <p className="font-mono text-[11px] uppercase tracking-[0.25em] text-smoke">
        {STATE_LABEL[state] ?? state}
      </p>
    </div>
  );
}
