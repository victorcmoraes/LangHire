import json
from types import SimpleNamespace
from urllib.error import HTTPError
import pytest

from core.application_safety import AttemptStore,SafetyError,job_key
from core.external_apply import canonical_ats,execute_external
from core.external_boards import add_boards,suggest_boards,board_candidates
from core.external_catalog import refresh_board,refresh_imported
from core.external_discovery import board_spec,discover
from core.external_qualification import qualify
from core.external_service import queue,run_external


URL='https://apply.workable.com/fixture/j/ABC0123456'


def posting(**extra):
    return {'shortcode':'ABC0123456','title':'Backend Engineer','url':'https://apply.workable.com/j/ABC0123456',
            'application_url':'https://apply.workable.com/j/ABC0123456/apply','published_on':'2026-11-03',
            'city':'Recife','state':'Pernambuco','country':'Brazil','telecommuting':True,'workplace_type':'remote',
            'description':'<h2>Requirements:</h2><p>3 years of experience</p>',**extra}


def public_api(monkeypatch,rows):
    calls=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'name':'Fixture Company','jobs':rows}).encode()
    def fetch(req,**kwargs):
        assert req.full_url=='https://www.workable.com/api/accounts/fixture?details=true'
        assert req.get_method()=='GET' and kwargs=={'timeout':40}
        assert req.get_header('Authorization') is None and req.data is None
        calls.append(req.full_url)
        return Response()
    monkeypatch.setattr('urllib.request.urlopen',fetch)
    return calls


def seed(data):
    (data/'external').mkdir()
    (data/'jobs.json').write_text('{}')
    (data/'confirmed_facts.json').write_text(json.dumps({'facts':[{'id':'experience.years','value':3}]}))


@pytest.mark.parametrize('mode,normalized,state',[('remote','remote','eligible'),('hybrid','hybrid','discarded'),('on_site','on-site','discarded'),(None,'remote','eligible'),('unknown','','pending')])
def test_public_api_normalizes_published_jobs_and_uses_workplace_type(monkeypatch,mode,normalized,state):
    public_api(monkeypatch,[posting(workplace_type=mode),posting(shortcode='DRAFT00001',published_on=None)])
    rows=refresh_board('workable','fixture')
    assert len(rows)==1
    job=rows[0]
    assert (job['platform'],job['source'],job['board'],job['company'],job['externalId'])==('workable','workable','fixture','Fixture Company','ABC0123456')
    assert job['url']==URL and job['applyUrl']==URL+'/apply'
    assert job['title']=='Backend Engineer' and job['description']=='Requirements:\n\n3 years of experience'
    assert job['location']=='Recife, Pernambuco, Brazil'+(' ('+normalized+')' if normalized else '')
    assert job['workplaceType']==normalized
    assert qualify(job,{'experience.years':{'value':3}})['state']==state


def test_multiple_locations_and_unknown_workplace_are_preserved(monkeypatch):
    public_api(monkeypatch,[posting(workplace_type=None,telecommuting=False,locations=[
        {'city':'Example City','region':None,'country':'Brazil'},
        {'country':'Portugal'},{'country':'Portugal'}])])
    job=refresh_board('workable','fixture')[0]
    assert job['location']=='Example City, Brazil; Portugal' and not job.get('country')
    assert job['workplaceType']==''


@pytest.mark.parametrize('value',['workable:fixture','https://apply.workable.com/fixture',URL+'?source=fixture',{'platform':'workable','board':'fixture'}])
def test_board_spec_accepts_workable_seeds_and_postings(value):
    assert board_spec(value)=={'platform':'workable','board':'fixture'}


@pytest.mark.parametrize('value',['http://apply.workable.com/fixture','https://apply.workable.com.evil.invalid/fixture','https://apply.workable.com:444/fixture','https://user@apply.workable.com/fixture','workable:../fixture'])
def test_board_spec_rejects_unofficial_workable_seeds(value):
    with pytest.raises(SafetyError):board_spec(value)


def test_workable_identity_protects_hosted_and_application_url_variants(tmp_path):
    store=AttemptStore(tmp_path);aid=store.begin(URL+'?source=fixture')
    store.transition(aid,'uncertain','Fixture outcome')
    for url in [URL+'/',URL+'/apply?source=other',URL.replace('ABC','abc')]:
        assert job_key(url)==job_key(URL)
        with pytest.raises(SafetyError):store.begin(url)
    assert canonical_ats(URL+'/apply?source=fixture')==URL
    assert job_key(URL)!=job_key(URL.replace('fixture','other'))
    assert job_key(URL)=='workable:fixture:ABC0123456'
    assert job_key(URL)!=job_key(URL.replace('ABC0123456','DEF0123456'))


def test_stored_workable_links_are_suggested(tmp_path,monkeypatch):
    seed(tmp_path);calls=public_api(monkeypatch,[posting()])
    (tmp_path/'jobs.json').write_text(json.dumps({'fixture':{'source':'linkedin','url':'https://www.linkedin.com/jobs/view/123/',
                                                          'description':URL}}))
    candidates,invalid=board_candidates(tmp_path)
    assert not invalid and candidates[0]['platform']=='workable' and candidates[0]['board']=='fixture'
    assert suggest_boards(tmp_path)['validated_boards']==1 and len(calls)==1


