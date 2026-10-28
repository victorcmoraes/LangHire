import json
import threading
import time
from urllib.error import HTTPError
import pytest

from core.application_safety import SafetyError
from core.external_boards import add_boards,board_candidates,suggest_boards


def job(board='fixture',identifier=1,**extra):
    return {'platform':'greenhouse','source':'greenhouse','board':board,'externalId':str(identifier),'title':'Backend Engineer',
            'url':f'https://job-boards.greenhouse.io/{board}/jobs/{identifier}','location':'Brazil (Remote)',
            'description':'Requirements:\n3 years of experience',**extra}


def seed(data):
    (data/'external').mkdir()
    (data/'external/board-registry.json').write_text(json.dumps({'version':1,'boards':[{'platform':'greenhouse','board':'registered','note':'preserve'}]}))
    (data/'jobs.json').write_text(json.dumps({
        'linkedin':dict(job(),source='linkedin',url='https://www.linkedin.com/jobs/view/123/',description='Apply: https://boards.greenhouse.io/fresh/jobs/1'),
        'jobgether':dict(job(),source='jobgether',board='jobgether',url='https://jobgether.com/offer/123',description='https://jobs.lever.co/second/123'),
        'other':dict(job(),description='https://jobs.lever.co/ignored/123'),
    }))
    (data/'confirmed_facts.json').write_text(json.dumps({'facts':[{'id':'experience.years','value':3}]}))


def test_candidates_only_use_existing_links_and_official_explicit_seeds(tmp_path):
    seed(tmp_path)
    (tmp_path/'external/board-seeds.txt').write_text('# Boards\ngreenhouse:fresh\nhttps://jobs.lever.co/second/job\ngreenhouse:registered\nhttps://evil.invalid/rogue\ngreenhouse:../bad\n')
    candidates,invalid=board_candidates(tmp_path)
    assert {(b['platform'],b['board']) for b in candidates}=={('greenhouse','fresh'),('lever','second')}
    assert len(invalid)==2 and len(candidates[0]['sources'])==2
    with pytest.raises(SafetyError):board_candidates(tmp_path,tmp_path/'missing.txt')


def test_suggest_counts_new_eligibility_caches_calls_and_never_changes_registry_or_catalog(tmp_path,monkeypatch):
    seed(tmp_path);calls=[]
    registry=(tmp_path/'external/board-registry.json').read_bytes();jobs=(tmp_path/'jobs.json').read_bytes()
    def fetch(platform,board):
        calls.append((platform,board))
        if board=='second':return [job(board,4,title='Account Executive')]
        return [job(board,2),job(board,3,location='United States'),job(board,5,description='Requirements:\n8 years of experience')]
    monkeypatch.setattr('core.external_boards.refresh_board',fetch)
    report=suggest_boards(tmp_path)
    assert report['validated_boards']==1 and report['new_eligible_jobs']==1
    assert report['boards'][0]['matching_jobs']==2
    assert not report['registry_changed']
    assert (tmp_path/'external/board-registry.json').read_bytes()==registry and (tmp_path/'jobs.json').read_bytes()==jobs
    assert not (tmp_path/'external/catalog.json').exists()
    suggest_boards(tmp_path)
    assert len(calls)==2
    settings={'job_tracks':{'include':'account executive'}}
    (tmp_path/'settings.json').write_text(json.dumps(settings))
    report=suggest_boards(tmp_path)
    assert report['boards'][1]['validated'] and report['new_eligible_jobs']==1 and len(calls)==2


@pytest.mark.parametrize('status',[403,429,404])
def test_http_failures_are_cached_without_retry_or_registration(tmp_path,monkeypatch,status):
    seed(tmp_path);calls=[]
    def fetch(*args):calls.append(args);raise HTTPError('https://example.invalid',status,'fixture',{},None)
    monkeypatch.setattr('core.external_boards.refresh_board',fetch)
    report=suggest_boards(tmp_path);suggest_boards(tmp_path)
    assert report['validated_boards']==0 and len(calls)==2
    assert all(b['reason']==('human_review_required' if status in (403,429) else 'public_board_unavailable') for b in report['boards'])
    with pytest.raises(SafetyError):add_boards(tmp_path,['greenhouse:fresh'])
    assert len(json.loads((tmp_path/'external/board-registry.json').read_text())['boards'])==1


def test_add_requires_explicit_validated_selection_and_is_atomic_on_invalid_selection(tmp_path,monkeypatch):
    seed(tmp_path)
    monkeypatch.setattr('core.external_boards.refresh_board',lambda platform,board:[job(board,2)] if board=='fresh' else [])
    with pytest.raises(SafetyError):add_boards(tmp_path,[])
    with pytest.raises(SafetyError):add_boards(tmp_path,['greenhouse:fresh','lever:second'])
    assert len(json.loads((tmp_path/'external/board-registry.json').read_text())['boards'])==1
    assert add_boards(tmp_path,['greenhouse:fresh'])['registered_boards']==2
    assert add_boards(tmp_path,['greenhouse:fresh'])['added']==[]
    assert json.loads((tmp_path/'external/board-registry.json').read_text())['boards'][0]['note']=='preserve'
    assert len(list((tmp_path/'backups').glob('board-registry-*')))==1


def test_workers_and_candidate_limit_bound_requests_and_cache_expires(tmp_path,monkeypatch):
    seed(tmp_path);(tmp_path/'jobs.json').write_text('{}')
    (tmp_path/'external/board-seeds.txt').write_text('\n'.join('greenhouse:board'+str(i) for i in range(8)))
    lock=threading.Lock();active=0;peak=0;calls=[]
    def fetch(platform,board):
        nonlocal active,peak
        with lock:active+=1;peak=max(peak,active);calls.append(board)
        time.sleep(.01)
        with lock:active-=1
        return [job(board)]
    monkeypatch.setattr('core.external_boards.refresh_board',fetch)
    report=suggest_boards(tmp_path,limit=4,workers=20)
    assert len(calls)==4 and peak<=3 and report['deferred_boards']==4
    path=tmp_path/'external/board-cache/greenhouse-board0.json'
    cached=json.loads(path.read_text());cached['cached_at']=0;path.write_text(json.dumps(cached))
    suggest_boards(tmp_path,limit=4)
    assert len(calls)==5


def test_missing_sources_produces_empty_report_without_network(tmp_path,monkeypatch):
    def unexpected(*args):pytest.fail('No candidates must make no requests')
    monkeypatch.setattr('core.external_boards.refresh_board',unexpected)
    assert suggest_boards(tmp_path)['candidate_boards']==0
