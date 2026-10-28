import json
import pytest
from core.easy_answers import AnswerBook, sync_profile_facts, valid_value, damaged_text, proposal_fact_sources
from core.application_safety import SafetyError


def book(tmp_path):
    (tmp_path/'confirmed_facts.json').write_text(json.dumps({'facts':[
        {'id':'name.full','value':'Test Candidate','source':'user'},
        {'id':'email','value':'test@example.invalid','source':'user'}]}))
    return AnswerBook(tmp_path)


def field(label='Years of experience', **kwargs):
    return dict(label=label,type='number',options=[],required=True,**kwargs)


def test_portuguese_roundtrip_and_rejection_of_damaged_application_answers(tmp_path):
    b=book(tmp_path);q={'label':'Descreva uma integração','type':'textarea','options':[]}
    text='Integração entre APIs: validação, preços, simulação e solução com revisão.'
    aid=b.resolve(q,{'url':'job'})['pending'];b.approve(aid,text)
    assert AnswerBook(tmp_path).resolve(q,{'url':'job'})['value']==text
    assert valid_value(q,text)
    for broken in ['Integra??o entre APIs','Valida\ufffd\ufffdo',text.encode('utf-8').decode('latin1')]:
        assert damaged_text(broken)
        with pytest.raises(SafetyError):b.approve(aid,broken)
    assert not damaged_text('Ready? Yes. Você confirma?')
    assert not damaged_text('https://example.invalid/project?ref=application')


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


def test_ats_option_ids_share_one_pending_and_remap_one_approval(tmp_path):
    b=book(tmp_path);job={'url':'job1','eligibility_country':'BR','form_language':'en','company':'Example'}
    first={'label':'Have you previously worked at this company?','type':'select-one',
           'options':[{'text':'Yes','value':'101'},{'text':'No','value':'102'}]}
    second=dict(first,options=[{'text':'No','value':'9002'},{'text':'Yes','value':'9001'}])
    aid=b.resolve(first,job)['pending']
    assert b.resolve(second,dict(job,url='job2'))['pending']==aid
    b.approve(aid,'102')
    answer=b.resolve(second,dict(job,url='job2'))
    assert answer['value']=='9002' and answer['approval_value']=='102' and answer['answer_id']==aid
    assert len(b.list()[0]['jobs'])==2
    assert 'pending' in b.resolve(second,dict(job,url='other',company='Different'))
    assert 'pending' in b.resolve(second,dict(job,url='us',eligibility_country='US'))
    changed=dict(second,options=second['options']+[{'text':'Contractor','value':'9003'}])
    assert 'pending' in b.resolve(changed,job)
    source=json.loads((tmp_path/'confirmed_facts.json').read_text());source['facts'][0]['value']='Changed'
    (tmp_path/'confirmed_facts.json').write_text(json.dumps(source))
    assert 'pending' in AnswerBook(tmp_path).resolve(second,job)


def test_checkbox_group_validates_and_reuses_only_approved_choices(tmp_path):
    b=book(tmp_path);job={'url':'one','eligibility_country':'BR'}
    q={'label':'How did you hear about us?','type':'checkbox-group','required':True,
       'options':[{'text':'Company website','value':'1'},{'text':'Referral','value':'2'}]}
    aid=b.resolve(q,job)['pending']
    for invalid in [True,'1',[],['1','1'],['missing']]:
        with pytest.raises(SafetyError):b.approve(aid,invalid)
    b.approve(aid,['1'])
    other=dict(q,options=[{'text':'Referral','value':'new2'},{'text':'Company website','value':'new1'}])
    assert b.resolve(other,dict(job,url='two'))['value']==['new1']
    assert b.resolve(other,dict(job,url='two'))['approval_value']==['1']


def test_negative_affiliation_fact_is_not_inferred_from_an_incomplete_history(tmp_path):
    b=book(tmp_path);job={'url':'one','company':'Exadel'}
    q={'label':"Have you previously been employed or contracted by Exadel or any of its subsidiaries?",'type':'select-one',
       'options':[{'text':'No','value':'n'},{'text':'Yes','value':'y'}]}
    assert 'pending' in b.resolve(q,job)
    source=json.loads((tmp_path/'confirmed_facts.json').read_text())
    source['facts'].append({'id':'employment.affiliation.exadel','value':False,'source':'user'})
    (tmp_path/'confirmed_facts.json').write_text(json.dumps(source));b=AnswerBook(tmp_path)
    assert b.resolve(q,job)['value']=='n'
    assert 'pending' in b.resolve(dict(q,label=q['label'].replace('Exadel','Different')),job)


