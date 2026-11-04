"""Suggest public ATS boards from local links and explicit seeds before registration."""
from concurrent.futures import ThreadPoolExecutor,as_completed
import json
from pathlib import Path
import re
import shutil
import time
import uuid
from filelock import FileLock

from .application_safety import SafetyError,job_key,now
from .easy_answers import AnswerBook
from .external_catalog import refresh_board
from .external_discovery import board_spec,candidate_location
from .external_qualification import home_cities,qualify
from .job_tracks import load_job_tracks,matches_track


LINK=re.compile(r'https://(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.csod\.com/ux/ats/careersite/[1-9][0-9]{0,9}(?=[/\s?\"<>]|$)(?:/[^\s\"<>]*)?(?:\?[^\s\"<>]*)?|(?:boards\.greenhouse\.io|job-boards\.greenhouse\.io|jobs\.lever\.co|jobs\.ashbyhq\.com|apply\.workable\.com)/[A-Za-z0-9][A-Za-z0-9_-]{0,99}(?=[/\s?\"<>]|$))',re.I)
CACHE_SECONDS=86400


def registry(data):
    path=Path(data)/'external/board-registry.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'version':1,'boards':[]}


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)


def board_candidates(data,seeds=None):
    data=Path(data);current=registry(data)
    # Excluded boards count as known so suggestions never bring them back.
    known={(b['platform'],b['board'].lower()) for b in current['boards']+current.get('excluded',[])}
    candidates={};invalid=[]
    def include(value,source):
        try:spec=board_spec(value)
        except (SafetyError,ValueError):invalid.append({'source':source,'reason':'invalid_official_board'});return
        key=(spec['platform'],spec['board'].lower())
        if key in known:return
        if key not in candidates:candidates[key]=dict(spec,sources=[])
        if source not in candidates[key]['sources']:candidates[key]['sources'].append(source)
    path=data/'jobs.json'
    jobs=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    for row in jobs.values():
        if row.get('source') not in ('linkedin','jobgether') and row.get('board')!='jobgether':continue
        # Scan stored fields only; never follow aggregator links or redirects.
        for url in LINK.findall(json.dumps(row,ensure_ascii=False)):
            include(url,'collected_'+('jobgether' if row.get('board')=='jobgether' else row['source']))
    path=Path(seeds) if seeds is not None else data/'external/board-seeds.txt'
    if seeds is not None and not path.exists():raise SafetyError('Board seeds file does not exist')
    if path.exists():
        for number,line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(),1):
            line=line.strip()
            if line and not line.startswith('#'):include(line,'seed_line_'+str(number))
    return list(candidates.values()),invalid


def validate_board(data,board):
    data=Path(data);spec=board_spec(board);root=data/'external/board-cache';root.mkdir(parents=True,exist_ok=True)
    path=root/(spec['platform']+'-'+spec['board'].lower().replace('/','_')+'.json')
    # Share cached successes and failures across suggestion/add runs and processes.
    with FileLock(str(path.with_suffix('.lock')),timeout=0):
        cached=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        if cached and 0<=time.time()-cached['cached_at']<CACHE_SECONDS:return cached
        try:result={'jobs':refresh_board(spec['platform'],spec['board'])}
        except Exception as error:
            code=getattr(error,'code',None)
            result={'error':type(error).__name__,'http_status':code,
                    'reason':'human_review_required' if code in (401,403,429) or isinstance(error,json.JSONDecodeError) else 'public_board_unavailable'}
        result.update(cached_at=time.time(),checked_at=now());write_json(path,result)
        return result


def evaluate_board(board,result,tracks,facts,known):
    item=dict(board,checked_at=result['checked_at'])
    if 'error' in result:return dict(item,**{k:v for k,v in result.items() if k not in ('cached_at','checked_at')},validated=False)
    matches=[j for j in result['jobs'] if matches_track(j['title'],tracks) and candidate_location(j,home_cities(facts))]
    fresh=[j for j in matches if job_key(j['url']) not in known]
    eligible=[j for j in fresh if qualify(j,facts,tracks)['state']=='eligible']
    return dict(item,validated=bool(matches),public_jobs=len(result['jobs']),matching_jobs=len(matches),new_matching_jobs=len(fresh),
                new_eligible_jobs=len(eligible),eligible_examples=[{'title':j['title'],'url':j['url']} for j in eligible[:3]])


def suggest_boards(data,seeds=None,limit=25,workers=3):
    if type(limit) is not int or not 1<=limit<=100:raise SafetyError('Board suggestion limit must be 1 to 100')
    data=Path(data);tracks=load_job_tracks(data);book=AnswerBook(data)
    candidates,invalid=board_candidates(data,seeds)
    path=data/'jobs.json';jobs=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    known={job_key(j['url']) for j in jobs.values()};selected=candidates[:limit];fetched={}
    with ThreadPoolExecutor(max_workers=min(3,max(1,workers))) as pool:
        futures={pool.submit(validate_board,data,b):(b['platform'],b['board']) for b in selected}
        for future in as_completed(futures):fetched[futures[future]]=future.result()
    rows=[evaluate_board(b,fetched[(b['platform'],b['board'])],tracks,book.facts,known) for b in selected]
    report={'checked_at':now(),'candidate_boards':len(candidates),'boards_checked':len(rows),'deferred_boards':max(0,len(candidates)-len(rows)),
            'validated_boards':sum(b['validated'] for b in rows),'new_eligible_jobs':sum(b.get('new_eligible_jobs',0) for b in rows),
            'boards':rows,'invalid_seeds':invalid,'registry_changed':False}
    path=data/'reports'/('board-suggestions-'+uuid.uuid4().hex+'.json');report['report_path']=str(path);write_json(path,report)
    return report


def add_boards(data,boards):
    """Explicit CLI selection is the approval; suggestion alone never registers a board."""
    if not boards:raise SafetyError('Select approved boards explicitly with --board')
    data=Path(data);root=data/'external';root.mkdir(parents=True,exist_ok=True)
    tracks=load_job_tracks(data);book=AnswerBook(data)
    with FileLock(str(root/'catalog.lock'),timeout=0):
        saved=registry(data);known={(b['platform'],b['board'].lower()) for b in saved['boards']};added=[]
        for raw in boards:
            spec=board_spec(raw);key=(spec['platform'],spec['board'].lower())
            if key in known:continue
            result=evaluate_board(spec,validate_board(data,spec),tracks,book.facts,set())
            if not result['validated']:raise SafetyError('Board has no validated matching jobs: '+spec['platform']+':'+spec['board'])
            added.append(spec);known.add(key)
        if added:
            path=root/'board-registry.json'
            if path.exists():
                backup=data/'backups'/('board-registry-'+uuid.uuid4().hex);backup.mkdir(parents=True)
                shutil.copy2(path,backup/path.name)
            saved['boards'].extend(added);write_json(path,saved)
    return {'added':added,'registered_boards':len(saved['boards']),'jobs_collected':0}
