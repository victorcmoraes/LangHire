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
    # Hosted questions can contain any Unicode character, including ATS symbols.
    # Windows redirected stdout otherwise defaults to a legacy code page.
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('status','collect','triage','stop'):sub.add_parser(name)
    evidence=sub.add_parser('evidence',help='Locate and verify arrangement quotes locally; no browser, no sends')
    evidence.add_argument('--limit',type=int)
    run=sub.add_parser('run');run.add_argument('--execution',choices=('review','submit'),default='review')
    run.add_argument('--limit',type=int,help='Maximum jobs this run; defaults to the complete eligible queue');run.add_argument('--platform',choices=('lever','greenhouse','recruitee'))
    run.add_argument('--job-url')
    discover=sub.add_parser('discover');discover.add_argument('--board',action='append',default=[]);discover.add_argument('--max-new',type=int,default=5000)
    discover.add_argument('--aggregator-pages',type=int,default=5)
    boards=sub.add_parser('boards');board_commands=boards.add_subparsers(dest='boards_command',required=True)
    suggest=board_commands.add_parser('suggest');suggest.add_argument('--seeds',type=Path);suggest.add_argument('--limit',type=int,default=25)
    add=board_commands.add_parser('add');add.add_argument('--board',action='append',required=True)
    batch=sub.add_parser('batch');batch.add_argument('--target',type=int,default=100)
    batch.add_argument('--execution',choices=('review','submit'));batch.add_argument('--platform',choices=('lever','greenhouse','recruitee'))
    batch.add_argument('--collect',action='store_true');batch.add_argument('--board',action='append',default=[])
    batch.add_argument('--resume');batch.add_argument('--tracker',type=Path)
    batch_status=sub.add_parser('batch-status');batch_status.add_argument('batch_id')
    pending=sub.add_parser('pending');pending.add_argument('--batch-id',required=True)
    approve=sub.add_parser('approve-answer');approve.add_argument('--id',type=int,required=True)
    value=approve.add_mutually_exclusive_group(required=True);value.add_argument('--text');value.add_argument('--json-value');value.add_argument('--value-file',type=Path)
    approve.add_argument('--currency');approve.add_argument('--period',choices=('monthly','annual','hourly'))
    sources=sub.add_parser('sources',help='Find ATS boards of companies hiring in Brazil from aggregators and GitHub job boards')
    sources.add_argument('--register',action='store_true',help='Register boards that have new eligible jobs')
    sources.add_argument('--source',action='append',choices=('linkedin','gupy','github','himalayas','workable','getonbrd'))
    sources.add_argument('--limit',type=int)
    linkedin=sub.add_parser('linkedin-collect',help='Collect LinkedIn postings; --external also keeps off-site Apply destinations')
    linkedin.add_argument('--title',action='append',required=True);linkedin.add_argument('--limit',type=int,default=10)
    linkedin.add_argument('--location');linkedin.add_argument('--date-posted',default='r604800')
    linkedin.add_argument('--work-type',default='2',help='LinkedIn f_WT: 1 on-site, 2 remote, 3 hybrid')
    linkedin.add_argument('--external',action='store_true')
    for name in ('assist','lever-assist'):
        assist=sub.add_parser(name,help="Serve the whole queue to the Apply Assist extension in the candidate's own Chrome; the candidate reviews, solves any CAPTCHA and submits")
        assist.add_argument('--no-open',action='store_true')
        assist.add_argument('--platform',action='append',choices=('lever','greenhouse','gupy','ashby','workable','linkedin'),
                            help='Limit and order the chain (repeatable); default: lever, greenhouse, gupy, ashby, workable, linkedin')
        assist.add_argument('--write-extension',action='store_true',help='Regenerate the extension field inspectors and exit')
    diagnose=sub.add_parser('diagnose-lever');diagnose.add_argument('--job-url',required=True)
    preflight=sub.add_parser('preflight');preflight.add_argument('--limit',type=int,default=50)
    resolve=sub.add_parser('resolve-pending',help='Answer stored pending questions with the local model; no browser, no sends')
    resolve.add_argument('--limit',type=int)
    export=sub.add_parser('export');export.add_argument('--tracker',type=Path)
    reconcile=sub.add_parser('reconcile-receipt');reconcile.add_argument('--attempt-id',required=True)
    reconcile.add_argument('--capture',required=True);reconcile.add_argument('--screenshot',required=True)
    reconcile.add_argument('--mail-receipt',required=True)
    args=parser.parse_args();data=get_data_dir();store=AttemptStore(data)
    if args.command=='evidence':
        from backend.core.arrangement_evidence import extract
        if args.limit is not None and args.limit<1:parser.error('--limit must be positive')
        report=asyncio.run(extract(data,args.limit));report['triage']=triage(data)['states']
        print(json.dumps(report,ensure_ascii=False,indent=2));return
    if args.command=='boards':
        from backend.core.external_boards import suggest_boards,add_boards
        report=suggest_boards(data,args.seeds,args.limit) if args.boards_command=='suggest' else add_boards(data,args.board)
        print(json.dumps(report,ensure_ascii=False,indent=2));return
    if args.command=='approve-answer':
        from backend.core.easy_answers import AnswerBook
        if bool(args.currency)!=bool(args.period):parser.error('Currency and period must be supplied together')
        value=args.text if args.text is not None else json.loads(args.value_file.read_text(encoding='utf-8') if args.value_file else args.json_value)
        row=AnswerBook(data).approve(args.id,value,{'currency':args.currency,'period':args.period} if args.currency else None)
        print(json.dumps({'id':row['id'],'state':row['state'],'context':row['context'],'jobs_affected':len(row['jobs'])},ensure_ascii=False,indent=2));return
    if args.command=='resolve-pending':
        from backend.core.automatic_answers import resolve_pending
        show=lambda row,state:print(json.dumps({'id':row['id'],'question':row['question'][:90],'result':state},ensure_ascii=False),flush=True)
        print(json.dumps(asyncio.run(resolve_pending(data,args.limit,show)),ensure_ascii=False,indent=2));return
    if args.command in ('assist','lever-assist'):
        from filelock import FileLock
        from backend.core.apply_assist import serve,write_extension,PLATFORMS
        if args.write_extension:print(write_extension());return
        from backend.core.config import load_settings
        # Holding the worker lease keeps automatic runs from recovering the attempts the candidate is filling.
        with FileLock(str(data/'application-worker.lock'),timeout=0):
            triage(data);serve(data,load_settings().get('resume_path',''),not args.no_open,args.platform or (['lever'] if args.command=='lever-assist' else PLATFORMS))
        return
    if args.command=='diagnose-lever':
        from filelock import FileLock
        import shutil
        from backend.core.application_safety import SafetyError,job_key,now
        from backend.core.external_apply import execute_external
        from backend.core.external_service import update_projection
        from backend.core.config import load_settings
        with FileLock(str(data/'application-worker.lock'),timeout=0):
            triage(data)
            job=next((j for j in queue(data) if j['platform']=='lever' and job_key(j['url'])==job_key(args.job_url)),None)
            if not job:raise SafetyError('Diagnostic requires a new eligible Lever job; protected identities cannot be retried')
            path=data/'external/lever-verification-hold.json';hold=json.loads(path.read_text(encoding='utf-8'))
            if not hold.get('active') or hold.get('scope')!='submission_diagnostics' or hold.get('account_restriction_confirmed') is not False:
                raise SafetyError('Only a technical verification hold can be diagnosed; account restrictions remain suspended')
            backup=data/'backups'/('lever-diagnostic-'+uuid.uuid4().hex);backup.mkdir(parents=True);shutil.copy2(path,backup/path.name)
            hold.setdefault('diagnostic_allow_once',{})[job_key(job['url'])]={'authorized_at':now(),'reason':'one native submission to diagnose transport; human CAPTCHA; no replay'}
            temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(hold,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)
            result=asyncio.run(execute_external(job,data,load_settings().get('resume_path',''),'submit',{},lease_held=True))
            update_projection(job,result,data)
            report={k:result[k] for k in ('id','job_url','state','message','evidence_dir')}
            report['platform_hold_preserved']=True
            report_path=data/'reports'/('lever-diagnostic-'+result['id']+'.json');report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2));return
    if args.command=='linkedin-collect':
        from backend.core.easy_service import collect
        from backend.core.config import load_profile
        filters={'date_posted':args.date_posted,'work_type':args.work_type,'location':args.location,'apply_mode':'external' if args.external else None}
        status={}
        for title in args.title:asyncio.run(collect(data,load_profile(),title,args.limit,{k:v for k,v in filters.items() if v},status))
        print(json.dumps({k:status.get(k,0) for k in ('collected','collected_external')}));return
    if args.command=='sources':
        from backend.core.job_sources import discover_sources
        report=discover_sources(data,args.register,tuple(args.source or ('linkedin','gupy','github','himalayas','workable','getonbrd')),limit=args.limit)
        print(json.dumps({k:v for k,v in report.items() if k!='boards'}|{'top':[{k:b.get(k) for k in ('platform','board','new_eligible_jobs','new_matching_jobs','sources')} for b in report['boards'][:25]]},ensure_ascii=False,indent=2));return
    if args.command=='discover':
        from backend.core.external_discovery import discover as discover_boards
        report=discover_boards(data,args.board,args.max_new,aggregator_pages=args.aggregator_pages)
        print(json.dumps({k:v for k,v in report.items() if k not in ('new','boards')},ensure_ascii=False,indent=2));return
    if args.command in ('batch','batch-status','pending'):
        from backend.core.external_batch import BatchStore,run_batch
        batches=BatchStore(data)
        if args.command=='pending':print(json.dumps(batches.pending(args.batch_id),ensure_ascii=False,indent=2));return
        if args.command=='batch-status':
            b=batches.get(args.batch_id)
            print(json.dumps({'batch_id':b['id'],'state':b['state'],'config':b['config'],'inventory':b['inventory'],
                'outcomes':dict(Counter(i['state'] for i in b['items']))},ensure_ascii=False,indent=2));return
        discovery=None
        if args.resume:
            if args.collect or args.board:parser.error('Resume uses the persisted batch; collect separately')
            bid=args.resume
            saved=batches.get(bid)['config']
            if args.execution and args.execution!=saved['execution']:parser.error('Execution mode is fixed in the saved batch; create another batch to change it')
            if args.platform and args.platform!=saved['platform']:parser.error('Platform selection is fixed in the saved batch')
        else:
            if args.collect or args.board:
                from backend.core.external_discovery import discover as discover_boards
                d=discover_boards(data,args.board);discovery={k:v for k,v in d.items() if k!='new'}
            bid=batches.create(args.target,args.execution or 'review',args.platform)
        print('Batch:',bid,flush=True)
        report=asyncio.run(run_batch(data,bid,discovery=discovery))
        if args.tracker:
            from backend.core.external_export import export_confirmed
            report['export']=export_confirmed(data,args.tracker)
        print(json.dumps({k:v for k,v in report.items() if k not in ('items','pending_groups','discovery')},ensure_ascii=False,indent=2));return
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
