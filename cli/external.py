"""Sequential external ATS queue. Local data, reusable answers and durable receipts."""
import argparse
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import sys
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
    run.add_argument('--limit',type=int,default=10);run.add_argument('--platform',choices=('lever','greenhouse'))
    run.add_argument('--job-url')
    args=parser.parse_args();data=get_data_dir();store=AttemptStore(data)
    if args.command=='stop':
        (data/'external/stop.json').write_text('{"stop":true}',encoding='utf-8');print('Stop requested; progress and uncertain sends are preserved.');return
    if args.command=='collect':
        refreshed=refresh_imported(data);qualified=triage(data)
        print(json.dumps({'active':len(refreshed['jobs']),'boards':refreshed['boards'],'triage':qualified['states']},ensure_ascii=False,indent=2));return
    if args.command=='triage':
        result=triage(data);print(json.dumps({k:v for k,v in result.items() if k!='decisions'},ensure_ascii=False,indent=2));return
    if args.command=='status':
        result={'daily_remaining':remaining_budget(store),'attempt_states':dict(Counter(a['state'] for a in store.list()))}
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
    if args.limit<1:parser.error('--limit must be positive')
    triage(data)
    body=SimpleNamespace(execution=args.execution,limit=args.limit,platform=args.platform,job_url=args.job_url,job_urls=None)
    status={};asyncio.run(run_external(data,body,status));print(json.dumps(status,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
