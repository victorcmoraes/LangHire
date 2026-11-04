import json
from urllib.error import HTTPError
import pytest

from core.external_catalog import check_missing_jobgether,identity,refresh_imported
from core.external_discovery import discover
from core.external_qualification import triage


def job(identifier,board='jobgether'):
    return {'platform':'lever','source':'lever','board':board,'company':board,'externalId':str(identifier),
            'title':'Backend Engineer','location':'Brazil (Remote)','description':'Requirements:\n3 years with Java',
            'url':f'https://jobs.lever.co/{board}/{identifier}'}


def seed(data,rows):
    (data/'external').mkdir()
    (data/'jobs.json').write_text(json.dumps({j['url']:j for j in rows}))
    (data/'external/catalog.json').write_text(json.dumps({'jobs':rows,'boards':[{'platform':'lever','board':rows[0]['board']}]}))
    (data/'confirmed_facts.json').write_text(json.dumps({'facts':[{'id':'experience.years','value':3}]}))


def api(monkeypatch,statuses):
    calls=[]
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
    def fetch(req,**kwargs):
        identifier=req.full_url.rsplit('/',1)[1];calls.append(identifier)
        assert req.full_url=='https://api.lever.co/v0/postings/jobgether/'+identifier
        assert req.get_method()=='GET' and kwargs['timeout']==10
        status=statuses[identifier]
        if isinstance(status,Exception):raise status
        if status!=200:raise HTTPError(req.full_url,status,'Fixture response',{},None)
        return Response()
    monkeypatch.setattr('urllib.request.urlopen',fetch)
    return calls


@pytest.mark.parametrize('refresh',[False,True],ids=['discovery','imported'])
def test_only_individual_404_closes_missing_jobs_and_survives_next_refresh(tmp_path,monkeypatch,refresh):
    rows=[job(i) for i in range(1,4)];seed(tmp_path,rows)
    calls=api(monkeypatch,{'1':404,'2':200})
    monkeypatch.setattr('core.external_discovery.scoped_board',lambda *args:([rows[2]],{}))
    monkeypatch.setattr('core.external_catalog.refresh_board',lambda *args:[rows[2]])
    def collect():return refresh_imported(tmp_path) if refresh else discover(tmp_path)
    collect();result=triage(tmp_path)
    decisions={d['externalId']:d for d in result['decisions']}
    assert calls==['1','2']
    assert (decisions['1']['state'],decisions['1']['reason'])==('closed','posting_not_found')
    assert decisions['2']['state']==decisions['3']['state']=='eligible'
    catalog=json.loads((tmp_path/'external/catalog.json').read_text())
    assert {j['externalId'] for j in catalog['jobs']}=={'2','3'}
    assert 'closed_imported' not in catalog['boards'][0]
    collect()
    assert calls==['1','2','2'] and triage(tmp_path)['states']=={'closed':1,'eligible':2}
    # A posting that reappears in the listing becomes active again.
    monkeypatch.setattr('core.external_discovery.scoped_board',lambda *args:(rows,{}))
    monkeypatch.setattr('core.external_catalog.refresh_board',lambda *args:rows)
    collect()
    assert calls==['1','2','2'] and triage(tmp_path)['states']=={'eligible':3}


@pytest.mark.parametrize('status',[429,500,503,TimeoutError('Fixture timeout')])
def test_transient_failure_stops_round_without_closing_jobs(tmp_path,monkeypatch,status):
    rows=[job(i) for i in range(1,4)];seed(tmp_path,rows)
    calls=api(monkeypatch,{'1':status})
    monkeypatch.setattr('core.external_discovery.scoped_board',lambda *args:([],{}))
    result=discover(tmp_path)
    assert calls==['1'] and result['catalog_jobs']==3
    assert result['triage']=={'eligible':3}
    assert result['boards'][0]['posting_not_found']==[]
    assert result['boards'][0]['posting_check_error']


def test_successful_checks_remain_valid_before_later_failure(monkeypatch):
    rows=[job(i) for i in range(1,4)];calls=api(monkeypatch,{'1':404,'2':429})
    result=check_missing_jobgether({identity(j):j for j in rows},[])
    assert calls==['1','2'] and result['posting_not_found']==['1']


def test_checks_are_bounded_and_skip_present_closed_and_other_boards(monkeypatch):
    rows=[job(f'{i:03}') for i in range(160)]+[job('other','fixture')]
    calls=api(monkeypatch,{j['externalId']:200 for j in rows})
    result=check_missing_jobgether({identity(j):j for j in rows},[rows[0]],['001'])
    assert len(calls)==result['posting_checks']==150
    assert calls[0]=='002' and calls[-1]=='151'
    assert result['posting_not_found']==['001']


def test_complete_boards_still_close_by_absence_without_posting_checks(tmp_path,monkeypatch):
    seed(tmp_path,[job('1','fixture')]);calls=api(monkeypatch,{})
    monkeypatch.setattr('core.external_discovery.scoped_board',lambda *args:([],{}))
    result=discover(tmp_path)
    assert calls==[] and result['triage']=={'closed':1}
    assert triage(tmp_path)['decisions'][0]['reason']=='not_in_current_complete_public_board'


def test_failed_collection_never_checks_postings(tmp_path,monkeypatch):
    seed(tmp_path,[job('1')]);calls=api(monkeypatch,{})
    def fail(*args):raise TimeoutError('Fixture collection timeout')
    monkeypatch.setattr('core.external_discovery.scoped_board',fail)
    result=discover(tmp_path)
    assert calls==[] and result['catalog_jobs']==1 and result['boards'][0]['error']=='TimeoutError'
