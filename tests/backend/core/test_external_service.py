from types import SimpleNamespace
import json
import pytest
from core.application_safety import AttemptStore
from core.external_service import run_external,remaining_budget
from core.easy_answers import AnswerBook


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
