import asyncio
import json
import pytest
from core.automatic_answers import resolve_answer
from core.easy_answers import AnswerBook


@pytest.fixture
def automatic(tmp_path,monkeypatch):
    (tmp_path/'settings.json').write_text(json.dumps({'automation_answers':{'enabled':True,'max_new_per_run':2}}))
    (tmp_path/'llm_settings.json').write_text(json.dumps({'provider':'ollama','ollama':{'model':'qwen3:8b','base_url':'http://127.0.0.1:11434'}}))
    (tmp_path/'confirmed_facts.json').write_text(json.dumps({'facts':[{'id':'name.full','value':'Test Candidate','source':'user'}]}))
    calls=[]
    async def propose(self,aid,settings,**kwargs):
        calls.append(aid);self.store.event('answer:'+str(aid),'model_call',{'model':settings['ollama']['model']})
        await asyncio.sleep(.03)
        with self.store.db() as db:
            db.execute("UPDATE easy_answers SET state='proposed',proposal=?,refs=?,facts_version=? WHERE id=?",
                (json.dumps('Test Candidate'),json.dumps(['name.full']),self.reference_version(['name.full']),aid))
        return {'supported':True,'value':'Test Candidate','refs':['name.full']}
    monkeypatch.setattr(AnswerBook,'propose',propose)
    return tmp_path,calls


async def test_unknown_auto_proposes_once_but_waits_for_first_approval(automatic):
    path,calls=automatic;book=AnswerBook(path);q={'label':'Your preferred display name?','type':'text','options':[]}
    job={'url':'one','eligibility_country':'BR','_answer_run_id':'run'};metrics={'model':0}
    result=await resolve_answer(book,q,job,metrics)
    assert result['approval_required'] and result['proposal_state']=='proposed' and 'value' not in result
    assert metrics['model']==1 and len(calls)==1
    assert 'pending' in await resolve_answer(AnswerBook(path),q,dict(job,url='two'),metrics)
    assert len(calls)==1
    book.approve(result['pending'],'Test Candidate')
    assert (await resolve_answer(AnswerBook(path),q,job,metrics))['source']=='cache'


async def test_parallel_schema_preflight_cannot_propose_the_same_question_twice(automatic):
    path,calls=automatic;q={'label':'Your preferred display name?','type':'text','options':[]}
    results=await asyncio.gather(*(resolve_answer(AnswerBook(path),q,{'url':str(i),'_answer_run_id':'same-run'}) for i in range(3)))
    assert len(calls)==1 and len({r['pending'] for r in results})==1


async def test_missing_facts_are_not_retried_until_the_facts_change(automatic,monkeypatch):
    path,calls=automatic
    async def missing(self,aid,settings,**kwargs):calls.append(aid);return {'supported':False,'reason':'missing_facts'}
    monkeypatch.setattr(AnswerBook,'propose',missing)
    q={'label':'Your professional notice period?','type':'number','options':[]};job={'url':'one'}
    assert (await resolve_answer(AnswerBook(path),q,job))['proposal_state']=='unsupported'
    await resolve_answer(AnswerBook(path),q,dict(job,_answer_run_id='another'))
    assert len(calls)==0
    source=json.loads((path/'confirmed_facts.json').read_text());source['facts'].append({'id':'availability.notice','value':14,'source':'user'})
    (path/'confirmed_facts.json').write_text(json.dumps(source))
    await resolve_answer(AnswerBook(path),q,dict(job,_answer_run_id='third'))
    assert len(calls)==1


async def test_run_budget_and_local_provider_are_enforced(automatic):
    path,calls=automatic
    for i in range(3):
        result=await resolve_answer(AnswerBook(path),{'label':'Question '+str(i),'type':'text'}, {'url':str(i),'_answer_run_id':'run'})
    assert len(calls)==2 and result['proposal_state']=='run_budget_reached'
    (path/'llm_settings.json').write_text(json.dumps({'provider':'openai'}))
    result=await resolve_answer(AnswerBook(path),{'label':'Another question','type':'text'},{'url':'other'})
    assert result['proposal_state']=='local_model_not_configured' and len(calls)==2


