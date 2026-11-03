import { useEffect, useState } from "react";
import { confirmIntervention, getInterventions } from "../lib/api";
import type { HumanIntervention } from "../lib/types";

/** Acknowledgement resumes observation of the same attempt, never a new submit. */
export default function HumanInterventions() {
  const [holds, setHolds] = useState<HumanIntervention[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const items = await getInterventions();
        if (active) { setHolds(items); setError(""); }
      } catch { if (active) setError("Não foi possível consultar as intervenções."); }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 2000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  const confirm = async (id: string) => {
    setBusy(id);
    try {
      await confirmIntervention(id);
      setHolds(await getInterventions());
    } catch { setError("Não foi possível confirmar. A tentativa continua preservada."); }
    finally { setBusy(null); }
  };
  if (!holds.length && !error) return null;
  return <section aria-label="Intervenções necessárias" className="rounded-xl border border-border p-4 space-y-3">
    {error && <p role="alert">{error}</p>}
    {holds.map(hold => <div key={hold.id} className="space-y-2">
      <p className="font-semibold">Candidatura aguardando tua intervenção</p>
      <p>{hold.message}</p>
      <p className="text-sm break-all">{hold.job_url}</p>
      <p className="text-sm">Resolva o desafio na janela já aberta, sem clicar novamente em enviar. Depois confirme aqui.</p>
      <button disabled={busy !== null || !!hold.acknowledged} onClick={() => void confirm(hold.id)}
        className="rounded-lg bg-primary px-4 py-2 text-primary-foreground disabled:opacity-50">
        {busy === hold.id || hold.acknowledged ? "Conferindo a mesma tentativa…" : "Resolvi o desafio"}
      </button>
    </div>)}
  </section>;
}
