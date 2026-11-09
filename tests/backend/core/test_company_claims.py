import json
import math
import pytest

from core.application_safety import AttemptStore
from core.company_claims import CompanyClaims,claim,interval,stack_key
from core.easy_answers import AnswerBook


def text(label):
    return {'label':label,'type':'text'}


def select(label,*options):
    return {'label':label,'type':'select-one','options':[{'value':str(i),'text':t} for i,t in enumerate(options)]}


def job(n,company='ciandt'):
    return {'url':f'https://jobs.lever.co/{company}/{n}','company':company,'title':'Developer'}


@pytest.mark.parametrize('question,key',[
    ('Há quantos anos você já usa Java no trabalho?','java'),
    ('How many years of experience working with SQL and Python?','python+sql'),
    ('For how long have you worked with .Net?','dotnet'),
    ('How long have you worked in Development?','general'),
    ('Quantos anos de experiência você possui com desenvolvimento backend utilizando Java?','java'),
    ('Are you at least 18 years of age?',None),
    ('Swile has my consent to retain my data for up to 10 years',None),
    ('Você possui experiência com React?',None),
])
def test_stack_of_a_years_question(question,key):
    assert stack_key(question)==key


@pytest.mark.parametrize('answer,span',[('3',(3,3)),('4 years',(4,4)),('3 a 5 anos',(3,5)),('5+ years',(5,math.inf)),
                                        ('Mais de 5 anos',(5,math.inf)),('Menos de 1 ano',(0,1)),('No experience',(0,0))])
def test_interval_of_an_answer(answer,span):
    assert interval(answer)==span


def test_a_single_checked_range_is_a_claim():
    field=dict(select('Quantos anos de experiência profissional você tem com Python em backend?','Menos de 3 anos','3 a 5 anos'),type='checkbox-group')
    assert claim(field,['1'])==('python',(3.0,5.0)) and claim(field,['0','1']) is None


def test_threshold_yes_no_questions_state_an_open_or_closed_range():
    field={'label':'Do you have 3+ years of Python experience?','type':'checkbox'}
    assert claim(field,True)==('python',(3.0,math.inf)) and claim(field,False)==('python',(0.0,3.0))


def test_first_claims_settle_and_contradictions_are_rewritten(tmp_path):
    claims=CompanyClaims(AttemptStore(tmp_path))
    general='How many years of professional development experience?'
    assert claims.apply(text(general),job(1),{'value':'3','source':'cache'})['value']=='3'
    adjusted=claims.apply(text('How long have you worked in Development?'),job(2),{'value':'4 years','source':'cache','approval_value':'4 years'})
    assert adjusted['value']=='3 years' and adjusted['approval_value']=='4 years' and adjusted['company_claim']['replaced']=='4 years'
    options=select('Há quantos anos você trabalha com desenvolvimento de software?','Menos de 2 anos','2 a 4 anos','Mais de 4 anos')
    assert claims.apply(options,job(3),{'value':'2','source':'cache'})['value']=='1'
    # Another company has heard nothing yet.
    assert claims.apply(text(general),job(4,'acme'),{'value':'4','source':'cache'})['value']=='4'


def test_a_control_that_cannot_state_the_claim_waits_for_a_person(tmp_path):
    claims=CompanyClaims(AttemptStore(tmp_path))
    claims.apply(text('How many years with Java?'),job(1),{'value':'3','source':'cache'})
    late=select('Years of Java experience','Less than 1 year','More than 5 years')
    result=claims.apply(late,job(2),{'value':'1','source':'cache'})
    assert result['pending']=='company-claim-conflict' and result['conflict']['stack']=='java'


def test_reapplying_to_the_same_job_is_not_a_conflict_with_itself(tmp_path):
    claims=CompanyClaims(AttemptStore(tmp_path))
    first=claims.apply(text('How many years with Python?'),job(1),{'value':'3','source':'cache'})
    assert claims.apply(text('How many years with Python?'),job(1),first)['value']=='3'


def test_backfill_reads_answers_of_sent_applications_only(tmp_path):
    store=AttemptStore(tmp_path);book=AnswerBook(tmp_path)
    sent,draft=job(1),job(2)
    aid=store.begin(sent['url']);store.transition(aid,'confirmed','ok');store.begin(draft['url'])
    (tmp_path/'jobs.json').write_text(json.dumps({sent['url']:sent,draft['url']:draft}))
    with store.db() as db:
        for n,(url,value) in enumerate([(sent['url'],'2'),(draft['url'],'7')]):
            db.execute("INSERT INTO easy_answers(key,question,context,field,state,answer,updated_at) VALUES(?,?,?,?,?,?,?)",
                       (f'k{n}','Years with .NET?','{}',json.dumps(text('Years with .NET?')),'approved',json.dumps(value),'2026-10-30'))
            db.execute('INSERT INTO easy_answer_jobs VALUES(?,?)',(n+1,url))
        db.execute('DROP TABLE company_claims')
    claims=CompanyClaims(store)
    assert claims.settled('ciandt','dotnet','https://jobs.lever.co/ciandt/9')==(2,2)
    assert book.claims.apply(text('How long with .Net?'),job(9),{'value':'5','source':'cache'})['value']=='2'