async def test_cached_proposal_with_irrelevant_refs_is_not_reused(automatic,monkeypatch):
    path,calls=automatic;book=AnswerBook(path)
    q={'label':'Your professional notice period?','type':'text','options':[]};job={'url':'job'}
    aid=book.resolve(q,job)['pending']
    with book.store.db() as db:
        db.execute("UPDATE easy_answers SET state='proposed',proposal=?,refs=?,facts_version=? WHERE id=?",
            (json.dumps('Immediately'),json.dumps(['name.full']),book.reference_version(['name.full']),aid))
    async def missing(self,aid,settings,**kwargs):
        calls.append(aid);return {'supported':False,'reason':'missing_facts'}
    monkeypatch.setattr(AnswerBook,'propose',missing)
    result=await resolve_answer(AnswerBook(path),q,job)
    assert result['proposal_state']=='unsupported' and len(calls)==0
    assert 'value' not in result and not result.get('approval_required')


@pytest.fixture
def autonomous(automatic,monkeypatch):
    path,calls=automatic
    (path/'settings.json').write_text(json.dumps({'automation_answers':{'enabled':True,'mode':'autonomous'}}))
    tailored=[]
    async def tailor(self,aid,settings,*,job):
        tailored.append((aid,job['url']))
        row=self.get(aid)
        if row['field'].get('options'):return {'supported':True,'value':row['field']['options'][-1]['value'],'refs':[]}
        return {'supported':True,'value':'Tailored for '+job['url'],'refs':['name.full']}
    monkeypatch.setattr(AnswerBook,'tailor',tailor)
    return path,calls,tailored


async def test_autonomous_choices_are_answered_once_and_reused_without_approval(autonomous):
    path,calls,tailored=autonomous
    q={'label':'Como você avalia seu conhecimento com Kotlin Backend?','type':'radio','options':[{'value':'1','text':'Básico'},{'value':'2','text':'Intermediário'}]}
    metrics={'rule':0,'cache':0,'pending':0,'model':0,'autonomous':0}
    first=await resolve_answer(AnswerBook(path),q,{'url':'one','_answer_run_id':'run'},metrics)
    assert first['source']=='autonomous' and first['value']=='2' and first['answer_id']
    row=next(r for r in AnswerBook(path).list() if r['id']==first['answer_id'])
    assert row['state']=='approved' and row['source']=='model'
    second=await resolve_answer(AnswerBook(path),q,{'url':'two','_answer_run_id':'run'},metrics)
    assert second['source']=='cache' and second['value']=='2' and len(tailored)==1 and not calls


async def test_autonomous_narratives_are_tailored_per_job(autonomous):
    path,calls,tailored=autonomous
    q={'label':'Poderia descrever sua experiência prática com Inteligência Artificial?','type':'textarea','options':[]}
    one=await resolve_answer(AnswerBook(path),q,{'url':'one'})
    again=await resolve_answer(AnswerBook(path),q,{'url':'one'})
    two=await resolve_answer(AnswerBook(path),q,{'url':'two'})
    assert one['value']==again['value']=='Tailored for one' and two['value']=='Tailored for two'
    assert len(tailored)==2 and 'answer_id' not in one


async def test_autonomous_checkable_facts_need_confirmed_evidence(autonomous,monkeypatch):
    path,calls,tailored=autonomous
    async def missing(self,aid,settings,**kwargs):calls.append(aid);return {'supported':False,'reason':'missing_facts'}
    monkeypatch.setattr(AnswerBook,'propose',missing)
    q={'label':'Do you have a Bachelor\'s in computer science?','type':'select-one','options':[{'value':'y','text':'Yes'},{'value':'n','text':'No'}]}
    result=await resolve_answer(AnswerBook(path),q,{'url':'one'})
    assert 'pending' in result and result['proposal_state']=='unsupported' and not tailored and len(calls)==1


