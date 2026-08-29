"use client";

import { TrackToggle, useLocalParticipant } from "@livekit/components-react";
import { Track } from "livekit-client";

export default function CallControls({ onHangUp }: { onHangUp: () => void }) {
  const { isMicrophoneEnabled } = useLocalParticipant();

  return (
    <div className="mt-6 flex items-center justify-center gap-3">
      <TrackToggle
        source={Track.Source.Microphone}
        showIcon={false}
        className="rounded-full border border-paper/15 bg-char px-5 py-3 font-mono text-xs uppercase tracking-widest text-paper transition hover:bg-paper/10"
      >
        {isMicrophoneEnabled ? "Silenciar" : "Reactivar mic"}
      </TrackToggle>
      <button
        type="button"
        onClick={onHangUp}
        className="rounded-full bg-paprika px-6 py-3 font-mono text-xs uppercase tracking-widest text-ink transition hover:bg-paprika/90"
      >
        Colgar
      </button>
    </div>
  );
}
