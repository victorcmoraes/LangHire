"""Per-job resumes from the master file: review drafts, preview queued jobs, build one job, switch tailoring on or off."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
os.environ.setdefault('LANGHIRE_DATA_DIR',str(ROOT/'.local'))
from backend.core.config import get_data_dir
from backend.core.application_safety import SafetyError,job_key
from backend.core.resume_tailoring import build,drafts,load_master,master_path,tailoring_enabled,write_sheet


def find_job(data,url):
    jobs=json.loads((data/'jobs.json').read_text(encoding='utf-8'))
    match=next((dict(j,url=u) for u,j in jobs.items() if job_key(u)==job_key(url)),None)
    if match is None:raise SafetyError('Job not in jobs.json: '+url)
    return match


def summary(pdf):
    meta=json.loads((pdf.parent/'selection.json').read_text(encoding='utf-8'))
    return {k:meta[k] for k in ('company','lang','headline','pages','drafts','bullets')}|{'pdf':str(pdf),'interview':str(pdf.parent/'interview.md')}


def set_enabled(data,value):
    path=data/'settings.json';settings=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    settings['resume_tailoring']=dict(settings.get('resume_tailoring') or {},enabled=value)
    path.write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'enabled':tailoring_enabled(data),'master':str(master_path(data))}


async def preview(data,limit):
    from backend.core.external_service import queue
    master=load_master(data);result=[]
    for job in queue(data)[:limit]:
        pdf=await build(data,job,master);result.append(dict(summary(pdf),title=job.get('title')))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('review',help='Bullets still marked draft in the master')
    sub.add_parser('status');sub.add_parser('enable');sub.add_parser('disable')
    p=sub.add_parser('preview',help='Build resumes for the next queued jobs without sending anything');p.add_argument('--limit',type=int,default=5)
    p=sub.add_parser('build',help='Build the resume and interview sheet for one job');p.add_argument('url')
    p=sub.add_parser('sheet',help='Refresh the interview sheet for one job');p.add_argument('url')
    args=parser.parse_args();data=get_data_dir()
    try:
        if args.command=='review':
            master=load_master(data)
            result=[{'where':where,'id':b['id'],'pt':b['pt'],'note':'mude status para confirmed ou off em '+str(master_path(data))} for where,b in drafts(master)]
        elif args.command=='status':result={'enabled':tailoring_enabled(data),'master':str(master_path(data)),'drafts':len(drafts(load_master(data)))}
        elif args.command in ('enable','disable'):
            if args.command=='enable':load_master(data)
            result=set_enabled(data,args.command=='enable')
        elif args.command=='preview':result=asyncio.run(preview(data,args.limit))
        elif args.command=='build':result=summary(asyncio.run(build(data,find_job(data,args.url))))
        else:result={'interview':str(write_sheet(data,find_job(data,args.url)))}
    except SafetyError as error:parser.exit(1,str(error)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
