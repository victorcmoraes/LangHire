from types import SimpleNamespace
import json
import pytest
from core.application_safety import AttemptStore
from core.external_service import run_external,remaining_budget
from core.easy_answers import AnswerBook


def test_ready_preflight_prioritizes_a_fillable_job_without_bypassing_live_checks(tmp_path):
    from core.external_service import queue
    from core.application_safety import now
    (tmp_path/'external/preflight').mkdir(parents=True)
    jobs=[{'platform':'greenhouse','source':'greenhouse','board':'fixture','externalId':str(i),'title':title,
           'url':f'https://job-boards.greenhouse.io/fixture/jobs/{i}'} for i,title in [(1,'Backend Engineer'),(2,'Full-stack Engineer')]]
    (tmp_path/'external/catalog.json').write_text(json.dumps({'jobs':jobs}))
    (tmp_path/'external/triage.json').write_text(json.dumps({'decisions':[dict(j,state='eligible') for j in jobs]}))
    (tmp_path/'jobs.json').write_text(json.dumps({j['url']:j for j in jobs}))
    book=AnswerBook(tmp_path);path=tmp_path/'external/preflight/fixture-2.json'
    ready={'url':jobs[1]['url'],'checked_at':now(),'ready_for_browser':True,'facts_version':book.version}
    path.write_text(json.dumps(ready))
    assert [j['externalId'] for j in queue(tmp_path)]==['2','1']
    ready['facts_version']='stale';path.write_text(json.dumps(ready))
    assert [j['externalId'] for j in queue(tmp_path)]==['1','2']


def test_receipt_reconciliation_updates_only_its_own_completed_status(tmp_path):
    from core.external_service import update_reconciled_status
    (tmp_path/'external').mkdir();path=tmp_path/'external/run-state.json'
    result={'id':'own','state':'confirmed'}
    for state in [{'attempt_id':'other','running':False},{'attempt_id':'own','running':True}]:
        path.write_text(json.dumps(state));assert not update_reconciled_status(tmp_path,result,'audit.json')
        assert json.loads(path.read_text())==state
    path.write_text(json.dumps({'attempt_id':'own','running':False,'result':'uncertain'}))
    assert update_reconciled_status(tmp_path,result,'audit.json')
    assert json.loads(path.read_text())['result']=='confirmed'


async def test_paused_channel_and_unresolved_preflight_do_not_launch_browser_or_consume_budget(tmp_path,monkeypatch):
    (tmp_path/'external').mkdir();(tmp_path/'reports').mkdir()
    book=AnswerBook(tmp_path)
    jobs=[{'platform':p,'url':url,'external_qualification':{'fact_refs':[],'facts_version':book.reference_version([])}}
          for p,url in [('lever','https://jobs.lever.co/fixture/00000000-0000-0000-0000-000000000001'),
                        ('greenhouse','https://job-boards.greenhouse.io/fixture/jobs/123')]]
    monkeypatch.setattr('core.external_service.queue',lambda _:jobs)
    monkeypatch.setattr('core.external_service.platform_hold',lambda data,platform:platform=='lever')
    async def missing(data,job):return {'url':job['url'],'ready_for_browser':False,'pending':[{'reason':'unknown_answer'}],'answer_metrics':{'rule':1,'cache':0,'pending':1,'model':0}}
    monkeypatch.setattr('core.greenhouse_apply.preflight_greenhouse',missing)
    async def forbidden(*args,**kwargs):raise AssertionError('Browser must not open')
    monkeypatch.setattr('core.external_service.execute_external',forbidden)
    body=SimpleNamespace(platform=None,job_url=None,job_urls=None,limit=10,execution='submit');status={}
    await run_external(tmp_path,body,status)
    report=json.loads(open(status['report_path'],encoding='utf-8').read())
    assert report['considered_jobs']==2 and report['submit_intents']==0 and not report['attempts']
    assert report['paused_channels']=={'lever':'verification_diagnosis'}
    assert report['preflight_answers']['pending']==1
    assert remaining_budget(AttemptStore(tmp_path))==10


async def test_failed_profile_lease_never_attributes_another_workers_attempt_to_our_report(tmp_path,monkeypatch):
    (tmp_path/'external').mkdir();(tmp_path/'reports').mkdir()
    store=AttemptStore(tmp_path);other=store.begin('https://jobs.lever.co/other/job')
    monkeypatch.setattr('core.external_service.queue',lambda _:[])
    class Busy:
        def __init__(self,*a,**kw):pass
        def __enter__(self):raise RuntimeError('Profile busy')
        def __exit__(self,*a):pass
    monkeypatch.setattr('core.external_service.FileLock',Busy)
    status={};body=SimpleNamespace(platform=None,job_url=None,job_urls=None,limit=10,execution='review')
    with pytest.raises(RuntimeError,match='Profile busy'):await run_external(tmp_path,body,status)
    report=json.loads(open(status['report_path'],encoding='utf-8').read())
    assert not report['attempts'] and store.get(other)['state']=='running'
