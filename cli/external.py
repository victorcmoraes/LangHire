"""Sequential external ATS queue. Local data, reusable answers and durable receipts."""
import argparse
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import sys
import uuid
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
os.environ.setdefault('LANGHIRE_DATA_DIR',str(ROOT/'.local'))
os.environ.setdefault('ANONYMIZED_TELEMETRY','false')
from backend.core.config import get_data_dir
from backend.core.application_safety import AttemptStore
from backend.core.external_service import run_external,remaining_budget,queue
from backend.core.external_qualification import triage
from backend.core.external_catalog import refresh_imported

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('status','collect','triage','stop'):sub.add_parser(name)
    run=sub.add_parser('run');run.add_argument('--execution',choices=('review','submit'),default='review')
    run.add_argument('--limit',type=int,help='Maximum jobs this run; defaults to the complete eligible queue');run.add_argument('--platform',choices=('lever','greenhouse'))
    run.add_argument('--job-url')
    preflight=sub.add_parser('preflight');preflight.add_argument('--limit',type=int,default=50)
    export=sub.add_parser('export');export.add_argument('--tracker',type=Path)
    reconcile=sub.add_parser('reconcile-receipt');reconcile.add_argument('--attempt-id',required=True)
    reconcile.add_argument('--capture',required=True);reconcile.add_argument('--screenshot',required=True)
    reconcile.add_argument('--mail-receipt',required=True)
    args=parser.parse_args();data=get_data_dir();store=AttemptStore(data)
    if args.command=='preflight':
        from backend.core.greenhouse_apply import preflight_greenhouse
        from backend.core.easy_answers import digest
        from backend.core.application_safety import now
        if args.limit<1:parser.error('--limit must be positive')
        (data/'reports').mkdir(parents=True,exist_ok=True)
        async def inspect_queue():
            run_id=uuid.uuid4().hex
            selected=[j for j in queue(data) if j['platform']=='greenhouse'][:args.limit]
            results=[];errors=[];semaphore=asyncio.Semaphore(3)
            async def inspect(job):
                async with semaphore:
                    try:results.append(await preflight_greenhouse(data,dict(job,eligibility_country='BR',form_language='en',_answer_run_id=run_id)))
                    except Exception as error:errors.append({'url':job['url'],'reason':str(error)[:300]})
            await asyncio.gather(*(inspect(job) for job in selected))
            report={'checked_at':now(),'considered':len(selected),'ready_for_browser':sum(r['ready_for_browser'] for r in results),
                    'pending':sum(not r['ready_for_browser'] for r in results),'errors':errors,'jobs':results,
                    'answers':{k:sum(r['answer_metrics'][k] for r in results) for k in ('rule','cache','pending','model')}}
            report['distinct_pending_answers']=len({p.get('answer_id',digest(p)) for r in results for p in r['pending']})
            path=data/'reports'/('external-preflight-'+report['checked_at'].replace(':','').replace('.','')+'.json')
            path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            return {k:v for k,v in report.items() if k!='jobs'}|{'report_path':str(path)}
        triage(data);print(json.dumps(asyncio.run(inspect_queue()),ensure_ascii=False,indent=2));return
    if args.command=='reconcile-receipt':
        import sqlite3,shutil
        from filelock import FileLock
        from backend.core.external_service import update_projection,update_reconciled_status
        with FileLock(str(data/'application-worker.lock'),timeout=0):
            backup=data/'backups'/('receipt-reconciliation-'+uuid.uuid4().hex);backup.mkdir(parents=True)
            with sqlite3.connect(str(store.path)) as original:
                with sqlite3.connect(str(backup/store.path.name)) as target:original.backup(target)
            shutil.copy2(data/'jobs.json',backup/'jobs.json')
            result=store.reconcile_confirmed_receipt(args.attempt_id,args.capture,args.screenshot,args.mail_receipt)
            catalog=json.loads((data/'external/catalog.json').read_text(encoding='utf-8'))
            job=next(j for j in catalog['jobs'] if j['url']==result['job_url'])
            update_projection(job,result)
            report={'attempt_id':result['id'],'url':result['job_url'],'state':result['state'],'previous_state':'uncertain',
                    'message':result['message'],'resubmitted':False,'evidence_dir':result['evidence_dir']}
            report_path=data/'reports'/('receipt-reconciliation-'+result['id']+'.json')
            report_path.write_text(json.dumps(report,indent=2),encoding='utf-8')
            update_reconciled_status(data,result,report_path)
        print(json.dumps(report,ensure_ascii=False,indent=2));return
    if args.command=='export':
        from backend.core.external_export import export_confirmed
        print(json.dumps(export_confirmed(data,args.tracker or data/'reports/applications.csv'),ensure_ascii=False,indent=2));return
    if args.command=='stop':
        (data/'external/stop.json').write_text('{"stop":true}',encoding='utf-8');print('Stop requested; progress and uncertain sends are preserved.');return
    if args.command=='collect':
        refreshed=refresh_imported(data);qualified=triage(data)
        print(json.dumps({'active':len(refreshed['jobs']),'boards':refreshed['boards'],'triage':qualified['states']},ensure_ascii=False,indent=2));return
    if args.command=='triage':
        result=triage(data);print(json.dumps({k:v for k,v in result.items() if k!='decisions'},ensure_ascii=False,indent=2));return
    if args.command=='status':
        result={'daily_limit':store.daily_limit(),'daily_limit_enabled':store.daily_limit() is not None,
                'daily_remaining':remaining_budget(store),'attempt_states':dict(Counter(a['state'] for a in store.list()))}
        with store.db() as db:
            confirmed=db.execute("SELECT a.job_url,EXISTS(SELECT 1 FROM events e WHERE e.attempt_id=a.id AND e.kind='legacy_import') AS historical FROM attempts a WHERE a.state='confirmed'").fetchall()
        result['confirmed_new']=sum(not r['historical'] for r in confirmed)
        result['confirmed_historical']=sum(bool(r['historical']) for r in confirmed)
        result['confirmed_external_new']=sum(not r['historical'] and 'linkedin.com' not in r['job_url'] for r in confirmed)
        contact=data/'pending-contact-verification.json'
        result['contact_confirmation_pending']=contact.exists() and json.loads(contact.read_text(encoding='utf-8')).get('active',False)
        if (data/'external/triage.json').exists():result['triage']=json.loads((data/'external/triage.json').read_text(encoding='utf-8'))['states']
        result['platform_holds']={p.name:json.loads(p.read_text(encoding='utf-8')).get('reason') for p in (data/'external').glob('*-verification-hold.json') if json.loads(p.read_text(encoding='utf-8')).get('active')}
        print(json.dumps(result,ensure_ascii=False,indent=2));return
    if args.limit is not None and args.limit<1:parser.error('--limit must be positive')
    triage(data)
    body=SimpleNamespace(execution=args.execution,limit=args.limit,platform=args.platform,job_url=args.job_url,job_urls=None)
    status={};asyncio.run(run_external(data,body,status));print(json.dumps(status,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
