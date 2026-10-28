"""Persistent external queue, daily ceiling and per-attempt evidence/reporting."""
import asyncio
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
import json
from pathlib import Path
import re
import time
import uuid
from filelock import FileLock

from .application_safety import AttemptStore, SafetyError, job_key
from .easy_answers import AnswerBook,normalized
from .external_apply import execute_external,canonical_ats,platform_hold
from .external_catalog import identity


def core_title(title):
    title=re.sub(r'\[[^]]+\]','',title)
    title=re.sub(r'[,|]\s*(?:Brazil|Brasil|Colombia|Argentina)\.?\s*$','',title,flags=re.I)
    return normalized(title)


def remaining_budget(store):
    day=datetime.now(ZoneInfo('America/Fortaleza')).date().isoformat()
    with store.db() as db:count=db.execute('SELECT COUNT(*) FROM submissions WHERE local_day=?',(day,)).fetchone()[0]
    return max(0,10-count)


def queue(data):
    data=Path(data);catalog=json.loads((data/'external/catalog.json').read_text(encoding='utf-8'))
    triage=json.loads((data/'external/triage.json').read_text(encoding='utf-8'))
    by={identity(j):j for j in catalog['jobs']};store=AttemptStore(data)
    all_jobs=json.loads((data/'jobs.json').read_text(encoding='utf-8'))
    known={job_key(url):j for url,j in all_jobs.items()}
    protected={job_key(a['job_url']) for a in store.list() if a['state'] in ('confirmed','uncertain','running','submitted','awaiting_human')}
    protected_titles={core_title(known[k]['title']) for k in protected if k in known}
    kept=[];isolated=[];seen=set()
    for decision in triage['decisions']:
        if decision['state']!='eligible':continue
        key=(decision['platform'],decision['board'],decision['externalId']);job=by.get(key)
        if not job:continue
        title=core_title(job['title'])
        match=re.search(r'\b(?:job[\s-]*)?(\d{5})\b',job['title'],re.I)
        duplicate=(job['board'],match[1]) if match else (job['board'],title)
        if job_key(job['url']) in protected or title in protected_titles or duplicate in seen:
            isolated.append({'url':job['url'],'reason':'protected_or_possible_duplicate','title':job['title']});continue
        seen.add(duplicate)
        kept.append(dict(job,external_qualification=decision))
    # Cached readiness changes priority only; execution checks a fresh schema
    # and the live form before allowing a send intention.
    facts_version=AnswerBook(data).version
    def ready_priority(job):
        path=data/'external/preflight'/(job['board']+'-'+str(job['externalId'])+'.json')
        try:
            result=json.loads(path.read_text(encoding='utf-8'))
            age=(datetime.now(ZoneInfo('UTC'))-datetime.fromisoformat(result['checked_at'])).total_seconds()
            return not (result['ready_for_browser'] and result['facts_version']==facts_version
                        and result['url']==job['url'] and 0<=age<=3600)
        except (OSError,ValueError,KeyError,TypeError):return True
    kept.sort(key=lambda j:(ready_priority(j),0 if re.search('back.?end|java|python',j['title'],re.I) else 1,j['title']))
    (data/'external/isolated-duplicates.json').write_text(json.dumps(isolated,ensure_ascii=False,indent=2),encoding='utf-8')
    return kept


def update_projection(job,result):
    from .shared_config import read_jobs,update_job
    for url,old in read_jobs().items():
        if identity(old)==identity(job):
            update_job(url,status='applied' if result['state']=='confirmed' else 'uncertain' if result['state']=='uncertain' else 'blocked',
                       external_result=result['state'],attempt_id=result['id'],evidence_dir=result['evidence_dir'],error=result['message'],
                       applied_at=result['updated_at'] if result['state']=='confirmed' else old.get('applied_at'))


def update_reconciled_status(data,result,report_path):
    """Refresh a completed run's current view, preserving its original report."""
    path=Path(data)/'external/run-state.json'
    if not path.exists():return False
    status=json.loads(path.read_text(encoding='utf-8'))
    if status.get('running') or status.get('attempt_id')!=result['id']:return False
    status.update(stage=result['state'],result=result['state'],reconciled=True,report_path=str(report_path))
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(status,ensure_ascii=False),encoding='utf-8');temporary.replace(path)
    return True


