import json
from types import SimpleNamespace
from urllib.error import HTTPError
import pytest

from core.application_safety import AttemptStore,SafetyError,job_key
from core.external_apply import canonical_ats,execute_external
from core.external_boards import add_boards,suggest_boards
from core.external_catalog import refresh_board,refresh_imported
from core.external_discovery import board_spec,discover
from core.external_qualification import qualify
from core.external_service import queue,run_external


URL='https://jobs.ashbyhq.com/fixture/00000000-0000-0000-0000-000000000abc'


def posting(**extra):
    return {'id':URL.rsplit('/',1)[1],'title':'Backend Engineer','jobUrl':URL,'applyUrl':URL+'/application',
            'location':'Brazil','secondaryLocations':[{'location':'Portugal','address':{'country':'Portugal'}}],
            'workplaceType':'Remote','isRemote':True,'isListed':True,
            'descriptionHtml':'<h2>Requirements:</h2><p>3 years of experience</p>',**extra}


def public_api(monkeypatch,rows):
    calls=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'apiVersion':'1','jobs':rows}).encode()
    def fetch(req,**kwargs):
        assert req.full_url=='https://api.ashbyhq.com/posting-api/job-board/fixture'
        assert req.get_method()=='GET'
        calls.append(req.full_url)
        return Response()
    monkeypatch.setattr('urllib.request.urlopen',fetch)
    return calls


def seed(data):
    (data/'external').mkdir()
    (data/'jobs.json').write_text('{}')
    (data/'confirmed_facts.json').write_text(json.dumps({'facts':[{'id':'experience.years','value':3}]}))


@pytest.mark.parametrize('mode,normalized,state',[('Remote','remote','eligible'),('Hybrid','hybrid','discarded'),('OnSite','on-site','discarded'),(None,'','pending')])
def test_public_api_normalizes_listed_jobs_and_uses_workplace_type(monkeypatch,mode,normalized,state):
    public_api(monkeypatch,[posting(workplaceType=mode),posting(id='unlisted',isListed=False)])
    rows=refresh_board('ashby','fixture')
    assert len(rows)==1
    job=rows[0]
    assert (job['platform'],job['source'],job['board'],job['company'],job['externalId'])==('ashby','ashby','fixture','fixture',URL.rsplit('/',1)[1])
    assert job['url']==URL and job['applyUrl']==URL+'/application'
    assert job['title']=='Backend Engineer' and job['description']=='Requirements:\n\n3 years of experience'
    assert job['location']=='Brazil; Portugal'+(' ('+normalized+')' if normalized else '')
    assert job['secondaryLocations']==posting()['secondaryLocations'] and job['workplaceType']==normalized
    assert qualify(job,{'experience.years':{'value':3}})['state']==state


def test_plain_description_and_unknown_country_are_preserved(monkeypatch):
    public_api(monkeypatch,[posting(location='Example City',secondaryLocations=[],descriptionPlain='Requirements:\n3 years of experience')])
    job=refresh_board('ashby','fixture')[0]
    assert job['location']=='Example City (remote)' and not job.get('country')
    assert job['description']=='Requirements:\n3 years of experience'
    assert qualify(job,{'experience.years':{'value':3}})['reason']=='geographic_eligibility_needs_evidence'


@pytest.mark.parametrize('value',['ashby:fixture','https://jobs.ashbyhq.com/fixture',URL+'?source=fixture',{'platform':'ashby','board':'fixture'}])
def test_board_spec_accepts_ashby_seeds_and_postings(value):
    assert board_spec(value)=={'platform':'ashby','board':'fixture'}


@pytest.mark.parametrize('value',['http://jobs.ashbyhq.com/fixture','https://jobs.ashbyhq.com.evil.invalid/fixture','https://jobs.ashbyhq.com:444/fixture','https://user@jobs.ashbyhq.com/fixture','ashby:../fixture'])
def test_board_spec_rejects_unofficial_ashby_seeds(value):
    with pytest.raises(SafetyError):board_spec(value)


def test_ashby_identity_protects_hosted_and_application_url_variants(tmp_path):
    store=AttemptStore(tmp_path);aid=store.begin(URL+'?source=fixture')
    store.transition(aid,'uncertain','Fixture outcome')
    for url in [URL+'/',URL+'/application?source=other',URL.replace('abc','ABC')]:
        assert job_key(url)==job_key(URL)
        with pytest.raises(SafetyError):store.begin(url)
    assert canonical_ats(URL+'/application?source=fixture')==URL
    assert job_key(URL)!=job_key(URL.replace('fixture','other'))


