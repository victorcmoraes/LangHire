import json
from io import BytesIO
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


URL='https://fixturebank.csod.com/ux/ats/careersite/1/home/requisition/123?c=fixturebank'
HOME='https://fixturebank.csod.com/ux/ats/careersite/1/home?c=fixturebank'
CLOUD='https://eu.api.csod.com/'


def posting(**extra):
    return {'requisitionId':123,'displayJobTitle':'Backend Engineer',
            'externalDescription':'<h2>Requirements:</h2><p>3 years of experience</p><p>Modalidade: remoto</p>',
            'locations':[{'city':'OSASCO','state':'SP'},{'city':'RECIFE','state':'PE'}],
            'postingEffectiveDate':'01/10/2026','postingExpirationDate':'30/11/2026',**extra}


def public_api(monkeypatch,rows,context=None,pages=None,site=1):
    calls=[];page_number=0
    def fetch(req,**kwargs):
        nonlocal page_number
        assert kwargs=={'timeout':40}
        assert req.get_header('User-agent')=='LangHire-local-job-catalog/1.0'
        calls.append(req)
        if req.get_method()=='GET':
            page_number=0
            assert req.full_url==HOME.replace('/1/','/'+str(site)+'/')
            return BytesIO((context if context is not None else '<script>var context={"token":"anonymous.fixture.jwt","cloud":"https:\\/\\/eu.api.csod.com\\/"}</script>').encode())
        assert req.full_url==CLOUD+'rec-job-search/external/jobs'
        assert req.get_header('Authorization')=='Bearer anonymous.fixture.jwt'
        body=json.loads(req.data);page_number+=1
        assert body=={'careerSiteId':site,'careerSitePageId':site,'pageNumber':page_number,'pageSize':100,'cultureId':12,
                      'searchText':'','cultureName':'pt-BR','states':[],'countryCodes':[],'cities':[],'placeID':'',
                      'radius':None,'postingsWithinDays':None,'customFieldCheckboxKeys':[],'customFieldDropdowns':[],'customFieldRadios':[]}
        page=pages[body['pageNumber']-1] if pages else rows
        return BytesIO(json.dumps({'data':{'totalCount':len(rows),'requisitions':page}}).encode())
    monkeypatch.setattr('urllib.request.urlopen',fetch)
    return calls


def seed(data):
    (data/'external').mkdir()
    (data/'jobs.json').write_text('{}')
    (data/'confirmed_facts.json').write_text(json.dumps({'facts':[{'id':'experience.years','value':3}]}))


@pytest.mark.parametrize('site',[1,7])
def test_public_context_paging_and_normalization(monkeypatch,site):
    rows=[posting(requisitionId=i) for i in range(1,102)]
    calls=public_api(monkeypatch,rows,pages=[rows[:100],rows[100:]],site=site)
    board='fixturebank'+('/'+str(site) if site!=1 else '')
    jobs=refresh_board('cornerstone',board)
    assert len(jobs)==101 and len(calls)==3
    job=jobs[0]
    assert (job['platform'],job['source'],job['board'],job['company'],job['externalId'])==('cornerstone','cornerstone',board,'fixturebank','1')
    assert job['title']=='Backend Engineer' and job['location']=='Osasco, SP; Recife, PE'
    assert job['description']=='Requirements:\n\n3 years of experience\n\nModalidade: remoto'
    assert job['url']==URL.replace('/1/home','/'+str(site)+'/home').replace('/123?','/1?')
    assert job['postingEffectiveDate']=='01/10/2026' and job['postingExpirationDate']=='30/11/2026'
    assert qualify(job,{'experience.years':{'value':3}})['state']=='eligible'


@pytest.mark.parametrize('context',[
    '<script>{"token":"anonymous.fixture.jwt","cloud":"https://eu.api.csod.com/"}</script>',
    '<div data-context="{&quot;token&quot;:&quot;anonymous.fixture.jwt&quot;,&quot;cloud&quot;:&quot;https://eu.api.csod.com/&quot;}"></div>'])
def test_public_context_json_and_html_entities(monkeypatch,context):
    calls=public_api(monkeypatch,[],context=context)
    assert refresh_board('cornerstone','fixturebank')==[] and len(calls)==2


