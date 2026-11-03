import json
from types import SimpleNamespace
from core.easy_service import run_jobs
from core.application_safety import AttemptStore


async def test_execution_report_retains_attempt_evidence_and_answer_metrics(tmp_path,monkeypatch):
    async def execute(data,body,status):
        store=AttemptStore(data);aid=store.begin('https://www.linkedin.com/jobs/view/123/')
        store.event(aid,'answer_metrics',{'rule':3,'cache':2,'pending':0,'model':0})
        store.transition(aid,'reviewed','No submit')
    monkeypatch.setattr('core.easy_service._run_jobs',execute)
    status={};await run_jobs(tmp_path,SimpleNamespace(execution='review'),status)
    report=json.loads(next((tmp_path/'reports').glob('*.json')).read_text())
    assert report['results']['reviewed']==1
    assert report['submit_intents']==0
    assert report['answers']['rule']==3 and report['answers']['cache']==2
    assert report['attempts'][0]['evidence_dir']


async def test_transient_pre_submit_failures_retry_twice_without_consuming_budget(tmp_path,monkeypatch):
    url='https://www.linkedin.com/jobs/view/123/';calls=[];delays=[]
    async def fail(job,data,*args):
        store=AttemptStore(data);aid=store.begin(url);calls.append(aid)
        store.event(aid,'easy_error',{'type':'TimeoutError','message':'Navigation timeout'})
        store.transition(aid,'pending','Navigation timeout')
        return store.get(aid)
    async def sleep(seconds):delays.append(seconds)
    monkeypatch.setattr('core.shared_config.read_jobs',lambda:{url:{'url':url,'easy_apply':True,'status':'pending','qualification':{'state':'eligible'}}})
    monkeypatch.setattr('core.shared_config.update_job',lambda *a,**k:None)
    monkeypatch.setattr('core.easy_service.qualification_current',lambda *a:True)
    monkeypatch.setattr('core.easy_service.execute_easy',fail)
    monkeypatch.setattr('core.easy_service.asyncio.sleep',sleep)
    await run_jobs(tmp_path,SimpleNamespace(job_url=url,job_urls=None,limit=1,execution='review',review_id=None),{})
    assert len(calls)==3 and delays==[2,4]
    assert all(not AttemptStore(tmp_path).submissions(aid) for aid in calls)