@pytest.mark.parametrize('value',['ashby:fixture',URL])
def test_suggest_and_add_validate_ashby_seeds_through_cached_api(tmp_path,monkeypatch,value):
    seed(tmp_path);calls=public_api(monkeypatch,[posting(),posting(id='unlisted',isListed=False)])
    (tmp_path/'external/board-seeds.txt').write_text(value)
    report=suggest_boards(tmp_path)
    assert report['validated_boards']==1 and report['new_eligible_jobs']==1
    assert report['boards'][0]['public_jobs']==1 and not report['registry_changed']
    assert not (tmp_path/'external/board-registry.json').exists()
    assert add_boards(tmp_path,[value])['added']==[{'platform':'ashby','board':'fixture'}]
    assert len(calls)==1 and json.loads((tmp_path/'jobs.json').read_text())=={}


@pytest.mark.parametrize('status',[401,403,429])
def test_ashby_blocked_api_requires_human_review_and_caches_failure(tmp_path,monkeypatch,status):
    seed(tmp_path);calls=[]
    (tmp_path/'external/board-seeds.txt').write_text('ashby:fixture')
    def blocked(req,**kwargs):
        calls.append(req.full_url)
        raise HTTPError(req.full_url,status,'Fixture block',{},None)
    monkeypatch.setattr('urllib.request.urlopen',blocked)
    for _ in range(2):
        report=suggest_boards(tmp_path)
        assert report['boards'][0]['reason']=='human_review_required'
    with pytest.raises(SafetyError):add_boards(tmp_path,['ashby:fixture'])
    assert len(calls)==1 and not (tmp_path/'external/board-registry.json').exists()


async def test_discovered_ashby_jobs_reach_the_assist_queue_but_never_the_automatic_run(tmp_path,monkeypatch):
    seed(tmp_path);public_api(monkeypatch,[posting()])
    report=discover(tmp_path,['ashby:fixture'])
    assert report['new_jobs']==1 and report['new_qualification']=={'eligible':1}
    jobs=json.loads((tmp_path/'jobs.json').read_text())
    assert jobs[URL]['collection_origin']=='public_ashby_board'
    # The shared queue feeds the human-submitted Apply Assist chain; the automatic runner still refuses Ashby.
    assert [j['url'] for j in queue(tmp_path)]==[URL]
    assert discover(tmp_path)['new_jobs']==0
    refreshed=refresh_imported(tmp_path)
    assert len(refreshed['jobs'])==1 and refreshed['boards'][0]['platform']=='ashby'
    # The runner also rejects unsupported platforms if a caller supplies a queue.
    monkeypatch.setattr('core.external_service.queue',lambda _:list(jobs.values()))
    async def forbidden(*args,**kwargs):pytest.fail('Ashby must not reach execution')
    monkeypatch.setattr('core.external_service.execute_external',forbidden)
    status={};body=SimpleNamespace(platform=None,job_url=URL,job_urls=None,limit=10,execution='submit')
    await run_external(tmp_path,body,status)
    result=json.loads(open(status['report_path'],encoding='utf-8').read())
    assert result['considered_jobs']==0 and result['submit_intents']==0 and not result['attempts']
    assert not status.get('job_errors') and AttemptStore(tmp_path).list()==[]


def test_triage_includes_collected_ashby_jobs(tmp_path,monkeypatch):
    from core.external_qualification import triage
    seed(tmp_path);public_api(monkeypatch,[posting()])
    discover(tmp_path,['ashby:fixture'])
    result=triage(tmp_path)
    assert [(d['platform'],d['state']) for d in result['decisions']]==[('ashby','eligible')]
    assert [j['platform'] for j in queue(tmp_path)]==['ashby']


@pytest.mark.parametrize('execution',['review','submit'])
async def test_execute_external_refuses_ashby_before_browser_or_attempt(tmp_path,monkeypatch,execution):
    def forbidden(*args,**kwargs):pytest.fail('Ashby must not open a browser')
    monkeypatch.setattr('core.external_apply.application_browser',forbidden)
    with pytest.raises(SafetyError,match='Unsupported hosted ATS'):
        await execute_external({'platform':'ashby','url':URL},tmp_path,'',execution)
    assert not (tmp_path/'application_attempts.sqlite').exists()