@pytest.mark.parametrize('context',['{}','{"token":"fixture"}',
    '{"token":"fixture","cloud":"https://evil.invalid/"}',
    '{"token":"fixture","cloud":"https://eu.api.csod.com.evil.invalid/"}',
    '{"token":"fixture","cloud":"https://user@eu.api.csod.com/"}'])
def test_invalid_public_context_stops_before_search(monkeypatch,context):
    calls=public_api(monkeypatch,[],context=context)
    with pytest.raises(ValueError):refresh_board('cornerstone','fixturebank')
    assert len(calls)==1


def test_incomplete_paging_is_not_a_complete_board(monkeypatch):
    public_api(monkeypatch,[posting(),posting(requisitionId=124)],pages=[[posting()],[]])
    with pytest.raises(ValueError,match='Incomplete'):refresh_board('cornerstone','fixturebank')


@pytest.mark.parametrize('value',['cornerstone:fixturebank/7','https://fixturebank.csod.com/ux/ats/careersite/7/home',{'platform':'cornerstone','board':'fixturebank/7'}])
def test_explicit_site_board_spec(value):
    assert board_spec(value)=={'platform':'cornerstone','board':'fixturebank/7'}
    assert board_spec('cornerstone:fixturebank/1')==board_spec('cornerstone:fixturebank')


def test_identity_keeps_site_and_requisition_and_checks_tenant():
    assert job_key(URL.split('?')[0])==job_key(URL+'&source=fixture')
    assert job_key(URL)!=job_key(URL.replace('/1/home','/7/home'))
    assert job_key(URL)!=job_key(URL.replace('/123?','/124?'))
    assert canonical_ats(URL.split('?')[0])==URL
    for parse in (job_key,canonical_ats):
        with pytest.raises(SafetyError):parse(URL.replace('c=fixturebank','c=other'))


def test_stored_links_are_suggested_and_explicit_sites_share_cache(tmp_path,monkeypatch):
    seed(tmp_path);calls=public_api(monkeypatch,[posting()],site=7)
    url=URL.replace('/1/home','/7/home')
    (tmp_path/'jobs.json').write_text(json.dumps({'fixture':{'source':'linkedin','url':'https://www.linkedin.com/jobs/view/123/','description':url}}))
    candidates,invalid=board_candidates(tmp_path)
    assert not invalid and candidates[0]['board']=='fixturebank/7'
    assert suggest_boards(tmp_path)['validated_boards']==1
    assert add_boards(tmp_path,['cornerstone:fixturebank/7'])['added']==[{'platform':'cornerstone','board':'fixturebank/7'}]
    assert len(calls)==2


@pytest.mark.parametrize('value',['cornerstone:fixturebank','https://fixturebank.csod.com/ux/ats/careersite/1/home?c=fixturebank',URL+'&source=fixture',{'platform':'cornerstone','board':'fixturebank'}])
def test_board_spec_accepts_cornerstone_seeds_and_postings(value):
    assert board_spec(value)=={'platform':'cornerstone','board':'fixturebank'}


@pytest.mark.parametrize('value',['http://fixturebank.csod.com/ux/ats/careersite/1/home','https://fixturebank.csod.com.evil.invalid/ux/ats/careersite/1/home','https://fixturebank.csod.com:444/ux/ats/careersite/1/home','https://user@fixturebank.csod.com/ux/ats/careersite/1/home','cornerstone:../fixturebank','cornerstone:fixturebank/0','cornerstone:fixturebank/01','cornerstone:fixturebank/1/2','cornerstone:-fixturebank','cornerstone:fixture_bank',URL.replace('c=fixturebank','c=other'),URL+'&c=fixturebank'])

def test_board_spec_rejects_unofficial_cornerstone_seeds(value):
    with pytest.raises(SafetyError):board_spec(value)


def test_cornerstone_identity_protects_requisition_url_variants(tmp_path):
    store=AttemptStore(tmp_path);aid=store.begin(URL+'&source=fixture')
    store.transition(aid,'uncertain','Fixture outcome')
    for url in [URL.replace('?c=fixturebank','/?c=fixturebank'),URL.replace('?c=fixturebank','/?c=fixturebank&source=other'),URL.replace('fixturebank.csod','FIXTUREBANK.csod')]:
        assert job_key(url)==job_key(URL)
        with pytest.raises(SafetyError):store.begin(url)
    assert canonical_ats(URL+'&source=fixture')==URL
    assert job_key(URL)!=job_key(URL.replace('fixturebank','other'))