def test_confirmed_identity_and_language_are_scoped_and_do_not_claim_native_english(tmp_path):
    source={'facts':[{'id':k,'value':v,'source':'user'} for k,v in {
        'identity.cpf':'12345678909','nationality':'Brazilian','level.english':'Proficient','location.country':'Brazil'}.items()]}
    (tmp_path/'confirmed_facts.json').write_text(json.dumps(source));b=AnswerBook(tmp_path)
    br={'url':'br-job','eligibility_country':'BR','form_language':'pt'}
    assert b.resolve({'label':'CPF','type':'text','options':[]},br)['value']=='12345678909'
    assert b.resolve({'label':'Nacionalidade','type':'text','options':[]},br)['value']=='Brasileira'
    q={'label':'Are you a citizen or permanent resident of the country where this position is based?','type':'select-one','options':[{'text':'Yes','value':'y'},{'text':'No','value':'n'}]}
    assert b.resolve(q,br)['value']=='y'
    assert 'pending' in b.resolve(q,dict(br,url='us-job',eligibility_country='US'))
    language={'label':'English Level','type':'select-one','options':[{'text':'Full professional proficiency','value':'pro'},{'text':'Native or bilingual proficiency','value':'native'}]}
    assert b.resolve(language,br)['value']=='pro'
    portuguese={'label':'Você está legalmente autorizado(a) a trabalhar no país onde a vaga está localizada?',
                'type':'select-one','options':[{'text':'Sim','value':'s'},{'text':'Não','value':'n'}]}
    assert b.resolve(portuguese,br)['value']=='s'
    assert 'pending' in b.resolve(portuguese,dict(br,url='foreign',eligibility_country='US'))
    demographic={'label':'Qual é a sua raça/etnia?','type':'select-one','category':'demographic',
                 'options':[{'text':'Não quero responder','value':'decline','decline_to_answer':True}]}
    assert b.resolve(demographic,br)['value']=='decline'
    assert b.context(portuguese['label'],dict(br,form_language='en'))['language']=='pt'


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
    result=await b.propose(aid,{'ollama':{'model':'qwen3:8b'}})
    assert result['supported'] is False
    assert result['reason']=='invalid_proposal'
    assert b.list()[0]['state']=='pending'


async def test_pending_model_result_cannot_overwrite_a_new_human_approval(tmp_path,monkeypatch):
    b=book(tmp_path);q={'label':'Your display name please','type':'text','options':[]}
    aid=b.resolve(q,{'url':'one'})['pending']
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):
            b.approve(aid,'Human choice')
            return json.dumps({'message':{'content':json.dumps({'supported':True,'value':'Model choice','refs':['name.full']})}}).encode()
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:Response())
    result=await b.propose(aid,{'ollama':{'model':'qwen3:8b'}})
    assert result['reason']=='question_changed_during_proposal'
    assert b.resolve(q,{'url':'one'})['value']=='Human choice'


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


@pytest.mark.parametrize('question',[
    'When are you available to begin work at Encora?',
    'Qual sua disponibilidade para início?',
    'Have you ever been a Group Example employee?',
    'I hereby consent to receive text messages from Example.',
    'What is your current base salary? (in reais)',
    'Do you currently receive any variable compensation (Annual Bonus, Commissions, Stock Options, RSUs)?',
    'Do you have any relatives currently working at Example?',
    'What was your undergraduate GPA (Coeficiente de Rendimento or IRA)?',
    'If it is from an EVENT or you clicked OTHER, please specify:',
    'If you checked any of the options above, please provide the details below.',
])
async def test_unrelated_facts_cannot_support_sensitive_factual_proposals(tmp_path,monkeypatch,question):
    b=book(tmp_path)
    source=json.loads((tmp_path/'confirmed_facts.json').read_text())
    source['facts'] += [{'id':'employer.current','value':'Consultancy','source':'user'},
                        {'id':'experience.start','value':'2023','source':'user'}]
    (tmp_path/'confirmed_facts.json').write_text(json.dumps(source))
    b=AnswerBook(tmp_path)
    q={'label':question,'type':'text','options':[]}
    aid=b.resolve(q,{'url':'job','company':'Example'})['pending']
    def forbidden(*args,**kwargs):raise AssertionError('Missing facts must not trigger inference')
    monkeypatch.setattr('urllib.request.urlopen',forbidden)
    assert (await b.propose(aid,{}))['reason']=='missing_facts'
    assert b.list()[0]['state']=='pending'


