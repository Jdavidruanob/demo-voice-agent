interface IdleScreenProps {
  onStart: () => void;
  isLoading: boolean;
  error: string | null;
}

export default function IdleScreen({ onStart, isLoading, error }: IdleScreenProps) {
  return (
    <div className="flex flex-col items-center gap-6 py-10 text-center">
      <div>
        <p className="font-mono text-xs tracking-[0.2em] text-smoke">AGENTE·POLLO</p>
        <p className="mt-3 font-display text-3xl uppercase tracking-wide text-paper">
          Desconectado
        </p>
        <p className="mt-2 max-w-2xs text-sm text-smoke">
          Llamada de prueba al agente de voz. Tu navegador va a pedir permiso de micrófono.
        </p>
      </div>
      <button
        type="button"
        onClick={onStart}
        disabled={isLoading}
        className="rounded-full bg-paprika px-8 py-3 font-mono text-xs uppercase tracking-widest text-ink transition hover:bg-paprika/90 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {isLoading ? "Conectando…" : "Iniciar llamada"}
      </button>
      {error ? (
        <p className="max-w-2xs rounded-lg bg-paprika/10 px-3 py-2 text-xs text-paprika">
          {error}
        </p>
      ) : null}
    </div>
  );
}
