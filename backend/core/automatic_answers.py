"""Generate one local proposal per unresolved question.

The default mode never approves it. The autonomous mode answers for the screening
goal, while checkable facts still need confirmed evidence."""
import json
import os
from pathlib import Path
import time
import types
import uuid

from .application_safety import now
from .easy_answers import digest,valid_value,proposal_fact_sources,demographic_question,hard_fact_question,narrative_field,mapped_answer,experience_question,unknown_terms,asked_text


def code_identity(code):
    """Stable across interpreters' mutable marshal/interning state and bundles."""
    def constant(value):
        if isinstance(value,types.CodeType):return code_identity(value)
        if isinstance(value,bytes):return {'bytes':value.hex()}
        if isinstance(value,(tuple,list)):return [constant(v) for v in value]
        if isinstance(value,frozenset):return sorted((constant(v) for v in value),key=repr)
        return value
    return {'bytecode':code.co_code.hex(),'constants':[constant(v) for v in code.co_consts],'names':code.co_names}


async def resolve_answer(book,field,job,metrics=None):
    answer=book.resolve(field,job)
    if not isinstance(answer.get('pending'),int):return answer
    data=book.store.directory
    path=data/'settings.json'
    policy=(json.loads(path.read_text(encoding='utf-8')).get('automation_answers') or {}) if path.exists() else {}
    if policy.get('enabled') is not True:return answer
    if field['type'] not in ('text','email','tel','url','number','textarea','select-one','radio','checkbox','checkbox-group'):
        return dict(answer,proposal_state='unsupported_control')
    llm_path=data/'llm_settings.json'
    settings=json.loads(llm_path.read_text(encoding='utf-8')) if llm_path.exists() else {}
    if settings.get('provider')!='ollama':return dict(answer,proposal_state='local_model_not_configured')
    ollama=settings.get('ollama') or {}
    # Answer quality was validated per model; another local model must be named in the policy.
    if (ollama.get('model') or '').split('@')[0]!=policy.get('model','qwen3:8b'):
        return dict(answer,proposal_state='validated_model_not_configured')
    aid=answer['pending'];row=book.get(aid)
    autonomous=policy.get('mode')=='autonomous'
    if autonomous and not hard_fact_question(row['question'],job.get('company','')):
        return await screening_answer(book,row,field,answer,job,policy,ollama,metrics)
    allowed=proposal_fact_sources(row['question'],row['context'],book.facts|book.project_evidence)
    if not allowed:
        return dict(answer,proposal_state='unsupported',proposal_reason='missing_facts')
    if (row['state']=='proposed' and row['facts_version']==book.reference_version(row['refs'])
            and row['refs'] and all(ref in allowed for ref in row['refs'])
            and valid_value(row['field'],row['proposal'])):
        if autonomous:return fact_accepted(book,row,field,answer,job,row['proposal'],row['refs'])
        return dict(answer,proposal_state='proposed',approval_required=True)
    fingerprint=digest({'field':row['field'],'context':row['context'],'facts':book.version,
        'repositories':digest(book.project_evidence),'model':ollama,
        'implementation':digest([code_identity(type(book).propose.__code__),code_identity(valid_value.__code__),code_identity(proposal_fact_sources.__code__),code_identity(demographic_question.__code__)])})
    run_id=job.get('_answer_run_id') or 'process:'+str(os.getpid())
    claim=uuid.uuid4().hex;instant=time.time()
    with book.store.db() as db:
        history=[json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE kind='automatic_proposal' ORDER BY id")]
        previous=next((h for h in reversed(history) if h.get('answer_id')==aid and h.get('fingerprint')==fingerprint),None)
        if previous:
            age=instant-previous['epoch'];state=previous['status']
            if state in ('proposed','unsupported') or age<min(900,max(30,policy.get('retry_seconds',900))):
                return dict(answer,proposal_state=state,proposal_reason=previous.get('reason'))
        started=sum(h.get('run_id')==run_id and h['status']=='started' for h in history)
        if started>=(run_budget(policy) if autonomous else min(50,max(1,int(policy.get('max_new_per_run',8))))):
            return dict(answer,proposal_state='run_budget_reached')
        event={'answer_id':aid,'fingerprint':fingerprint,'run_id':run_id,'claim':claim,'epoch':instant,'status':'started'}
        db.execute('INSERT INTO events(attempt_id,at,kind,payload) VALUES(?,?,?,?)',
                   ('answer:'+str(aid),now(),'automatic_proposal',json.dumps(event)))
        before=db.execute("SELECT COUNT(*) FROM events WHERE attempt_id=? AND kind='model_call'",('answer:'+str(aid),)).fetchone()[0]
    try:
        proposal=await book.propose(aid,{'provider':'ollama','ollama':ollama},job=job)
        state='proposed' if proposal.get('supported') is True else 'unsupported'
        reason=proposal.get('reason')
    except Exception as error:
        state='failed';reason=type(error).__name__
    finally:
        with book.store.db() as db:
            after=db.execute("SELECT COUNT(*) FROM events WHERE attempt_id=? AND kind='model_call'",('answer:'+str(aid),)).fetchone()[0]
        if metrics is not None:metrics['model']+=max(0,after-before)
    book.store.event('answer:'+str(aid),'automatic_proposal',dict(event,status=state,reason=reason,epoch=time.time()))
    if autonomous and state=='proposed':return fact_accepted(book,row,field,answer,job,proposal['value'],proposal['refs'])
    return dict(answer,proposal_state=state,proposal_reason=reason,approval_required=state=='proposed')


def run_budget(policy):
    return max(1,int(policy.get('autonomous_max_per_run',2000)))


def fact_accepted(book,row,field,answer,job,value,refs):
    """A grounded proposal for a checkable fact is accepted only when it names experience the facts show."""
    missing=unknown_terms(asked_text(row['question']),book.known_words()[1])
    if experience_question(row['question']) and (missing or not book.experience_supported(row['question'],refs)):
        return dict(answer,proposal_state='unsupported',proposal_reason='unknown_experience')
    return accepted(book,row,field,answer,value,refs)


def accepted(book,row,field,answer,value,refs):
    stored,bound=book.accept_model(row['id'],value,refs)
    mapped=mapped_answer(row['field'],field,stored)
    if mapped is None:return dict(answer,proposal_state='unmappable_options')
    return {'value':mapped,'refs':bound,'source':'autonomous','answer_id':row['id'],'approval_value':stored}


def screening_rules(book):
    """Identity of the rules that decide an experience is missing."""
    rules=(experience_question,asked_text,unknown_terms,hard_fact_question,type(book).experience_supported,type(book).known_words,type(book).tailor,type(book).screening_problem)
    return digest([code_identity(rule.__code__) for rule in rules])


async def screening_answer(book,row,field,answer,job,policy,ollama,metrics):
    """Narratives are tailored per job; choices and numbers are reused like approvals."""
    aid=row['id'];per_job=narrative_field(row['field'])
    if per_job and (kept:=book.tailored(aid,job['url'])):
        value=mapped_answer(row['field'],field,kept[0])
        if value is not None:return {'value':value,'refs':kept[1],'source':'autonomous'}
    run_id=job.get('_answer_run_id') or 'process:'+str(os.getpid())
    with book.store.db() as db:
        # Missing experience stays the candidate's question until the facts or these rules change.
        if db.execute("SELECT 1 FROM events WHERE attempt_id=? AND kind='screening_answer' AND json_extract(payload,'$.reason')='unknown_experience' "
                      "AND json_extract(payload,'$.facts_version')=?",('answer:'+str(aid),book.version+':'+screening_rules(book))).fetchone():
            return dict(answer,proposal_state='unsupported',proposal_reason='unknown_experience')
        spent=db.execute("SELECT COUNT(*) FROM events WHERE kind='screening_answer' AND json_extract(payload,'$.run_id')=?",(run_id,)).fetchone()[0]
        if spent>=run_budget(policy):return dict(answer,proposal_state='run_budget_reached')
        before=db.execute("SELECT COUNT(*) FROM events WHERE attempt_id=? AND kind='model_call'",('answer:'+str(aid),)).fetchone()[0]
    try:
        result=await book.tailor(aid,{'provider':'ollama','ollama':ollama},job=job)
    except Exception as error:
        result={'supported':False,'reason':type(error).__name__}
    finally:
        with book.store.db() as db:
            after=db.execute("SELECT COUNT(*) FROM events WHERE attempt_id=? AND kind='model_call'",('answer:'+str(aid),)).fetchone()[0]
        if metrics is not None:metrics['model']+=max(0,after-before)
    state='answered' if result.get('supported') is True else 'unsupported'
    book.store.event('answer:'+str(aid),'screening_answer',{'answer_id':aid,'run_id':run_id,'job_url':job['url'],'status':state,'reason':result.get('reason'),'missing':result.get('missing'),'facts_version':book.version+':'+screening_rules(book)})
    if state!='answered':return dict(answer,proposal_state=state,proposal_reason=result.get('reason'))
    if not per_job:return accepted(book,row,field,answer,result['value'],result['refs'])
    book.keep_tailored(aid,job['url'],result['value'],result['refs'])
    value=mapped_answer(row['field'],field,result['value'])
    if value is None:return dict(answer,proposal_state='unmappable_options')
    return {'value':value,'refs':result['refs'],'source':'autonomous'}


async def resolve_pending(data,limit=None,progress=None):
    """Answer stored pending questions offline, in their original job context; nothing is sent."""
    from collections import Counter
    from .application_safety import job_key
    from .easy_answers import AnswerBook
    data=Path(data);jobs=json.loads((data/'jobs.json').read_text(encoding='utf-8')) if (data/'jobs.json').exists() else {}
    by_key={job_key(url):job for url,job in jobs.items()}
    rows=[r for r in AnswerBook(data).list() if r['state'] in ('pending','proposed','stale') and r['jobs']]
    outcome=Counter();run_id='resolve-pending:'+uuid.uuid4().hex
    for row in rows[:limit]:
        url=row['jobs'][0];context=row['context']
        job=dict(by_key.get(job_key(url),{}),url=url,eligibility_country=context.get('country',''),form_language=context.get('language','unknown'),
                 salary_currency=context.get('currency',''),salary_period=context.get('period',''),_answer_run_id=run_id)
        result=await resolve_answer(AnswerBook(data),row['field'],job)
        state='answered' if 'value' in result else result.get('proposal_reason') or result.get('proposal_state') or 'pending'
        outcome[state]+=1
        if progress:progress(row,state)
    return dict(outcome)
