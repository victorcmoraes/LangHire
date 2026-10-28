"""Generate one local proposal per unresolved question; never approve it."""
import json
import os
from pathlib import Path
import time
import types
import uuid

from .application_safety import now
from .easy_answers import digest,valid_value,proposal_fact_sources,demographic_question


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
    if (ollama.get('model') or '').split('@')[0]!='qwen3:8b':
        return dict(answer,proposal_state='qwen3_8b_not_configured')
    aid=answer['pending'];row=next(r for r in book.list() if r['id']==aid)
    allowed=proposal_fact_sources(row['question'],row['context'],book.facts|book.project_evidence)
    if not allowed:
        return dict(answer,proposal_state='unsupported',proposal_reason='missing_facts')
    if (row['state']=='proposed' and row['facts_version']==book.reference_version(row['refs'])
            and row['refs'] and all(ref in allowed for ref in row['refs'])
            and valid_value(row['field'],row['proposal'])):
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
        if started>=min(50,max(1,int(policy.get('max_new_per_run',8)))):
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
    return dict(answer,proposal_state=state,proposal_reason=reason,approval_required=state=='proposed')