@pytest.mark.parametrize('status',[401,403,404,429,500])
def test_refresh_propagates_http_errors_without_retry(monkeypatch,status):
    calls=[]
    def blocked(req,**kwargs):
        calls.append(req.full_url)
        raise HTTPError(req.full_url,status,'Fixture error',{},None)
    monkeypatch.setattr('urllib.request.urlopen',blocked)
    with pytest.raises(HTTPError) as caught:refresh_board('workable','fixture')
    assert caught.value.code==status and len(calls)==1


@pytest.mark.parametrize('value',['workable:fixture',URL])
def test_suggest_and_add_validate_workable_seeds_through_cached_api(tmp_path,monkeypatch,value):
    seed(tmp_path);calls=public_api(monkeypatch,[posting(),posting(shortcode='DRAFT00001',published_on=None)])
    (tmp_path/'external/board-seeds.txt').write_text(value)
    report=suggest_boards(tmp_path)
    assert report['validated_boards']==1 and report['new_eligible_jobs']==1
    assert report['boards'][0]['public_jobs']==1 and not report['registry_changed']
    assert not (tmp_path/'external/board-registry.json').exists()
    assert add_boards(tmp_path,[value])['added']==[{'platform':'workable','board':'fixture'}]
    assert len(calls)==1 and json.loads((tmp_path/'jobs.json').read_text())=={}


@pytest.mark.parametrize('status',[401,403,429])
def test_workable_blocked_api_requires_human_review_and_caches_failure(tmp_path,monkeypatch,status):
    seed(tmp_path);calls=[]
    (tmp_path/'external/board-seeds.txt').write_text('workable:fixture')
    def blocked(req,**kwargs):
        calls.append(req.full_url)
        raise HTTPError(req.full_url,status,'Fixture block',{},None)
    monkeypatch.setattr('urllib.request.urlopen',blocked)
    for _ in range(2):
        report=suggest_boards(tmp_path)
        assert report['boards'][0]['reason']=='human_review_required'
    with pytest.raises(SafetyError):add_boards(tmp_path,['workable:fixture'])
    assert len(calls)==1 and not (tmp_path/'external/board-registry.json').exists()


async def test_discovered_workable_jobs_reach_the_assist_queue_but_never_the_automatic_run(tmp_path,monkeypatch):
    seed(tmp_path);public_api(monkeypatch,[posting()])
    report=discover(tmp_path,['workable:fixture'])
    assert report['new_jobs']==1 and report['new_qualification']=={'eligible':1}
    jobs=json.loads((tmp_path/'jobs.json').read_text())
    assert jobs[URL]['collection_origin']=='public_workable_board'
    # The shared queue feeds the human-submitted Apply Assist chain; the automatic runner still refuses Workable.
    assert [j['url'] for j in queue(tmp_path)]==[URL]
    assert discover(tmp_path)['new_jobs']==0
    refreshed=refresh_imported(tmp_path)
    assert len(refreshed['jobs'])==1 and refreshed['boards'][0]['platform']=='workable'
    # The runner also rejects unsupported platforms if a caller supplies a queue.
    monkeypatch.setattr('core.external_service.queue',lambda _:list(jobs.values()))
    async def forbidden(*args,**kwargs):pytest.fail('Workable must not reach execution')
    monkeypatch.setattr('core.external_service.execute_external',forbidden)
    status={};body=SimpleNamespace(platform=None,job_url=URL,job_urls=None,limit=10,execution='submit')
    await run_external(tmp_path,body,status)
    result=json.loads(open(status['report_path'],encoding='utf-8').read())
    assert result['considered_jobs']==0 and result['submit_intents']==0 and not result['attempts']
    assert not status.get('job_errors') and AttemptStore(tmp_path).list()==[]


def test_triage_includes_collected_workable_jobs(tmp_path,monkeypatch):
    from core.external_qualification import triage
    seed(tmp_path);public_api(monkeypatch,[posting()])
    discover(tmp_path,['workable:fixture'])
    result=triage(tmp_path)
    assert [(d['platform'],d['state']) for d in result['decisions']]==[('workable','eligible')]
    assert [j['platform'] for j in queue(tmp_path)]==['workable']


@pytest.mark.parametrize('execution',['review','submit'])
async def test_execute_external_refuses_workable_before_browser_or_attempt(tmp_path,monkeypatch,execution):
    def forbidden(*args,**kwargs):pytest.fail('Workable must not open a browser')
    monkeypatch.setattr('core.external_apply.application_browser',forbidden)
    with pytest.raises(SafetyError,match='Unsupported hosted ATS'):
        await execute_external({'platform':'workable','url':URL},tmp_path,'',execution)
    assert not (tmp_path/'application_attempts.sqlite').exists()
