"""Discover new engineering jobs from an explicit registry of public ATS boards."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import Counter
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit
import uuid
import urllib.request
from filelock import FileLock

from .cornerstone import board_parts,url_parts
from .application_safety import SafetyError,now,job_key
from .external_catalog import refresh_board,identity,lever_job,check_missing_jobgether
from .external_qualification import home_cities,qualify,triage
from .job_tracks import load_job_tracks,matches_track
from .easy_answers import AnswerBook


def board_spec(value):
    """Accept platform:slug or an official hosted job-board URL, never arbitrary URLs."""
    if isinstance(value,dict):platform,board=value.get('platform'),value.get('board')
    elif '://' in value:
        p=urlsplit(value)
        if p.scheme!='https' or p.port not in (None,443) or p.username or p.password:
            raise SafetyError('Use an official HTTPS job-board URL')
        platform={'jobs.lever.co':'lever','job-boards.greenhouse.io':'greenhouse','boards.greenhouse.io':'greenhouse','jobs.ashbyhq.com':'ashby','apply.workable.com':'workable'}.get(p.hostname)
        board=p.path.strip('/').split('/')[0]
        try:cornerstone=url_parts(value)
        except ValueError as error:raise SafetyError('Invalid public ATS board') from error
        if cornerstone:
            tenant,site=cornerstone;platform='cornerstone';board=tenant+('/'+str(site) if site!=1 else '')
    else:
        parts=value.split(':',1)
        if len(parts)!=2:raise SafetyError('Board format: greenhouse:company, lever:company, ashby:company, workable:company or cornerstone:tenant/siteId')
        platform,board=parts
    if platform=='cornerstone':
        try:tenant,site=board_parts(board)
        except ValueError as error:raise SafetyError('Invalid public ATS board') from error
        return {'platform':platform,'board':tenant+('/'+str(site) if site!=1 else '')}
    if platform not in ('lever','greenhouse','ashby','workable') or not isinstance(board,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}',board):
        raise SafetyError('Invalid public ATS board')
    return {'platform':platform,'board':board}


def load_boards(data,extra=()):
    data=Path(data);rows=[];excluded=set()
    path=data/'external/board-registry.json'
    if path.exists():
        saved=json.loads(path.read_text(encoding='utf-8'));rows.extend(saved['boards'])
        excluded={(b['platform'],b['board'].lower()) for b in saved.get('excluded',[])}
    else:
        path=data/'external/catalog.json'
        if path.exists():rows.extend(json.loads(path.read_text(encoding='utf-8')).get('boards',[]))
    rows.extend(extra)
    unique={}
    for row in rows:
        item=board_spec(row);unique.setdefault((item['platform'],item['board'].lower()),item)
    return [b for key,b in unique.items() if key not in excluded]


def candidate_location(job,cities=()):
    """Discovery may include ambiguous regional jobs; qualification must still prove eligibility."""
    places=[r'\bBrazil\b',r'\bBrasil\b',r'\bLATAM\b','Latin America',*(r'\b'+re.escape(c)+r'\b' for c in cities)]
    if job.get('platform')=='cornerstone' and re.search(r', (?:AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)(?:;|$)',job.get('location','')):return True
    return job.get('country')=='BR' or bool(re.search('|'.join(places),job.get('location',''),re.I))


def scoped_board(platform,board,aggregator_pages):
    if (platform,board)!=('lever','jobgether'):
        return refresh_board(platform,board),{}
    jobs={};checked=0;more=False
    for page in range(aggregator_pages):
        url=f'https://api.lever.co/v0/postings/jobgether?mode=json&location=Brazil&limit=100&skip={page*100}'
        req=urllib.request.Request(url,headers={'User-Agent':'LangHire-local-job-catalog/1.0','Accept':'application/json'})
        with urllib.request.urlopen(req,timeout=40) as response:rows=json.load(response)
        checked+=1;fresh=[item for item in rows if item['id'] not in jobs]
        for item in fresh:jobs[item['id']]=lever_job(item,'jobgether')
        more=len(rows)>=100
        if not more or not fresh:break
    return list(jobs.values()),{'scope':'Brazil only, paginated; never a complete global board','pages_checked':checked,'more_pages_possible':more}


def discover(data,boards=(),max_new=5000,workers=3,aggregator_pages=5):
    if max_new<1:raise SafetyError('max_new must be positive')
    if type(aggregator_pages) is not int or not 1<=aggregator_pages<=20:raise SafetyError('Aggregator page count must be 1 to 20')
    data=Path(data);root=data/'external';root.mkdir(parents=True,exist_ok=True)
    from .shared_config import add_collected_job
    with FileLock(str(root/'catalog.lock'),timeout=0):
        selected=load_boards(data,boards);book=AnswerBook(data);tracks=load_job_tracks(data)
        path=root/'catalog.json'
        catalog=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'jobs':[],'boards':[]}
        previous={identity(j):j for j in catalog['jobs']};known_urls={job_key(j['url']) for j in catalog['jobs']}
        backup=data/'backups'/('discovery-'+uuid.uuid4().hex);backup.mkdir(parents=True)
        for name in ('jobs.json','external/catalog.json','external/triage.json','external/board-registry.json'):
            source=data/name
            if source.exists():shutil.copy2(source,backup/Path(name).name)
        # All results are merged in registry order, regardless of HTTP completion order.
        fetched={};errors={}
        with ThreadPoolExecutor(max_workers=min(3,max(1,workers))) as pool:
            futures={pool.submit(scoped_board,b['platform'],b['board'],aggregator_pages):(b['platform'],b['board']) for b in selected}
            for future in as_completed(futures):
                key=futures[future]
                try:fetched[key]=future.result()
                except Exception as error:errors[key]={'error':type(error).__name__,'http_status':getattr(error,'code',None)}
        reports=[];new=[];new_identities=0;truncated=False
        metadata={(b['platform'],b['board']):b for b in catalog['boards']}
        for board in selected:
            key=(board['platform'],board['board'])
            if key in errors:
                reports.append(dict(board,**errors[key]));continue
            rows,scope=fetched[key];active={identity(j) for j in rows};retained=0;added=0
            for job in rows:
                ident=identity(job);exists=ident in previous
                relevant=matches_track(job['title'],tracks) and candidate_location(job,home_cities(book.facts))
                if not exists and (not relevant or job_key(job['url']) in known_urls):continue
                if not exists and new_identities>=max_new:truncated=True;continue
                if not exists:
                    job=dict(job,collection_origin='public_'+job['platform']+'_board')
                    # Preserve the existing source label used for deterministic GH attribution.
                    if job['platform']=='greenhouse':job['collection_origin']='bounded_public_greenhouse_board'
                    if add_collected_job(dict(job,status='new'),data):
                        decision=qualify(job,book.facts,tracks)
                        new.append({'url':job['url'],'title':job['title'],'state':decision['state'],'reason':decision['reason']})
                        added+=1
                    new_identities+=1
                previous[ident]=job;known_urls.add(job_key(job['url']));retained+=1
            info=dict(board,active_public=len(rows),active_imported=retained,added=added,checked_at=now())
            if key==('lever','jobgether'):
                info.update(scope)
                info.update(check_missing_jobgether(previous,rows,metadata.get(key,{}).get('posting_not_found',[])))
                previous={ident:j for ident,j in previous.items() if ident[:2]!=key or ident[2] not in info['posting_not_found']}
            else:info['closed_imported']=sum(ident[:2]==key and ident not in active for ident in previous)
            metadata[key]=info;reports.append(info)
            # Closure is valid only for a complete successful company board response.
            if key!=('lever','jobgether'):
                previous={ident:j for ident,j in previous.items() if ident[:2]!=key or ident in active}
        catalog.update(jobs=list(previous.values()),boards=list(metadata.values()))
        temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(catalog,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)
        registry=root/'board-registry.json';temporary=registry.with_suffix('.tmp')
        temporary.write_text(json.dumps({'version':1,'boards':selected},indent=2),encoding='utf-8');temporary.replace(registry)
        qualification=triage(data)
        report={'checked_at':now(),'boards_checked':len(selected),'boards':reports,'new_jobs':len(new),
                'new_qualification':dict(Counter(j['state'] for j in new)),'new':new,
                'new_limit_reached':truncated,'triage':qualification['states'],'catalog_jobs':len(catalog['jobs'])}
        report_path=data/'reports'/('discovery-'+uuid.uuid4().hex+'.json');report_path.parent.mkdir(exist_ok=True)
        report['report_path']=str(report_path);report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return report
