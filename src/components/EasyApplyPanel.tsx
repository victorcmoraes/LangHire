import { useCallback, useEffect, useState } from 'react';
import { getEasyAnswers, getEasyReviews, approveEasyAnswer, proposeEasyAnswer, getEasyChannel,
  verifyEasySession, startApplying, getJobs, getEasyFacts, qualifyEasyJob, getApplyStatus, launchLogin } from '../lib/api';
import type { EasyQuestion, EasyReview } from '../lib/api';
import type { Job } from '../lib/types';
import HumanInterventions from './HumanInterventions';

function AnswerCard({ question, refresh }: { question: EasyQuestion; refresh: () => void }) {
  const [value, setValue] = useState<string | boolean | number>(question.proposal ?? question.answer ?? (question.field.type === 'checkbox' ? false : ''));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [currency, setCurrency] = useState(question.context.currency || '');
  const [period, setPeriod] = useState(question.context.period || '');
  const isSalary = /salary|salari|remunera|pretensao|renda|taxa hora/.test(question.question.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase());
  useEffect(() => { setValue(question.proposal ?? question.answer ?? (question.field.type==='checkbox'?false:'')); }, [question.proposal,question.answer,question.field.type]);
  const perform = async (action: () => Promise<unknown>) => {
    setBusy(true); setError(''); setNotice('');
    try { await action(); refresh(); } catch (e) {setError(String(e));} finally {setBusy(false);}
  };
  return <div className="border border-border rounded-lg p-3 space-y-2">
    <p className="font-semibold">{question.question}</p>
    {question.field.profile_text && <pre className="whitespace-pre-wrap text-sm">{question.field.profile_text}</pre>}
    <p className="text-sm">{question.state} · {question.jobs.length} candidatura(s) · {JSON.stringify(question.context)}</p>
    {question.field.options?.length ? <select aria-label={question.question} value={String(value)} onChange={e=>setValue(e.target.value)} className="input-base">
      <option value="">Selecione</option>{question.field.options.map(o=><option key={o.value} value={o.value}>{o.text}</option>)}
    </select> : question.field.type==='checkbox' ? <label><input type="checkbox" checked={value===true} onChange={e=>setValue(e.target.checked)}/> Sim</label>
      : <input aria-label={question.question} className="input-base" value={String(value)} onChange={e=>setValue(e.target.value)}/>}
    {isSalary && <div className="flex gap-3">
      <label>Moeda desta resposta<input aria-label={`Moeda: ${question.question}`} className="input-base" placeholder="BRL, USD..." maxLength={3} value={currency} readOnly={!!question.context.currency} onChange={e=>setCurrency(e.target.value.toUpperCase())}/></label>
      <label>Período desta resposta<select aria-label={`Período: ${question.question}`} className="input-base" value={period} disabled={!!question.context.period} onChange={e=>setPeriod(e.target.value)}>
        <option value="">Selecione</option><option value="monthly">Mensal</option><option value="annual">Anual</option><option value="hourly">Por hora</option>
      </select></label>
    </div>}
    {isSalary && (!currency || !period) && <p className="text-sm">Confirma a moeda e o período para salvar este valor no contexto certo.</p>}
    <div className="flex gap-3"><button className="btn-secondary mr-2" disabled={busy} onClick={()=>void perform(async()=>{const result=await proposeEasyAnswer(question.id);if(result.supported===false)setNotice(result.message || 'Faltam fatos no cadastro. Informe a resposta para confirmar.');})}>Propor com 8b</button>
      <button className="btn-secondary mr-2" disabled={busy || value==='' || (isSalary && (!/^[A-Z]{3}$/.test(currency) || !period))} onClick={()=>void perform(()=>isSalary ? approveEasyAnswer(question.id,value,{currency,period}) : approveEasyAnswer(question.id,value))}>Confirmar e reutilizar</button></div>
    {question.refs.length>0 && <p className="text-xs">Fatos: {question.refs.join(', ')}</p>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
  </div>;
}

export function EasyAnswers() {
  const [questions,setQuestions]=useState<EasyQuestion[]>([]);
  const [error,setError]=useState('');
  const refresh=useCallback(()=>{getEasyAnswers().then(setQuestions).catch(e=>setError(String(e)));},[]);
  useEffect(()=>{refresh();const timer=setInterval(refresh,5000);return()=>clearInterval(timer);},[refresh]);
  return <section className="card space-y-3 mb-4"><h2 className="font-semibold">Respostas do Easy Apply</h2>
    <p className="text-sm">Propostas do modelo só serão usadas depois da tua confirmação.</p>
    {error && <p role="alert">{error}</p>}
    {questions.filter(q=>q.state!=='approved').map(q=><AnswerCard key={q.id} question={q} refresh={refresh}/>)}
    {!questions.length && <p>Nenhuma pergunta nova.</p>}
    {questions.filter(q=>q.state==='approved').length>0 && <details><summary>Respostas aprovadas</summary>
      {questions.filter(q=>q.state==='approved').map(q=><AnswerCard key={q.id} question={q} refresh={refresh}/>)}</details>}
  </section>;
}

function ReviewCard({ review, release, busy }: {review: EasyReview;release:()=>void;busy:boolean}) {
  const payload=JSON.parse(review.payload) as {title:string;company:string;answers:{question:string;value:unknown;refs:string[]}[];resume_hash:string;review_text:string};
  return <article className="border border-border rounded-lg p-3 space-y-2">
    <a href={review.job_url} target="_blank" rel="noreferrer">{payload.title} — {payload.company}</a>
    <details><summary>Conferir respostas e currículo</summary><table><tbody>{payload.answers.map((a,i)=><tr key={i}><td>{a.question}</td><td>{String(a.value)}</td><td>{a.refs.join(', ')}</td></tr>)}</tbody></table>
      <p className="text-xs break-all">PDF SHA256: {payload.resume_hash}</p><p>{payload.review_text}</p></details>
    <button className="btn-secondary mr-2" disabled={busy} onClick={release}>Conferi esta revisão: enviar uma vez</button>
  </article>;
}

export default function EasyApplyPanel() {
  const [reviews,setReviews]=useState<EasyReview[]>([]);
  const [jobs,setJobs]=useState<Job[]>([]);
  const [facts,setFacts]=useState<Record<string,{value:unknown}>>({});
  const [selected,setSelected]=useState('');
  const [evidence,setEvidence]=useState('');
  const [refs,setRefs]=useState<string[]>([]);
  const [language,setLanguage]=useState('pt');
  const [currency,setCurrency]=useState('');
  const [period,setPeriod]=useState('');
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [stage,setStage]=useState('');
  const [reason,setReason]=useState('');
  const [runError,setRunError]=useState('');
  const refresh=useCallback(async()=>{
    try {const [r,j,f,c,s]=await Promise.all([getEasyReviews(),getJobs({limit:5000}),getEasyFacts(),getEasyChannel(),getApplyStatus()]);
      setReviews(r);setJobs(j.filter(x=>x.easy_apply===true && !['applied','uncertain'].includes(x.status)));setFacts(f);setReason(c.reason);setBusy(s.running);
      setStage(s.stage || 'parado');setRunError(s.error || '');
    } catch(e){setError(String(e));}
  },[]);
  useEffect(()=>{const initial=setTimeout(()=>void refresh(),0);const timer=setInterval(()=>void refresh(),4000);return()=>{clearTimeout(initial);clearInterval(timer);};},[refresh]);
  const perform=async(action:()=>Promise<unknown>)=>{setBusy(true);setError('');try{await action();await refresh();}catch(e){setError(String(e));setBusy(false);}};
  const job=jobs.find(j=>j.url===selected);
  return <section className="space-y-4 mb-6" aria-label="Easy Apply padronizado">
    <div className="card space-y-3"><h2 className="font-semibold">LinkedIn Easy Apply — revisão antes do envio</h2>
      <p>Etapa: {stage}. Um navegador; até dez tentativas/dia.</p>
      {reason && <p role="status">Canal suspenso: {reason}</p>}
      <button className="btn-secondary mr-2" disabled={busy} onClick={()=>void perform(()=>launchLogin('linkedin'))}>Abrir login do LinkedIn</button>
      <button className="btn-secondary mr-2" disabled={busy} onClick={()=>void perform(()=>verifyEasySession())}>Verificar sessão LinkedIn</button>
      <p className="text-sm">Use o login do LinkedIn no cadastro e feche a janela de login antes de verificar.</p>
      <select aria-label="Vaga Easy Apply" className="input-base" value={selected} onChange={e=>{setSelected(e.target.value);setEvidence('');setRefs([]);}}>
        <option value="">Selecione uma vaga</option>{jobs.map(j=><option key={j.url} value={j.url}>{j.title} — {j.company}</option>)}</select>
      {job && <><details><summary>Requisitos e localização</summary><p>{job.location}</p><p className="whitespace-pre-wrap">{job.description}</p></details>
        <textarea aria-label="Evidências de elegibilidade" className="input-base" placeholder="Trechos que comprovam localização elegível e requisitos obrigatórios atendidos" value={evidence} onChange={e=>setEvidence(e.target.value)}/>
        <select multiple aria-label="Fatos que sustentam elegibilidade" className="input-base" value={refs} onChange={e=>setRefs([...e.target.selectedOptions].map(o=>o.value))}>
          {Object.entries(facts).map(([id,f])=><option key={id} value={id}>{id}: {JSON.stringify(f.value)}</option>)}</select>
        <select aria-label="Idioma do formulário" value={language} onChange={e=>setLanguage(e.target.value)}><option value="pt">Português</option><option value="en">English</option></select>
        <label>Moeda salarial, se confirmada<input aria-label="Moeda salarial" placeholder="BRL, USD…" value={currency} onChange={e=>setCurrency(e.target.value.toUpperCase())}/></label>
        <label>Período salarial<select aria-label="Período salarial" value={period} onChange={e=>setPeriod(e.target.value)}><option value="">Não informado</option><option value="monthly">Mensal</option><option value="annual">Anual</option><option value="hourly">Por hora</option></select></label>
        <button className="btn-secondary mr-2" disabled={busy || !evidence.trim() || !refs.length} onClick={()=>void perform(()=>qualifyEasyJob(selected,evidence,refs,language,currency,period))}>Confirmar elegibilidade no Brasil</button>
        <button className="btn-secondary mr-2" disabled={busy} onClick={()=>void perform(()=>startApplying({mode:'easy',execution:'review',job_url:selected,workers:1}))}>Preencher e revisar sem enviar</button></>}
      {error && <p role="alert">{error}</p>}
      {runError && <p role="alert">{runError}</p>}
    </div>
    <HumanInterventions/>
    <EasyAnswers/>
    <div className="card space-y-3"><h2 className="font-semibold">Revisões prontas</h2><p>Prepare três vagas diferentes antes de liberar o primeiro envio.</p>
      {reviews.map(r=><ReviewCard key={r.id} review={r} busy={busy} release={()=>void perform(()=>startApplying({mode:'easy',execution:'submit',job_url:r.job_url,review_id:r.id,workers:1}))}/>)}</div>
  </section>;
}
