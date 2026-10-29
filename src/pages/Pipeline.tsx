import { useEffect, useMemo, useRef, useState } from "react";
import { sankey, sankeyLinkHorizontal, type SankeyNode } from "d3-sankey";
import { GitBranch, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { getPipeline } from "../lib/api";
import type { PipelineData, PipelineJob } from "../lib/types";
import { PageHeader, LoadingSpinner } from "../components/ui";

type Node = PipelineData["sankey"]["nodes"][number];
const colors = ["var(--color-muted-foreground)", "var(--color-warning)", "var(--color-primary)", "var(--color-success)", "var(--color-foreground)"];
const pageSize = 50;

export default function Pipeline() {
  const { t } = useTranslation("dashboard");
  const [data, setData] = useState<PipelineData | null>(null);
  const [error, setError] = useState(false);
  const [search, setSearch] = useState("");
  const [platform, setPlatform] = useState("");
  const [stage, setStage] = useState("");
  const [bucket, setBucket] = useState("");
  const [status, setStatus] = useState("");
  const [selection, setSelection] = useState<string[]>([]);
  const [sort, setSort] = useState<"title" | "company" | "platform" | "triage" | "application_status" | "outcome" | "last_update">("last_update");
  const [descending, setDescending] = useState(true);
  const [page, setPage] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const [detail, setDetail] = useState<PipelineJob | null>(null);
  const label = (value: string) => t(`pipeline.labels.${value}`, value);

  useEffect(() => {
    let cancelled = false;
    getPipeline().then(value => { if (!cancelled) setData(value); }).catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (detail) dialog.current?.showModal();
  }, [detail]);

  const chartHeight = Math.max(650, (data?.sankey.nodes.filter(n => n.stage === 1).length ?? 0) * 42 + 80);
  const graph = useMemo(() => {
    if (!data?.sankey.links.length) return null;
    return sankey<Node, { value: number }>().nodeId(n => n.id).nodeAlign(n => n.stage)
      .nodeWidth(14).nodePadding(32).extent([[15, 40], [1200, chartHeight - 30]])({
        nodes: data.sankey.nodes.map(n => ({ ...n })), links: data.sankey.links.map(l => ({ ...l })),
      });
  }, [data, chartHeight]);

  const filtered = useMemo(() => {
    const query = search.toLowerCase();
    const value = (job: PipelineJob) => sort === "triage" ? job.triage.state + job.triage.bucket : sort === "outcome" ? job.outcome.stage : job[sort];
    return (data?.jobs ?? []).filter(job =>
      (!query || `${job.title} ${job.company} ${job.board} ${job.triage.reason} ${job.location}`.toLowerCase().includes(query)) &&
      (!platform || job.platform === platform) && (!stage || job.triage.state === stage) &&
      (!bucket || job.triage.bucket === bucket) && (!status || job.application_status === status) &&
      selection.every((id, index) => job.path.includes(id) && (index === 0 || job.path.indexOf(id) === job.path.indexOf(selection[index - 1]) + 1))
    ).sort((a, b) => (descending ? -1 : 1) * value(a).localeCompare(value(b)));
  }, [data, search, platform, stage, bucket, status, selection, sort, descending]);
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const currentPage = Math.min(page, pages - 1);
  const choose = (ids: string[]) => { setSelection(ids); setPage(0); };
  const sortBy = (column: typeof sort) => { setDescending(sort === column ? !descending : false); setSort(column); };

  if (error) return <div role="alert" className="card text-destructive">{t("pipeline.error")}</div>;
  if (!data) return <LoadingSpinner />;
  const options = (values: string[]) => [...new Set(values)].sort().map(v => <option key={v} value={v}>{label(v)}</option>);
  const nodeName = (id: string) => label(data.sankey.nodes.find(n => n.id === id)?.label ?? id);
  const nodeId = (n: string | number | SankeyNode<Node, { value: number }>) => typeof n === "object" ? n.id : String(n);
  const activate = (event: React.KeyboardEvent, ids: string[]) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(ids); } };
  const formatDate = (date: string) => date ? new Date(date).toLocaleString() : t("pipeline.unknown");

  return <div className="max-w-7xl space-y-5">
    <PageHeader title={t("pipeline.title")} subtitle={t("pipeline.subtitle")} />
    <div className="grid grid-cols-2 xl:grid-cols-5 gap-4">
      {(["collected", "eligible", "sent", "confirmed", "advanced"] as const).map(key => <div className="card" key={key}>
        <p className="text-sm text-muted-foreground">{t(`pipeline.kpis.${key}`)}</p>
        <p className="text-3xl font-bold tabular-nums" data-testid={`kpi-${key}`}>{data.totals[key]}</p>
        <p className="text-xs text-muted-foreground">{t("pipeline.conversion", { percent: data.totals.collected ? Math.round(data.totals[key] / data.totals.collected * 100) : 0 })}</p>
      </div>)}
    </div>
    <section className="card" aria-label={t("pipeline.flow")}>
      <h2 className="section-title flex gap-2 items-center"><GitBranch size={18} />{t("pipeline.flow")}</h2>
      <p className="text-sm text-muted-foreground mt-1">{t("pipeline.hint")}</p>
      {graph ? <div className="overflow-x-auto"><svg viewBox={`0 0 1400 ${chartHeight}`} className="w-full min-w-[1200px]" aria-label={t("pipeline.flow")}>
        {["collection", "triage", "application", "receipt", "outcome"].map((s, i) => <text key={s} x={15 + i * 296.5} y={17} className="fill-muted-foreground text-xs">{t(`pipeline.stages.${s}`)}</text>)}
        {graph.links.map(link => {
          const ids = [nodeId(link.source), nodeId(link.target)];
          const name = `${nodeName(ids[0])} → ${nodeName(ids[1])}: ${link.value}`;
          return <path key={ids.join("|")} d={sankeyLinkHorizontal()(link) ?? ""} fill="none" stroke={colors[(link.source as SankeyNode<Node, { value: number }>).stage]} strokeWidth={Math.max(2, link.width ?? 0)} strokeOpacity={selection.length && !selection.every(id => ids.includes(id)) ? 0.08 : 0.3} className="cursor-pointer hover:stroke-opacity-60 focus:stroke-opacity-60" role="button" tabIndex={0} aria-label={name} onClick={() => choose(ids)} onKeyDown={e => activate(e, ids)}><title>{name}</title></path>;
        })}
        {graph.nodes.map(n => <g key={n.id} role="button" tabIndex={0} aria-label={`${label(n.label)}: ${n.value}`} onClick={() => choose([n.id])} onKeyDown={e => activate(e, [n.id])} className="cursor-pointer">
          <title>{label(n.label)}: {n.value}</title>
          <rect x={n.x0} y={n.y0} width={14} height={Math.max(2, (n.y1 ?? 0) - (n.y0 ?? 0))} fill={colors[n.stage]} stroke={selection.includes(n.id) ? "var(--color-foreground)" : "none"} strokeWidth={2} />
          <text x={(n.x0 ?? 0) + 20} y={(n.y0 ?? 0) + 10} className="fill-foreground text-[13px]">{label(n.label).split(": ")[0]} · {n.value}{n.label.includes(": ") && <tspan x={(n.x0 ?? 0) + 20} dy={14}>{label(n.label).split(": ").slice(1).join(": ")}</tspan>}</text>
        </g>)}
      </svg></div> : <p className="py-12 text-muted-foreground">{t("pipeline.empty")}</p>}
    </section>
    <section className="card space-y-4" aria-label={t("pipeline.explorer")}>
      <div className="flex items-center justify-between"><h2 className="section-title">{t("pipeline.explorer")} ({filtered.length})</h2>
        <button className="text-sm text-primary" onClick={() => { setSearch(""); setPlatform(""); setStage(""); setBucket(""); setStatus(""); choose([]); }}>{t("pipeline.reset")}</button></div>
      {selection.length > 0 && <p className="text-sm" role="status">{t("pipeline.selected")}: {selection.map(nodeName).join("\u2192")}</p>}
      <div className="flex flex-wrap gap-2">
        <input className="input-base flex-1 min-w-40" aria-label={t("pipeline.search")} placeholder={t("pipeline.search")} value={search} onChange={e => { setSearch(e.target.value); setPage(0); }} />
        {([
          ["platform", platform, setPlatform, options(data.jobs.map(j => j.platform))],
          ["triage", stage, setStage, options(data.jobs.map(j => j.triage.state))],
          ["bucket", bucket, setBucket, options(data.jobs.map(j => j.triage.bucket))],
          ["status", status, setStatus, options(data.jobs.map(j => j.application_status))],
        ] as const).map(([name, val, setter, choices]) => <select key={name} className="input-base max-w-52" aria-label={t(`pipeline.columns.${name}`)} value={val} onChange={e => { setter(e.target.value); setPage(0); }}><option value="">{t(`pipeline.columns.${name}`)}</option>{choices}</select>)}
      </div>
      <div className="overflow-x-auto"><table className="w-full text-sm text-left"><thead><tr className="border-b border-border">
        {([['title', 'title'], ['company', 'company'], ['platform', 'platform'], ['triage', 'triage'], ['status', 'application_status'], ['outcome', 'outcome'], ['updated', 'last_update']] as const).map(([name, column]) => <th key={column} className="p-2" aria-sort={sort === column ? descending ? "descending" : "ascending" : "none"}><button onClick={() => sortBy(column)}>{t(`pipeline.columns.${name}`)} {sort === column ? descending ? '\u2193' : '\u2191' : ''}</button></th>)}
      </tr></thead><tbody>
        {filtered.slice(currentPage * pageSize, (currentPage + 1) * pageSize).map(job => <tr key={job.job_key} className="border-b border-border hover:bg-secondary cursor-pointer" onClick={() => setDetail(job)}>
          <td className="p-2"><button className="text-primary text-left" onClick={e => { e.stopPropagation(); setDetail(job); }}>{job.title || t("pipeline.unknown")}</button></td>
          <td className="p-2">{job.company || job.board}</td><td className="p-2">{job.platform}</td><td className="p-2">{label(job.triage.state)}<span className="block text-xs text-muted-foreground">{label(job.triage.bucket)}</span></td>
          <td className="p-2">{label(job.application_status)}</td><td className="p-2">{label(job.outcome.stage)}</td><td className="p-2 text-xs">{formatDate(job.last_update)}</td>
        </tr>)}
      </tbody></table></div>
      {filtered.length === 0 && <p>{t("pipeline.noMatches")}</p>}
      <div className="flex items-center justify-end gap-4 text-sm"><button disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>{t("pipeline.previous")}</button><span>{t("pipeline.pagination", { page: currentPage + 1, pages })}</span><button disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>{t("pipeline.next")}</button></div>
    </section>
    <p className="text-xs text-muted-foreground">{t("pipeline.generated", { date: formatDate(data.generated_at) })}</p>
    {detail && <dialog aria-label={detail.title} ref={dialog} onCancel={() => setDetail(null)} className="fixed inset-y-0 left-auto right-0 m-0 w-full max-w-xl border-0 p-0 h-screen max-h-screen bg-card text-foreground backdrop:bg-background/80" onClick={e => { if (e.target === e.currentTarget) setDetail(null); }}>
      <aside className="w-full max-w-xl bg-card border-l border-border p-6 overflow-y-auto shadow-xl" onClick={e => e.stopPropagation()} onKeyDown={e => { if (e.key === "Escape") setDetail(null); }}>
        <button autoFocus className="float-right p-2" aria-label={t("pipeline.close")} onClick={() => setDetail(null)}><X size={20} /></button>
        <h2 className="text-xl font-bold">{detail.title}</h2><p>{detail.company || detail.board} · {detail.platform}</p><p className="text-muted-foreground">{detail.location}</p>
        {/^https?:\/\//i.test(detail.url) && <a href={detail.url} target="_blank" rel="noreferrer" className="text-primary underline">{t("pipeline.posting")}</a>}
        <p className="text-xs text-muted-foreground mt-2">{t("pipeline.collectedAt", { date: formatDate(detail.collected_at) })} · {detail.source}</p>
        <h3 className="section-title mt-6">{t("pipeline.stages.triage")}</h3><p>{label(detail.triage.state)} · {label(detail.triage.bucket)}</p><p className="break-words">{detail.triage.reason}</p><ul className="list-disc pl-5 mt-2">{detail.triage.evidence.map((e, i) => <li key={i} className="break-words">{e}</li>)}</ul>
        {detail.triage.recorded_state && detail.triage.recorded_state !== detail.triage.state && <p className="text-xs text-muted-foreground">{t("pipeline.recordedTriage", { state: label(detail.triage.recorded_state) })}</p>}
        <h3 className="section-title mt-6">{t("pipeline.attempts")}</h3><p>{label(detail.application_status)}</p>
        {detail.attempts.length === 0 && <p className="text-muted-foreground">{t("pipeline.noAttempts")}</p>}
        <ol className="space-y-3 mt-3">{detail.attempts.map(a => <li key={a.id} className="border border-border rounded-xl p-3"><p className="font-semibold">{label(a.state)}</p><p className="text-xs text-muted-foreground">{formatDate(a.created_at)} → {formatDate(a.updated_at)}</p><p className="break-words">{a.message}</p><p className="text-xs">{t("pipeline.sendIntent")}: {t(a.send_intent ? "pipeline.yes" : "pipeline.no")}</p></li>)}</ol>
        <h3 className="section-title mt-6">{t("pipeline.stages.outcome")}</h3><p>{label(detail.outcome.stage)}</p>{detail.outcome.reply_at && <p>{formatDate(detail.outcome.reply_at)}</p>}{detail.outcome.ambiguous && <p className="text-muted-foreground">{t("pipeline.ambiguous")}</p>}
      </aside>
    </dialog>}
  </div>;
}