async def run_external(data,body,status):
    from .config import load_settings
    data=Path(data);store=AttemptStore(data);started=time.monotonic();owned=set()
    (data/'reports').mkdir(exist_ok=True)
    status.setdefault('mode','external');status.setdefault('running',True)
    status.setdefault('answer_run_id',uuid.uuid4().hex)
    jobs=queue(data)
    if getattr(body,'platform',None):jobs=[j for j in jobs if j['platform']==body.platform]
    if body.job_url:jobs=[j for j in jobs if canonical_ats(j['url'])==canonical_ats(body.job_url)]
    elif body.job_urls:
        selected={canonical_ats(u) for u in body.job_urls};jobs=[j for j in jobs if canonical_ats(j['url']) in selected]
    jobs=jobs[:body.limit or len(jobs)]
    stop_file=data/'external/stop.json';stop_file.unlink(missing_ok=True)
    def save_status():
        path=data/'external/run-state.json';temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(status,ensure_ascii=False,default=str),encoding='utf-8');temporary.replace(path)
    async def watch():
        while True:
            if stop_file.exists():status['cancel_requested']=True
            save_status()
            await asyncio.sleep(.5)
    watcher=asyncio.create_task(watch())
    try:
        with FileLock(str(data/'application-worker.lock'),timeout=0):
            store.recover()
            for job in jobs:
                job=dict(job,_answer_run_id=status['answer_run_id'])
                if status.get('cancel_requested'):break
                if body.execution=='submit' and platform_hold(data,job['platform']):
                    status.setdefault('paused_channels',{})[job['platform']]='verification_diagnosis'
                    continue
                if body.execution=='submit' and not remaining_budget(store):status['stop_reason']='daily_limit';break
                qualification=job['external_qualification'];book=AnswerBook(data)
                if qualification['facts_version']!=book.reference_version(qualification['fact_refs']):continue
                if job['platform']=='greenhouse':
                    from .greenhouse_apply import preflight_greenhouse
                    status['stage']='preflight_external'
                    try:readiness=await preflight_greenhouse(data,dict(job,eligibility_country='BR',form_language='en'))
                    except Exception as error:
                        status.setdefault('job_errors',[]).append({'url':job['url'],'reason':str(error)[:300]});continue
                    status.setdefault('preflight',[]).append(readiness)
                    if not readiness['ready_for_browser']:continue
                status.update(stage='open_external',job_url=job['url'])
                previous={a['id'] for a in store.list()}
                try:
                    result=await execute_external(job,data,load_settings().get('resume_path',''),body.execution,status,lease_held=True)
                except SafetyError as error:
                    status.setdefault('job_errors',[]).append({'url':job['url'],'reason':str(error)})
                    continue
                finally:
                    owned.update(a['id'] for a in store.list() if a['id'] not in previous)
                update_projection(job,result)
                status.update(stage=result['state'],result=result['state'])
    finally:
        watcher.cancel()
        try:await watcher
        except asyncio.CancelledError:pass
        attempts=[store.get(aid) for aid in owned]
        report={'execution':body.execution,'seconds':round(time.monotonic()-started,2),'daily_remaining':remaining_budget(store),
                'outcomes':dict(Counter(a['state'] for a in attempts)),
                'attempts':[{'id':a['id'],'url':a['job_url'],'state':a['state'],'message':a['message'],'evidence_dir':a['evidence_dir']} for a in attempts],
                'submit_intents':sum(len(store.submissions(a['id'])) for a in attempts),'stop_reason':status.get('stop_reason'),
                'considered_jobs':len(jobs),'paused_channels':status.get('paused_channels',{}),'job_errors':status.get('job_errors',[]),
                'preflight':status.get('preflight',[]),
                'answers':{'rule':0,'cache':0,'pending':0,'model':0}}
        report['preflight_answers']={key:sum(p['answer_metrics'][key] for p in report['preflight']) for key in ('rule','cache','pending','model')}
        with store.db() as db:
            for a in attempts:
                for event in db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='answer_metrics'",(a['id'],)):
                    for key,value in json.loads(event[0]).items():report['answers'][key]+=value
        path=data/'reports'/('external-'+uuid.uuid4().hex+'.json');path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        status.update(running=False,report_path=str(path));save_status()
        print('External report:',path,report['outcomes'],'daily remaining',report['daily_remaining'],flush=True)
