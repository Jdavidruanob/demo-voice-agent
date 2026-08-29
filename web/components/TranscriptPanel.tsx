"use client";

import { useEffect, useRef } from "react";
import { useLocalParticipant, useTranscriptions } from "@livekit/components-react";

export default function TranscriptPanel() {
  const transcriptions = useTranscriptions();
  const { localParticipant } = useLocalParticipant();
  const scrollRef = useRef<HTMLDivElement>(null);

  const sorted = [...transcriptions].sort(
    (a, b) => a.streamInfo.timestamp - b.streamInfo.timestamp,
  );

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [sorted.length]);

  return (
    <div className="min-h-0 flex-1 rounded-lg bg-paper/[0.04] ring-1 ring-paper/10">
      <div ref={scrollRef} className="h-48 overflow-y-auto px-4 py-3 sm:h-56">
        {sorted.length === 0 ? (
          <p className="font-mono text-xs text-smoke">
            La transcripción aparece aquí en cuanto empiecen a hablar.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {sorted.map((item) => {
              const isLocal = item.participantInfo.identity === localParticipant.identity;
              return (
                <li
                  key={item.streamInfo.id}
                  className={`flex flex-col ${isLocal ? "items-end text-right" : "items-start text-left"}`}
                >
                  <span className="font-mono text-[10px] uppercase tracking-widest text-smoke">
                    {isLocal ? "tú" : "agente"}
                  </span>
                  <span
                    className={`max-w-[85%] rounded-xl px-3 py-1.5 text-sm leading-snug text-paper ${
                      isLocal ? "bg-mustard/15" : "bg-paprika/15"
                    }`}
                  >
                    {item.text}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
