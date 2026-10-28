import json
from contextlib import asynccontextmanager
import pytest
from filelock import FileLock,Timeout
from core.application_safety import AttemptStore
from core.easy_answers import AnswerBook
from core.external_batch import BatchStore,run_batch


def seed(path,count=2):
    (path/'external').mkdir()
    (path/'settings.json').write_text(json.dumps({'submission_policy':{'daily_limit':None}}))
    (path/'confirmed_facts.json').write_text(json.dumps({'facts':[{'id':'experience.years','value':3}]}))
    jobs=[{'platform':'greenhouse','source':'greenhouse','board':'fixture','company':'Fixture','externalId':str(i),
           'title':'Backend Engineer '+str(i),'location':'Brazil (Remote)','description':'Requirements:\n3 years with Java',
           'url':f'https://job-boards.greenhouse.io/fixture/jobs/{i}'} for i in range(count)]
    (path/'jobs.json').write_text(json.dumps({j['url']:j for j in jobs}))
    (path/'external/catalog.json').write_text(json.dumps({'jobs':jobs,'boards':[{'platform':'greenhouse','board':'fixture'}]}))
    return jobs


@pytest.fixture
def runtime(monkeypatch):
    observations={'opens':0,'calls':[],'session':object()}
    @asynccontextmanager
    async def browser(data):
        observations['opens']+=1;yield observations['session']
    async def ready(data,job):
        return {'ready_for_browser':True,'pending':[],'answer_metrics':{'rule':1,'cache':0,'pending':0,'model':0}}
    async def execute(job,data,resume,execution,status,lease_held=False,session=None):
        assert session is observations['session'] and lease_held
        observations['calls'].append(job['url']);store=AttemptStore(data);aid=store.begin(job['url'])
        store.event(aid,'external_batch',{'batch_id':job['_batch_id'],'attempt_id':aid})
        store.intent(aid,aid,job['url']);store.response(aid,200);store.transition(aid,'confirmed','Fixture receipt')
        return store.get(aid)
    monkeypatch.setattr('core.external_batch.persistent_browser',browser)
    monkeypatch.setattr('core.greenhouse_apply.preflight_greenhouse',ready)
    monkeypatch.setattr('core.external_batch.execute_external',execute)
    return observations


async def test_batch_processes_100_groups_questions_and_resumes_without_duplicate_sends(tmp_path,monkeypatch,runtime):
    seed(tmp_path,100);store=BatchStore(tmp_path);bid=store.create(100,'submit')
    async def question(data,job):
        book=AnswerBook(data);answer=book.resolve({'label':'Preferred display name?','type':'text','options':[]},dict(job,form_language='en'))
        return {'ready_for_browser':'value' in answer,'pending':[] if 'value' in answer else [{'question':'Preferred display name?','answer_id':answer['pending']}],
                'answer_metrics':{'rule':0,'cache':int('value' in answer),'pending':int('pending' in answer),'model':0}}
    monkeypatch.setattr('core.greenhouse_apply.preflight_greenhouse',question)
    initial=await run_batch(tmp_path,bid)
    assert initial['job_outcomes']=={'pending':100} and runtime['opens']==0
    assert len(initial['pending_groups'])==1 and len(initial['pending_groups'][0]['job_urls'])==100
    AnswerBook(tmp_path).approve(initial['pending_groups'][0]['answer_ids'][0],'Candidate')
    assert not store.pending(bid)
    finished=await run_batch(tmp_path,bid)
    assert finished['new_confirmations']==100 and finished['native_send_intents']==100
    assert len(runtime['calls'])==100 and runtime['opens']==1
    resumed=await run_batch(tmp_path,bid)
    assert resumed['new_confirmations']==0 and resumed['native_send_intents']==0
    assert resumed['batch_confirmations']==100 and len(runtime['calls'])==100
    assert len({initial['report_path'],finished['report_path'],resumed['report_path']})==3
    assert json.loads(open(initial['report_path']).read())['new_confirmations']==0


async def test_supply_shortfall_is_not_padded_with_duplicates_or_paused_jobs(tmp_path,runtime):
    jobs=seed(tmp_path,2)
    rows=json.loads((tmp_path/'jobs.json').read_text());lever=dict(jobs[0],platform='lever',source='lever',board='other',url='https://jobs.lever.co/other/00000000-0000-0000-0000-000000000001')
    rows[lever['url']]=lever;(tmp_path/'jobs.json').write_text(json.dumps(rows))
    catalog=json.loads((tmp_path/'external/catalog.json').read_text());catalog['jobs'].append(lever);(tmp_path/'external/catalog.json').write_text(json.dumps(catalog))
    (tmp_path/'external/lever-verification-hold.json').write_text('{"active":true}')
    store=BatchStore(tmp_path);bid=store.create(100,'submit');r=await run_batch(tmp_path,bid)
    assert r['selected_jobs']==2 and r['inventory']['target_shortfall']==98 and r['inventory']['paused_jobs']==1
    assert r['new_confirmations']==2


