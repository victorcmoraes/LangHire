"""Durable, measurable external application batches with one shared browser."""
import asyncio
from collections import Counter
from contextlib import AsyncExitStack
import json
from pathlib import Path
import time
import uuid
from filelock import FileLock

from .application_safety import AttemptStore,SafetyError,job_key,now
from .easy_answers import AnswerBook,equivalent_key,digest
from .external_qualification import triage,qualify
from .external_service import queue,remaining_budget,update_projection
from .external_apply import execute_external,platform_hold
from .browser_runtime import persistent_browser


def batch_hold(data,platform,execution):
    if not platform_hold(data,platform):return False
    path=Path(data)/'external'/(platform+'-verification-hold.json')
    hold=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    return execution=='submit' or hold.get('account_restriction_confirmed') is True or hold.get('allow_read_only_preparation') is not True


class BatchStore:
    def __init__(self,data):
        self.data=Path(data);self.attempts=AttemptStore(self.data)
        with self.attempts.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS external_batches (
                id TEXT PRIMARY KEY,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                state TEXT NOT NULL,config TEXT NOT NULL,inventory TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS external_batch_items (
                batch_id TEXT NOT NULL,job_key TEXT NOT NULL,position INTEGER NOT NULL,
                job TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'queued',detail TEXT NOT NULL DEFAULT '{}',
                attempt_id TEXT,updated_at TEXT NOT NULL,PRIMARY KEY(batch_id,job_key));''')

    def create(self,target,execution,platform=None):
        if type(target) is not int or target<1:raise SafetyError('Batch target must be positive')
        if execution not in ('review','submit'):raise SafetyError('Invalid batch execution mode')
        qualification=triage(self.data);stock=queue(self.data)
        if platform:stock=[j for j in stock if j['platform']==platform]
        paused=[j for j in stock if batch_hold(self.data,j['platform'],execution)]
        jobs=[j for j in stock if j not in paused][:target]
        # Canonical identities are authoritative, including hosted URL aliases.
        unique={}
        for job in jobs:unique.setdefault(job_key(job['url']),job)
        jobs=list(unique.values());bid=uuid.uuid4().hex
        inventory={'qualification':qualification['states'],'eligible_queue':len(stock),'paused_jobs':len(paused),
                   'selected':len(jobs),'target_shortfall':max(0,target-len(jobs))}
        with self.attempts.db() as db:
            db.execute('INSERT INTO external_batches VALUES(?,?,?,?,?,?)',
                (bid,now(),now(),'created',json.dumps({'target':target,'execution':execution,'platform':platform}),json.dumps(inventory)))
            for position,job in enumerate(jobs):
                db.execute('INSERT INTO external_batch_items(batch_id,job_key,position,job,updated_at) VALUES(?,?,?,?,?)',
                    (bid,job_key(job['url']),position,json.dumps(job),now()))
        return bid

    def get(self,bid):
        with self.attempts.db() as db:
            row=db.execute('SELECT * FROM external_batches WHERE id=?',(bid,)).fetchone()
            if not row:raise SafetyError('Unknown batch')
            result=dict(row);result['config']=json.loads(result['config']);result['inventory']=json.loads(result['inventory'])
            result['items']=[]
            for raw in db.execute('SELECT * FROM external_batch_items WHERE batch_id=? ORDER BY position',(bid,)):
                item=dict(raw);item['job']=json.loads(item['job']);item['detail']=json.loads(item['detail']);result['items'].append(item)
            return result

    def next_ready(self,bid):
        with self.attempts.db() as db:
            row=db.execute("SELECT * FROM external_batch_items WHERE batch_id=? AND state='ready' ORDER BY position LIMIT 1",(bid,)).fetchone()
        if row is None:return None
        item=dict(row);item['job']=json.loads(item['job']);item['detail']=json.loads(item['detail']);return item

    def state(self,bid,state):
        with self.attempts.db() as db:db.execute('UPDATE external_batches SET state=?,updated_at=? WHERE id=?',(state,now(),bid))

    def item(self,bid,url,state,detail=None,attempt_id=None):
        with self.attempts.db() as db:
            db.execute('UPDATE external_batch_items SET state=?,detail=?,attempt_id=?,updated_at=? WHERE batch_id=? AND job_key=?',
                (state,json.dumps(detail or {}),attempt_id,now(),bid,job_key(url)))

    def refresh_snapshots(self,bid):
        path=self.data/'external/catalog.json'
        rows={job_key(j['url']):j for j in json.loads(path.read_text(encoding='utf-8'))['jobs']}
        for item in self.get(bid)['items']:
            if item['state'] in ('confirmed','uncertain','submitted','awaiting_human'):continue
            fresh=rows.get(item['job_key'])
            if fresh is None:self.item(bid,item['job']['url'],'unavailable',{'reason':'not_in_current_catalog'});continue
            fresh=dict(fresh,collection_origin=item['job'].get('collection_origin',fresh.get('collection_origin')))
            if digest(fresh)!=digest(item['job']):
                self.attempts.event('batch:'+bid,'batch_job_snapshot_updated',{'job_key':item['job_key'],'previous':item['job'],'current_sha256':digest(fresh)})
                with self.attempts.db() as db:db.execute('UPDATE external_batch_items SET job=?,updated_at=? WHERE batch_id=? AND job_key=?',(json.dumps(fresh),now(),bid,item['job_key']))

    def sync_receipts(self,bid):
        """Reconcile manifest after a crash; a manifest can never authorize resending."""
        known={}
        for a in self.attempts.list():known.setdefault(job_key(a['job_url']),[]).append(a)
        for item in self.get(bid)['items']:
            history=known.get(item['job_key'],[])
            protected=next((a for a in history if a['state']=='confirmed'),None)
            protected=protected or next((a for a in history if a['state'] in ('uncertain','submitted','awaiting_human','running')),None)
            if protected:self.item(bid,item['job']['url'],protected['state'],{'reason':protected['message'],'reconciled_from_attempts':True},protected['id'])

    def pending(self,bid):
        book=AnswerBook(self.data);answers={r['id']:r for r in book.list()};groups={}
        for item in self.get(bid)['items']:
            for pending in item['detail'].get('pending',[]):
                row=answers.get(pending.get('answer_id'))
                if row:
                    current=book.resolve(row['field'],dict(item['job'],eligibility_country='BR',form_language=row['context'].get('language','en')))
                    if 'value' in current:continue
                    if isinstance(current.get('pending'),int) and current['pending'] not in answers:
                        answers.update({r['id']:r for r in book.list()})
                    row=answers.get(current.get('pending'),row)
                key=equivalent_key(row['field'],row['context']) if row else digest(pending)
                group=groups.setdefault(key,{'key':key,'question':pending['question'],
                    'context':row['context'] if row else {},'answer_ids':[],'job_urls':[],
                    'state':row['state'] if row else 'unsupported','proposal':row['proposal'] if row else None})
                if row and row['id'] not in group['answer_ids']:group['answer_ids'].append(row['id'])
                if item['job']['url'] not in group['job_urls']:group['job_urls'].append(item['job']['url'])
        return list(groups.values())

    def report(self,bid,seconds=0,model_metrics=None,discovery=None,run_started=None,run_id=None):
        batch=self.get(bid);counts=Counter(i['state'] for i in batch['items']);attempt_ids=set()
        selected={i['job_key'] for i in batch['items']}
        # Count only attempts linked to this manifest, not unrelated historical activity.
        with self.attempts.db() as db:
            payloads=db.execute("SELECT payload FROM events WHERE kind='external_batch'",()).fetchall()
            for row in payloads:
                p=json.loads(row[0])
                if p.get('batch_id')==bid:attempt_ids.add(p['attempt_id'])
        attempts=[self.attempts.get(a) for a in sorted(attempt_ids)]
        # Imported/reconciled history is reported separately from new work.
        new_attempts=[a for a in attempts if a['created_at']>=batch['created_at'] and job_key(a['job_url']) in selected]
        current_attempts=[a for a in new_attempts if not run_started or a['created_at']>=run_started]
        browser_metrics={'rule':0,'cache':0,'pending':0,'model':0,'autonomous':0}
        with self.attempts.db() as db:
            for a in current_attempts:
                for row in db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='answer_metrics'",(a['id'],)):
                    for k,v in json.loads(row[0]).items():browser_metrics[k]+=v
        report={'batch_id':bid,'state':batch['state'],'target':batch['config']['target'],'execution':batch['config']['execution'],
                'inventory':batch['inventory'],'selected_jobs':len(batch['items']),'job_outcomes':dict(counts),
                'new_confirmations':sum(a['state']=='confirmed' for a in current_attempts),
                'batch_confirmations':sum(a['state']=='confirmed' for a in new_attempts),
                'native_send_intents':sum(len(self.attempts.submissions(a['id'])) for a in current_attempts),
                'batch_native_send_intents':sum(len(self.attempts.submissions(a['id'])) for a in new_attempts),
                'seconds':round(seconds,2),'daily_remaining':remaining_budget(self.attempts),
                'confirmed_per_hour':round(sum(a['state']=='confirmed' for a in current_attempts)*3600/seconds,2) if seconds else 0,
                'preflight_answer_metrics':model_metrics or {},'browser_answer_metrics':browser_metrics,'pending_groups':self.pending(bid),
                'items':[{'url':i['job']['url'],'title':i['job']['title'],'platform':i['job']['platform'],'state':i['state'],
                          'attempt_id':i['attempt_id'],'detail':i['detail']} for i in batch['items']],
                'discovery':discovery}
        path=self.data/'reports'/('batch-'+bid+('-'+run_id if run_id else '')+'.json');path.parent.mkdir(exist_ok=True)
        report['report_path']=str(path);path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        if run_id:(self.data/'reports'/('batch-'+bid+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return report


async def prepare_batch(store,bid,metrics,answer_run_id=None):
    from .greenhouse_apply import preflight_greenhouse
    semaphore=asyncio.Semaphore(3);batch=store.get(bid)
    async def prepare(item):
        if item['state'] in ('confirmed','uncertain','submitted','awaiting_human','reviewed','unavailable'):return
        job=dict(item['job'],eligibility_country='BR',form_language='en',_answer_run_id=answer_run_id or bid)
        if batch_hold(store.data,job['platform'],batch['config']['execution']):
            store.item(bid,job['url'],'paused',{'reason':'platform_hold'});return
        if job['platform']!='greenhouse':store.item(bid,job['url'],'ready',{'reason':'hosted_inspection_required'});return
        async with semaphore:
            decision=qualify(job,AnswerBook(store.data).facts)
            if decision['state']!='eligible':store.item(bid,job['url'],'pending',{'reason':'qualification_changed','qualification':decision});return
            try:
                result=await preflight_greenhouse(store.data,job)
                for key,value in result['answer_metrics'].items():metrics[key]+=value
                store.item(bid,job['url'],'ready' if result['ready_for_browser'] else 'pending',result)
            except Exception as error:store.item(bid,job['url'],'failed',{'reason':type(error).__name__,'message':str(error)[:300]})
    await asyncio.gather(*(prepare(i) for i in batch['items']))


async def run_batch(data,bid,*,discovery=None):
    from .config import load_settings
    store=BatchStore(data);started=time.monotonic();run_started=now();run_id=uuid.uuid4().hex;metrics={'rule':0,'cache':0,'pending':0,'model':0,'autonomous':0}
    batch=store.get(bid);execution=batch['config']['execution'];status={'mode':'external_batch','batch_id':bid,'answer_run_id':run_id,'running':True}
    stop=store.data/'external/stop.json'
    async def watch():
        while True:
            if stop.exists():status['cancel_requested']=True
            path=store.data/'external/run-state.json';temporary=path.with_suffix('.tmp')
            # Windows refuses the replace while a reader (desktop UI, monitor) has the file open; retry next tick.
            try:temporary.write_text(json.dumps(status,ensure_ascii=False,default=str),encoding='utf-8');temporary.replace(path)
            except PermissionError:pass
            await asyncio.sleep(.5)
    watcher=None;preparer=None
    try:
        with FileLock(str(store.data/'external-batch.lock'),timeout=0):
            # The same exclusive lease guards recovery and every application.
            with FileLock(str(store.data/'application-worker.lock'),timeout=0):
                stop.unlink(missing_ok=True)
                store.attempts.recover();store.sync_receipts(bid);store.refresh_snapshots(bid);store.state(bid,'preparing')
                watcher=asyncio.create_task(watch());preparer=asyncio.create_task(prepare_batch(store,bid,metrics,run_id))
                store.state(bid,'running')
                async with AsyncExitStack() as stack:
                    session=None
                    while True:
                        if status.get('cancel_requested'):store.state(bid,'interrupted');break
                        item=store.next_ready(bid)
                        if item is None:
                            if preparer.done():
                                await preparer;store.state(bid,'completed');break
                            await asyncio.sleep(.2);continue
                        if execution=='submit' and remaining_budget(store.attempts)==0:
                            store.state(bid,'daily_limit');break
                        job=dict(item['job'],_answer_run_id=run_id,_batch_id=bid);book=AnswerBook(store.data)
                        decision=qualify(job,book.facts)
                        if decision['state']!='eligible':store.item(bid,job['url'],'pending',{'reason':'qualification_changed','qualification':decision});continue
                        decision['facts_version']=book.reference_version(decision['fact_refs']);job['external_qualification']=decision
                        if batch_hold(store.data,job['platform'],execution):store.item(bid,job['url'],'paused',{'reason':'platform_hold'});continue
                        store.item(bid,job['url'],'running',{'reason':'browser_preparation'})
                        if session is None:session=await stack.enter_async_context(persistent_browser(store.data))
                        before={a['id'] for a in store.attempts.list()}
                        try:
                            result=await execute_external(job,store.data,load_settings().get('resume_path',''),execution,status,lease_held=True,session=session)
                            detail={'reason':result['message']}
                            pending_path=Path(result['evidence_dir'])/'pending-answers.json'
                            if pending_path.exists():detail['pending']=json.loads(pending_path.read_text(encoding='utf-8'))
                            store.item(bid,job['url'],result['state'],detail,result['id']);update_projection(job,result,store.data)
                            status.update(result=result['state'],stage=result['state'])
                        except asyncio.CancelledError:raise
                        except Exception as error:
                            store.item(bid,job['url'],'failed',{'reason':type(error).__name__,'message':str(error)[:300]})
                        finally:
                            for a in store.attempts.list():
                                if a['id'] not in before:
                                    with store.attempts.db() as db:owned=db.execute("SELECT 1 FROM events WHERE attempt_id=? AND kind='external_batch' AND json_extract(payload,'$.batch_id')=?",(a['id'],bid)).fetchone()
                                    if not owned:store.attempts.event(a['id'],'external_batch',{'batch_id':bid,'attempt_id':a['id']})
                store.sync_receipts(bid)
    except BaseException:
        # Do not overwrite another worker's live batch if we never acquired its lease.
        if watcher is not None:store.state(bid,'interrupted')
        raise
    finally:
        if preparer is not None and not preparer.done():
            preparer.cancel()
            try:await preparer
            except asyncio.CancelledError:pass
        if watcher is not None:
            watcher.cancel()
            try:await watcher
            except asyncio.CancelledError:pass
            status['running']=False
            try:(store.data/'external/run-state.json').write_text(json.dumps(status,ensure_ascii=False,default=str),encoding='utf-8')
            except PermissionError:pass
            report=store.report(bid,time.monotonic()-started,metrics,discovery,run_started,run_id)
    return report
