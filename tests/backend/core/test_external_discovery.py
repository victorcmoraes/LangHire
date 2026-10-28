import json
import pytest
from core.application_safety import AttemptStore,SafetyError
from core.external_discovery import discover,board_spec


def job(board,identifier):
    return {'platform':'greenhouse','source':'greenhouse','board':board,'company':board,'externalId':str(identifier),
            'title':'Backend Engineer '+str(identifier),'location':'Brazil (Remote)','description':'Requirements:\n3 years with Java',
            'url':f'https://job-boards.greenhouse.io/{board}/jobs/{identifier}'}


def seed(path,jobs):
    (path/'external').mkdir()
    (path/'jobs.json').write_text(json.dumps({j['url']:dict(j,status='applied',manual_note='keep') for j in jobs}))
    (path/'external/catalog.json').write_text(json.dumps({'jobs':jobs,'boards':[{'platform':'greenhouse','board':'fixture'}]}))
    (path/'confirmed_facts.json').write_text(json.dumps({'facts':[{'id':'experience.years','value':3}]}))


def test_discovery_adds_new_jobs_without_overwriting_confirmed_or_manual_records(tmp_path,monkeypatch):
    one=job('fixture',1);two=job('fixture',2);seed(tmp_path,[one])
    s=AttemptStore(tmp_path);aid=s.begin(one['url']);s.transition(aid,'confirmed','fixture receipt')
    monkeypatch.setattr('core.external_discovery.refresh_board',lambda *args:[dict(one,description='Requirements:\nJava'),two])
    r=discover(tmp_path)
    assert r['new_jobs']==1 and r['new_qualification']=={'eligible':1}
    rows=json.loads((tmp_path/'jobs.json').read_text())
    assert rows[one['url']]['status']=='applied' and rows[one['url']]['manual_note']=='keep'
    assert rows[two['url']]['collection_origin']=='bounded_public_greenhouse_board'
    assert s.get(aid)['state']=='confirmed'
    assert discover(tmp_path)['new_jobs']==0


def test_new_limit_is_deterministic_and_invalid_urls_cannot_change_the_request_host(tmp_path,monkeypatch):
    seed(tmp_path,[])
    monkeypatch.setattr('core.external_discovery.refresh_board',lambda platform,board:[job(board,1),job(board,2)])
    r=discover(tmp_path,['greenhouse:extra'],max_new=1)
    assert r['new_jobs']==1 and r['new_limit_reached']
    assert r['new'][0]['url']==job('fixture',1)['url']
    for raw in ['https://evil.invalid/foo','greenhouse:../foo','https://jobs.lever.co:444/foo','https://name:password@jobs.lever.co/foo']:
        with pytest.raises(SafetyError):board_spec(raw)
    assert board_spec('https://jobs.lever.co/Flex/id?tracking=1')=={'platform':'lever','board':'Flex'}


def test_failed_or_partial_board_never_closes_missing_jobs(tmp_path,monkeypatch):
    old=job('fixture',1);seed(tmp_path,[old])
    def fail(*args):raise OSError('offline')
    monkeypatch.setattr('core.external_discovery.refresh_board',fail)
    assert discover(tmp_path)['boards'][0]['error']=='OSError'
    assert json.loads((tmp_path/'external/catalog.json').read_text())['jobs'][0]['url']==old['url']
    partial=dict(old,platform='lever',source='lever',board='jobgether',company='jobgether')
    (tmp_path/'jobs.json').write_text(json.dumps({partial['url']:partial}))
    (tmp_path/'external/catalog.json').write_text(json.dumps({'jobs':[partial],'boards':[{'platform':'lever','board':'jobgether'}]}))
    (tmp_path/'external/board-registry.json').unlink()
    monkeypatch.setattr('core.external_discovery.refresh_board',lambda *args:[])
    monkeypatch.setattr('core.external_discovery.scoped_board',lambda *args:([],{}))
    result=discover(tmp_path)
    assert result['catalog_jobs']==1 and 'closed_imported' not in result['boards'][0]


def test_only_a_complete_successful_board_can_mark_imported_job_closed(tmp_path,monkeypatch):
    seed(tmp_path,[job('fixture',1)])
    monkeypatch.setattr('core.external_discovery.refresh_board',lambda *args:[])
    assert discover(tmp_path)['triage']=={'closed':1}


def test_aggregator_pagination_is_bounded_brazil_only_and_deduplicated(monkeypatch):
    from core.external_discovery import scoped_board
    from urllib.parse import urlsplit,parse_qs
    calls=[]
    class Response:
        def __init__(self,rows):self.rows=rows
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self):return json.dumps(self.rows).encode()
    def fetch(req,**kw):
        p=urlsplit(req.full_url);q=parse_qs(p.query);calls.append(int(q['skip'][0]))
        assert p.hostname=='api.lever.co' and q['location']==['Brazil'] and q['limit']==['100']
        rows=[{'id':str(i),'text':'Backend Engineer','hostedUrl':'https://jobs.lever.co/jobgether/'+str(i),'applyUrl':'apply'} for i in range(100)]
        return Response(rows)  # Repeated pages must not inflate the catalog.
    monkeypatch.setattr('urllib.request.urlopen',fetch)
    rows,scope=scoped_board('lever','jobgether',5)
    assert len(rows)==100 and calls==[0,100] and scope['pages_checked']==2


def test_discovery_uses_same_configured_tracks_as_triage(tmp_path,monkeypatch):
    seed(tmp_path,[])
    row=dict(job('fixture',42),title='Platform Engineer')
    monkeypatch.setattr('core.external_discovery.refresh_board',lambda *args:[row])
    assert discover(tmp_path)['new_jobs']==0
    (tmp_path/'settings.json').write_text(json.dumps({'job_tracks':{'include':'platform engineer'}}))
    report=discover(tmp_path)
    assert report['new_jobs']==1 and report['new_qualification']=={'eligible':1}
    assert report['triage']=={'eligible':1}