class Crash(BaseException):pass


@pytest.mark.parametrize('phase',['before_intent','after_intent','after_confirmation'])
async def test_crash_recovery_obeys_attempt_truth_even_when_manifest_not_updated(tmp_path,monkeypatch,runtime,phase):
    seed(tmp_path,1);store=BatchStore(tmp_path);bid=store.create(1,'submit');calls=[]
    normal=__import__('core.external_batch',fromlist=['execute_external']).execute_external
    async def crash(job,data,resume,execution,status,**kwargs):
        calls.append(job['url']);s=AttemptStore(data);aid=s.begin(job['url']);s.event(aid,'external_batch',{'batch_id':bid,'attempt_id':aid})
        if phase!='before_intent':s.intent(aid,aid,job['url'])
        if phase=='after_confirmation':s.response(aid,200);s.transition(aid,'confirmed','Receipt durable before crash')
        raise Crash()
    monkeypatch.setattr('core.external_batch.execute_external',crash)
    with pytest.raises(Crash):await run_batch(tmp_path,bid)
    monkeypatch.setattr('core.external_batch.execute_external',normal)
    report=await run_batch(tmp_path,bid)
    if phase=='before_intent':
        assert report['new_confirmations']==1 and len(runtime['calls'])==1
        assert len(store.attempts.list())==2
    elif phase=='after_intent':assert report['job_outcomes']=={'uncertain':1} and not runtime['calls']
    else:assert report['job_outcomes']=={'confirmed':1} and report['new_confirmations']==0 and not runtime['calls']


async def test_concurrent_profile_worker_is_not_recovered_or_overwritten(tmp_path,runtime):
    seed(tmp_path,1);store=BatchStore(tmp_path);bid=store.create(1,'submit')
    other=store.attempts.begin('https://job-boards.greenhouse.io/other/jobs/999')
    with FileLock(str(tmp_path/'application-worker.lock'),timeout=0):
        with pytest.raises(Timeout):await run_batch(tmp_path,bid)
    assert store.attempts.get(other)['state']=='running'
    assert store.get(bid)['state']=='created' and not runtime['calls']


async def test_resume_requalifies_changed_requirements_before_opening_browser(tmp_path,runtime):
    seed(tmp_path,1);store=BatchStore(tmp_path);bid=store.create(1,'submit')
    path=tmp_path/'external/catalog.json';catalog=json.loads(path.read_text())
    catalog['jobs'][0]['description']='Requirements:\n8 years of development';path.write_text(json.dumps(catalog))
    report=await run_batch(tmp_path,bid)
    assert report['job_outcomes']=={'pending':1} and runtime['opens']==0
    assert report['items'][0]['detail']['qualification']['reason']=='mandatory_duration_above_confirmed'


async def test_ready_job_does_not_wait_for_an_unrelated_model_proposal(tmp_path,monkeypatch,runtime):
    import asyncio
    jobs=seed(tmp_path,2);rows=json.loads((tmp_path/'jobs.json').read_text())
    lever=dict(jobs[1],platform='lever',source='lever',board='other',url='https://jobs.lever.co/other/00000000-0000-0000-0000-000000000001')
    rows.pop(jobs[1]['url']);rows[lever['url']]=lever;(tmp_path/'jobs.json').write_text(json.dumps(rows))
    catalog=json.loads((tmp_path/'external/catalog.json').read_text());catalog['jobs'][1]=lever;(tmp_path/'external/catalog.json').write_text(json.dumps(catalog))
    release=asyncio.Event();normal=__import__('core.external_batch',fromlist=['execute_external']).execute_external
    async def slow_preflight(data,job):
        await release.wait()
        return {'ready_for_browser':True,'pending':[],'answer_metrics':{'rule':1,'cache':0,'pending':0,'model':0}}
    async def execute(*args,**kwargs):
        result=await normal(*args,**kwargs);release.set();return result
    monkeypatch.setattr('core.greenhouse_apply.preflight_greenhouse',slow_preflight)
    monkeypatch.setattr('core.external_batch.execute_external',execute)
    store=BatchStore(tmp_path);bid=store.create(2,'submit')
    result=await asyncio.wait_for(run_batch(tmp_path,bid),timeout=5)
    assert result['new_confirmations']==2 and runtime['calls'][0]==lever['url']