@pytest.mark.parametrize('question,options,language,expected',[
    ('When are you available to begin work at Encora?', [{'text':'Immediately','value':'now'},{'text':'In 1 month','value':'later'}], 'en','now'),
    ('Qual sua disponibilidade para início?', [], 'pt','Imediato'),
])
def test_explicit_immediate_availability_is_reused_without_model(tmp_path,question,options,language,expected):
    book(tmp_path)
    path=tmp_path/'confirmed_facts.json';source=json.loads(path.read_text())
    source['facts'].append({'id':'availability.immediate','value':True,'source':'user'})
    path.write_text(json.dumps(source))
    b=AnswerBook(tmp_path);q={'label':question,'type':'text' if not options else 'select-one','options':options}
    result=b.resolve(q,{'url':'job','form_language':language})
    assert result=={'value':expected,'refs':['availability.immediate'],'source':'rule'}
    source['facts'].pop();path.write_text(json.dumps(source))
    assert 'pending' in AnswerBook(tmp_path).resolve(q,{'url':'job','form_language':language})


async def test_factual_proposal_cannot_cite_unrelated_employment_fact(tmp_path,monkeypatch):
    book(tmp_path)
    path=tmp_path/'confirmed_facts.json';source=json.loads(path.read_text())
    source['facts'].append({'id':'availability.notice','value':'2 weeks','source':'user'})
    path.write_text(json.dumps(source))
    b=AnswerBook(tmp_path)
    aid=b.resolve({'label':'Your notice period?','type':'text','options':[]},{'url':'job'})['pending']
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'message':{'content':json.dumps({'supported':True,'value':'Immediately','refs':['name.full']})}}).encode()
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:Response())
    assert (await b.propose(aid,{}))['reason']=='invalid_proposal'
    assert b.list()[0]['state']=='pending'


def test_sms_consent_and_affiliation_evidence_stay_bound_to_the_confirmed_company():
    sources={'consent.sms.example':{'value':True,'scope':'Example'},
             'employment.affiliation.example':{'value':False},
             'employment.previous':{'value':False,'scope':'Other'},
             'employer.current':{'value':'Consultancy'}}
    assert set(proposal_fact_sources('I consent to receive text messages',{'company':'Example'},sources))=={'consent.sms.example'}
    assert proposal_fact_sources('I consent to receive text messages',{'company':'Other'},sources)=={}
    assert set(proposal_fact_sources('Have you ever been an Example employee?',{'company':'Example'},sources))=={'employment.affiliation.example'}
    assert proposal_fact_sources('Have you ever been an employee?',{'company':''},sources)=={}


@pytest.mark.parametrize('label,ref,level',[
    ('Qual o seu nível de inglês?','level.english','Proficient'),
    ('Qual o seu nível de espanhol?','level.spanish','Advanced'),
])
def test_portuguese_language_labels_reuse_confirmed_level_without_inference(tmp_path,label,ref,level):
    book(tmp_path);path=tmp_path/'confirmed_facts.json';data=json.loads(path.read_text())
    data['facts'].append({'id':ref,'value':level,'source':'user'})
    path.write_text(json.dumps(data))
    field={'label':label,'type':'select-one','options':[{'text':'Avançado (consigo conversar profissionalmente)','value':'advanced'},{'text':'Nativo','value':'native'}]}
    answer=AnswerBook(tmp_path).resolve(field,{'url':'job','eligibility_country':'BR'})
    assert answer=={'value':'advanced','refs':[ref],'source':'rule'}


def test_company_relationship_and_discovery_use_actual_job_context(tmp_path):
    b=book(tmp_path);job={'url':'https://job-boards.greenhouse.io/example/jobs/123','company':'Example','collection_origin':'bounded_public_greenhouse_board'}
    assert b.context('Você trabalha em um cliente ou parceiro?',job)['company']=='Example'
    q={'label':'Como ficou sabendo deste processo seletivo?','type':'select-one','options':[{'text':'Linkedin','value':'li'},{'text':'Site da Example - Trabalhe Conosco','value':'careers'},{'text':'Outros','value':'other'}]}
    assert b.resolve(q,job)['value']=='careers'
    assert 'pending' in b.resolve(q,dict(job,collection_origin='unknown'))
    assert 'pending' in b.resolve(q,dict(job,url='https://example.invalid/jobs/123'))
    q['options']=q['options'][::2]
    assert b.resolve(q,job)['value']=='other'
    detail={'label':'If it is from an EVENT or you clicked OTHER, please specify:','type':'text','options':[]}
    assert b.resolve(detail,job)['value']=='Greenhouse careers page'


def test_salary_expectation_and_recent_process_confirmation_do_not_leak_to_other_roles(tmp_path):
    b=book(tmp_path);job={'url':'one','company':'Example','title':'Backend','salary_currency':'BRL','salary_period':'monthly'}
    q={'label':'Pretensão Salarial','type':'text','options':[]}
    aid=b.resolve(q,job)['pending'];b.approve(aid,'7000')
    assert b.resolve(q,job)['value']=='7000'
    assert 'pending' in b.resolve(q,dict(job,title='Data'))
    assert 'pending' in b.resolve(q,dict(job,company='Other'))
    q={'label':'Você participou de processo seletivo da Example nos últimos 6 meses?','type':'text','options':[]}
    aid=b.resolve(q,job)['pending'];b.approve(aid,'Não')
    assert 'pending' in b.resolve(q,dict(job,url='two'))


