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
    assert len(calls)==1
    source=json.loads((path/'confirmed_facts.json').read_text());source['facts'].append({'id':'availability.notice','value':14,'source':'user'})
    (path/'confirmed_facts.json').write_text(json.dumps(source))
    await resolve_answer(AnswerBook(path),q,dict(job,_answer_run_id='third'))
    assert len(calls)==2


async def test_run_budget_and_local_provider_are_enforced(automatic):
    path,calls=automatic
    for i in range(3):
        result=await resolve_answer(AnswerBook(path),{'label':'Question '+str(i),'type':'text'}, {'url':str(i),'_answer_run_id':'run'})
    assert len(calls)==2 and result['proposal_state']=='run_budget_reached'
    (path/'llm_settings.json').write_text(json.dumps({'provider':'openai'}))
    result=await resolve_answer(AnswerBook(path),{'label':'Another question','type':'text'},{'url':'other'})
    assert result['proposal_state']=='local_model_not_configured' and len(calls)==2