@pytest.mark.parametrize('value',['cornerstone:fixturebank',URL])
def test_suggest_and_add_validate_cornerstone_seeds_through_cached_api(tmp_path,monkeypatch,value):
    seed(tmp_path);calls=public_api(monkeypatch,[posting()])
    (tmp_path/'external/board-seeds.txt').write_text(value)
    report=suggest_boards(tmp_path)
    assert report['validated_boards']==1 and report['new_eligible_jobs']==1
    assert report['boards'][0]['public_jobs']==1 and not report['registry_changed']
    assert not (tmp_path/'external/board-registry.json').exists()
    assert add_boards(tmp_path,[value])['added']==[{'platform':'cornerstone','board':'fixturebank'}]
    assert len(calls)==2 and json.loads((tmp_path/'jobs.json').read_text())=={}


@pytest.mark.parametrize('status',[401,403,429])
@pytest.mark.parametrize('stage',['page','search'])
def test_cornerstone_blocked_api_requires_human_review_and_caches_failure(tmp_path,monkeypatch,status,stage):
    seed(tmp_path);calls=[]
    (tmp_path/'external/board-seeds.txt').write_text('cornerstone:fixturebank')
    def blocked(req,**kwargs):
        calls.append(req.full_url)
        if stage=='search' and req.get_method()=='GET':return BytesIO(json.dumps({'token':'fixture','cloud':CLOUD}).encode())
        raise HTTPError(req.full_url,status,'Fixture block',{},None)
    monkeypatch.setattr('urllib.request.urlopen',blocked)
    for _ in range(2):
        report=suggest_boards(tmp_path)
        assert report['boards'][0]['reason']=='human_review_required'
    with pytest.raises(SafetyError):add_boards(tmp_path,['cornerstone:fixturebank'])
    assert len(calls)==(1 if stage=='page' else 2) and not (tmp_path/'external/board-registry.json').exists()


async def test_discovered_cornerstone_jobs_are_qualified_but_skipped_by_queue_and_run(tmp_path,monkeypatch):
    seed(tmp_path);public_api(monkeypatch,[posting()])
    report=discover(tmp_path,['cornerstone:fixturebank'])
    assert report['new_jobs']==1 and report['new_qualification']=={'eligible':1}
    jobs=json.loads((tmp_path/'jobs.json').read_text())
    assert jobs[URL]['collection_origin']=='public_cornerstone_board'
    assert queue(tmp_path)==[]
    assert discover(tmp_path)['new_jobs']==0
    refreshed=refresh_imported(tmp_path)
    assert len(refreshed['jobs'])==1 and refreshed['boards'][0]['platform']=='cornerstone'
    # The runner also rejects unsupported platforms if a caller supplies a queue.
    monkeypatch.setattr('core.external_service.queue',lambda _:list(jobs.values()))
    async def forbidden(*args,**kwargs):pytest.fail('Cornerstone must not reach execution')
    monkeypatch.setattr('core.external_service.execute_external',forbidden)
    status={};body=SimpleNamespace(platform=None,job_url=URL,job_urls=None,limit=10,execution='submit')
    await run_external(tmp_path,body,status)
    result=json.loads(open(status['report_path'],encoding='utf-8').read())
    assert result['considered_jobs']==0 and result['submit_intents']==0 and not result['attempts']
    assert not status.get('job_errors') and AttemptStore(tmp_path).list()==[]


def test_triage_includes_collected_cornerstone_jobs(tmp_path,monkeypatch):
    from core.external_qualification import triage
    seed(tmp_path);public_api(monkeypatch,[posting()])
    discover(tmp_path,['cornerstone:fixturebank'])
    result=triage(tmp_path)
    assert [(d['platform'],d['state']) for d in result['decisions']]==[('cornerstone','eligible')]
    assert queue(tmp_path)==[]


@pytest.mark.parametrize('execution',['review','submit'])
async def test_execute_external_refuses_cornerstone_before_browser_or_attempt(tmp_path,monkeypatch,execution):
    def forbidden(*args,**kwargs):pytest.fail('Cornerstone must not open a browser')
    monkeypatch.setattr('core.external_apply.application_browser',forbidden)
    with pytest.raises(SafetyError,match='Unsupported hosted ATS'):
        await execute_external({'platform':'cornerstone','url':URL},tmp_path,'',execution)
    assert not (tmp_path/'application_attempts.sqlite').exists()
