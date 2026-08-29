import StatusHeader from "@/components/StatusHeader";
import Waveform from "@/components/Waveform";
import TranscriptPanel from "@/components/TranscriptPanel";
import CallControls from "@/components/CallControls";

interface InCallConsoleProps {
  roomCode: string;
  onHangUp: () => void;
}

export default function InCallConsole({ roomCode, onHangUp }: InCallConsoleProps) {
  return (
    <div className="flex flex-1 flex-col">
      <StatusHeader roomCode={roomCode} />
      <Waveform />
      <TranscriptPanel />
      <CallControls onHangUp={onHangUp} />
    </div>
  );
}
