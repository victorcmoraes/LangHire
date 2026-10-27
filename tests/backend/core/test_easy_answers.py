import json
import pytest
from core.easy_answers import AnswerBook, sync_profile_facts
from core.application_safety import SafetyError


def book(tmp_path):
    (tmp_path/'confirmed_facts.json').write_text(json.dumps({'facts':[
        {'id':'name.full','value':'Test Candidate','source':'user'},
        {'id':'email','value':'test@example.invalid','source':'user'}]}))
    return AnswerBook(tmp_path)


def field(label='Years of experience', **kwargs):
    return dict(label=label,type='number',options=[],required=True,**kwargs)


def test_unknown_never_uses_defaults_and_equivalent_questions_group_by_context(tmp_path):
    b=book(tmp_path);job={'url':'job1','eligibility_country':'BR','form_language':'en'}
    a=b.resolve(field(),job)
    assert 'pending' in a
    assert b.resolve(field(),dict(job,url='job2'))['pending']==a['pending']
    assert len(b.list()[0]['jobs'])==2
    assert b.resolve(field(),dict(job,eligibility_country='US'))['pending']!=a['pending']


def test_human_approval_reuses_and_fact_changes_invalidate(tmp_path):
    b=book(tmp_path);job={'url':'job1','eligibility_country':'BR'}
    aid=b.resolve(field(),job)['pending'];b.approve(aid,'3')
    assert b.resolve(field(),job)['value']=='3'
    source=json.loads((tmp_path/'confirmed_facts.json').read_text());source['facts'][0]['value']='Changed'
    (tmp_path/'confirmed_facts.json').write_text(json.dumps(source))
    assert 'pending' in AnswerBook(tmp_path).resolve(field(),job)


def test_country_currency_period_and_options_are_not_interchangeable(tmp_path):
    b=book(tmp_path);q=field('Salary');j={'url':'job','eligibility_country':'BR','salary_currency':'BRL','salary_period':'monthly'}
    aid=b.resolve(q,j)['pending'];b.approve(aid,8000)
    assert b.resolve(q,j)['source']=='cache'
    assert 'pending' in b.resolve(q,dict(j,salary_period='annual'))
    assert 'pending' in b.resolve(q,dict(j,salary_currency='USD'))
    q=dict(label='Authorization',type='select-one',options=[{'text':'Yes','value':'yes'}])
    aid=b.resolve(q,j)['pending']
    with pytest.raises(SafetyError):b.approve(aid,'invented-option')


def test_direct_facts_bypass_model_but_never_infer_years(tmp_path):
    b=book(tmp_path);j={'url':'job'}
    assert b.resolve(dict(label='Full name',type='text',options=[]),j)['value']=='Test Candidate'
    assert 'pending' in b.resolve(field(),j)


def test_profile_edits_replace_affected_facts_without_importing_untouched_defaults(tmp_path):
    book(tmp_path)
    sync_profile_facts(tmp_path,{'email':'test@example.invalid','years_of_experience':0},
                       {'email':'new@example.invalid','years_of_experience':0})
    b=AnswerBook(tmp_path)
    assert b.facts['email']['value']=='new@example.invalid'
    assert b.facts['email']['source']=='user:profile-edit'
    assert 'experience.years' not in b.facts


def test_actual_linkedin_contact_labels_and_country_calling_code(tmp_path):
    book(tmp_path)
    source=json.loads((tmp_path/'confirmed_facts.json').read_text())
    source['facts'] += [{'id':'phone.countryCode','value':'+55','source':'user'}, {'id':'phone','value':'+5581999999999','source':'user'}]
    (tmp_path/'confirmed_facts.json').write_text(json.dumps(source))
    b=AnswerBook(tmp_path);job={'url':'job','eligibility_country':'BR'}
    assert b.resolve({'label':'E-mail*','type':'select-one','options':[{'text':'test@example.invalid','value':'test@example.invalid'}]},job)['value']=='test@example.invalid'
    assert b.resolve({'label':'Código do país*','type':'select-one','options':[{'text':'Brasil (+55)','value':'BR'},{'text':'Portugal (+351)','value':'PT'}]},job)['value']=='BR'
    assert b.resolve({'label':'Número de celular*','type':'tel','options':[],'separate_country_code':True},job)['value']=='81999999999'


async def test_model_with_missing_support_cannot_become_answer(tmp_path,monkeypatch):
    b=book(tmp_path);aid=b.resolve(field(),{'url':'job'})['pending']
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'message':{'content':json.dumps({'supported':True,'value':99,'refs':['invented']})}}).encode()
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:Response())
    with pytest.raises(SafetyError):await b.propose(aid,{'ollama':{'model':'qwen3:8b'}})
    assert b.list()[0]['state']=='pending'


async def test_valid_model_proposal_still_requires_human_approval(tmp_path,monkeypatch):
    b=book(tmp_path);q=dict(label='Contact email please',type='text',options=[]);j={'url':'job'}
    aid=b.resolve(q,j)['pending']
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'message':{'content':json.dumps({'supported':True,'value':'test@example.invalid','refs':['email']})}}).encode()
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:Response())
    await b.propose(aid,{'ollama':{'model':'qwen3:8b'}})
    assert 'pending' in b.resolve(q,j)
    b.approve(aid,'test@example.invalid')
    assert b.resolve(q,j)['source']=='cache'