async def test_autonomous_grounded_fact_proposals_are_accepted(autonomous):
    path,calls,tailored=autonomous
    q={'label':'Confirm your full legal name as on your degree','type':'text','options':[]}
    result=await resolve_answer(AnswerBook(path),q,{'url':'one'})
    assert result['source']=='autonomous' and result['value']=='Test Candidate' and len(calls)==1 and not tailored


async def test_unknown_experience_is_not_asked_again_until_facts_change(autonomous,monkeypatch):
    path,calls,tailored=autonomous
    async def unknown(self,aid,settings,*,job):tailored.append(aid);return {'supported':False,'reason':'unknown_experience','missing':['Kotlin']}
    monkeypatch.setattr(AnswerBook,'tailor',unknown)
    q={'label':'Como você avalia seu conhecimento com Kotlin?','type':'select-one','options':[{'value':'1','text':'Básico'}]}
    first=await resolve_answer(AnswerBook(path),q,{'url':'one'})
    second=await resolve_answer(AnswerBook(path),q,{'url':'two'})
    assert 'pending' in first and second['proposal_reason']=='unknown_experience' and len(tailored)==1
    source=json.loads((path/'confirmed_facts.json').read_text());source['facts'].append({'id':'skills','value':['Kotlin']})
    (path/'confirmed_facts.json').write_text(json.dumps(source))
    await resolve_answer(AnswerBook(path),q,{'url':'three'})
    assert len(tailored)==2


async def test_resolve_pending_answers_stored_questions_in_their_job_context(autonomous):
    from core.automatic_answers import resolve_pending
    path,calls,tailored=autonomous
    (path/'jobs.json').write_text(json.dumps({'https://job-boards.greenhouse.io/acme/jobs/1':{'company':'acme','title':'Backend'}}))
    q={'label':'Why do you want to work here?','type':'textarea','options':[]}
    job={'url':'https://job-boards.greenhouse.io/acme/jobs/1','eligibility_country':'BR','form_language':'en','company':'acme'}
    AnswerBook(path).resolve(q,job)
    seen=[]
    assert await resolve_pending(path,progress=lambda row,state:seen.append(state))=={'answered':1}
    assert seen==['answered'] and tailored==[(1,job['url'])]
    assert (await resolve_answer(AnswerBook(path),q,dict(job)))['value']=='Tailored for '+job['url']


async def test_autonomous_fact_proposals_about_unshown_experience_stay_pending(autonomous,monkeypatch):
    path,calls,tailored=autonomous
    async def yes(self,aid,settings,**kwargs):
        with self.store.db() as db:
            db.execute("UPDATE easy_answers SET state='proposed',proposal=?,refs=?,facts_version=? WHERE id=?",
                (json.dumps('y'),json.dumps(['name.full']),self.reference_version(['name.full']),aid))
        return {'supported':True,'value':'y','refs':['name.full']}
    monkeypatch.setattr(AnswerBook,'propose',yes)
    q={'label':'Do you have at least 3 years of data engineering experience?','type':'select-one','options':[{'value':'y','text':'Yes'},{'value':'n','text':'No'}]}
    result=await resolve_answer(AnswerBook(path),q,{'url':'one'})
    assert 'pending' in result and result['proposal_reason']=='unknown_experience'


async def test_question_first_stored_as_optional_still_resolves_when_required(autonomous):
    path,calls,tailored=autonomous
    q={'label':'How did you hear about this opportunity?','type':'select-one','options':[{'value':'1','text':'LinkedIn'},{'value':'2','text':'Other'}]}
    AnswerBook(path).resolve(dict(q,required=False),{'url':'one'})
    result=await resolve_answer(AnswerBook(path),dict(q,required=True),{'url':'two'})
    assert result['source']=='autonomous' and result['value']=='2'