@pytest.mark.parametrize('label',[
    'Sinalize com qual raça/cor você se identifica',
    'Sinalize com qual identidade de gênero você se identifica',
    'Sinalize se você se identifica parte da comunidade LGBTQIA+',
    'Você possui algum tipo de deficiência?',
])
async def test_custom_demographic_questions_decline_disclosure_and_never_call_model(tmp_path,monkeypatch,label):
    b=book(tmp_path);job={'url':'job','eligibility_country':'BR'}
    q={'label':label,'type':'select-one','options':[{'text':'Sim','value':'yes'},{'text':'Prefiro não informar','value':'decline'}]}
    assert b.resolve(q,job)=={'value':'decline','refs':[],'source':'rule'}
    q['options']=q['options'][:1];aid=b.resolve(q,job)['pending']
    def forbidden(*a,**kw):raise AssertionError('Do not infer demographic attributes')
    monkeypatch.setattr('urllib.request.urlopen',forbidden)
    assert (await b.propose(aid,{}))['reason']=='missing_facts'


def test_salary_units_can_be_confirmed_on_pending_question_without_cross_job_leak(tmp_path):
    b=book(tmp_path);q=field('Salary');j={'url':'job','eligibility_country':'BR'}
    aid=b.resolve(q,j)['pending']
    with pytest.raises(SafetyError):b.approve(aid,7000)
    b.approve(aid,7000,{'currency':'BRL','period':'monthly'})
    assert b.resolve(q,j)['value']==7000
    assert 'pending' in b.resolve(q,dict(j,url='other-job'))
    assert 'pending' in b.resolve(q,dict(j,salary_currency='USD',salary_period='monthly'))


def test_salary_context_cannot_override_explicit_units_or_omit_income_units(tmp_path):
    b=book(tmp_path);j={'url':'job','salary_currency':'USD','salary_period':'annual'}
    aid=b.resolve(field('Salary'),j)['pending']
    with pytest.raises(SafetyError):b.approve(aid,7000,{'currency':'BRL','period':'monthly'})
    income=b.resolve(field('Renda mensal atual'),{'url':'job'})['pending']
    with pytest.raises(SafetyError):b.approve(income,7000)
    b.approve(income,7000,{'currency':'BRL','period':'monthly'})


async def test_model_abstention_is_normal_result_and_preserves_pending(tmp_path,monkeypatch):
    b=book(tmp_path);aid=b.resolve(field(),{'url':'job'})['pending']
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'message':{'content':json.dumps({'supported':False,'value':None,'refs':[]})}}).encode()
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:Response())
    result=await b.propose(aid,{'ollama':{'model':'qwen3:8b'}})
    assert result['supported'] is False
    assert result['reason']=='missing_facts'
    assert b.list()[0]['state']=='pending'


async def test_open_answer_uses_repository_files_and_invalidates_if_evidence_changes(tmp_path,monkeypatch):
    book(tmp_path);catalog=tmp_path/'github';catalog.mkdir()
    path=catalog/'integration.json'
    record={'repository':'candidate/integration','commit':'one','evidence':[
        {'path':'README.md','ref':'file:1','url':'https://github.com/candidate/integration/blob/one/README.md',
         'content':'Integration experiment: compared batch and webhook delivery.'}],
        'analysis':{'application_draft':'UNVERIFIED_MODEL_CLAIM'}}
    path.write_text(json.dumps(record))
    b=AnswerBook(tmp_path);q={'label':'Describe an integration experiment','type':'textarea','options':[]}
    aid=b.resolve(q,{'url':'job'})['pending'];ref='github:candidate/integration:README.md'
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'message':{'content':json.dumps({'supported':True,'value':'Compared batch and webhook delivery.','refs':[ref]})}}).encode()
    def model(req,**kwargs):
        payload=json.loads(req.data)
        assert ref in payload['format']['properties']['refs']['items']['enum']
        assert 'UNVERIFIED_MODEL_CLAIM' not in str(payload)
        assert 'professional' in payload['messages'][0]['content']
        return Response()
    monkeypatch.setattr('urllib.request.urlopen',model)
    result=await b.propose(aid,{})
    assert result['supported'] is True
    assert 'pending' in b.resolve(q,{'url':'job'})
    b.approve(aid,result['value'])
    assert b.resolve(q,{'url':'job'})['source']=='cache'
    record['commit']='two';path.write_text(json.dumps(record))
    assert 'pending' in AnswerBook(tmp_path).resolve(q,{'url':'job'})
