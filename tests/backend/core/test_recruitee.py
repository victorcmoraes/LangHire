import hashlib
import io
import json
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError,URLError
from urllib.parse import unquote
import pytest

from core.application_safety import AttemptStore,SafetyError
from core.easy_answers import AnswerBook
from core.external_apply import canonical_ats,execute_external,platform_hold
from core.external_boards import board_candidates,add_boards,suggest_boards
from core.external_catalog import refresh_board,refresh_imported,recruitee_job
from core.external_discovery import board_spec,discover
from core.external_qualification import qualify,triage
from core.external_service import queue,run_external,update_projection
from core.external_export import export_confirmed
from core.recruitee_apply import schema_fields


URL='https://fictional-cloud.recruitee.com/o/backend-engineer'
BASE='https://fictional-cloud.recruitee.com/api/offers/'


def offer(**extra):
    return {'id':123,'slug':'backend-engineer','title':'Backend Engineer','status':'published',
        'careers_url':URL,'careers_apply_url':URL+'/c/new','city':'Recife','country':'Brazil','country_code':'BR',
        'remote':True,'hybrid':False,'on_site':False,'description':'<p>Build services.</p>',
        'requirements':'<p>3 years of experience</p>','open_questions':[],**extra}


def question(identifier=1,kind='string',required=True,body='Nationality',choices=()):
    return {'id':identifier,'kind':kind,'required':required,'position':identifier,'body':body,
            'open_question_options':[{'id':i,'body':text} for i,text in enumerate(choices)]}


@pytest.fixture
def candidate(tmp_path,monkeypatch):
    (tmp_path/'external').mkdir();(tmp_path/'jobs.json').write_text('{}')
    facts={'name.full':'Fictional Candidate','email':'candidate@example.invalid','phone':'+558100000000',
           'experience.years':3,'location.country':'BR'}
    (tmp_path/'confirmed_facts.json').write_text(json.dumps({'facts':[{'id':k,'value':v,'source':'user'} for k,v in facts.items()]}))
    resume=tmp_path/'resume.pdf';resume.write_bytes(b'%PDF fictional resume')
    def forbidden(*args,**kwargs):pytest.fail('Recruitee must not open a browser')
    monkeypatch.setattr('core.external_apply.application_browser',forbidden)
    monkeypatch.setattr('core.external_batch.persistent_browser',forbidden)
    monkeypatch.setattr('urllib.request.urlopen',forbidden)
    return tmp_path,resume


class Response(io.BytesIO):
    def __init__(self,value,status=200):
        super().__init__(value if isinstance(value,bytes) else json.dumps(value).encode());self.status=status


def api(monkeypatch,rows=None,status=201,result=None,failure=None,get_status=None):
    rows=rows if rows is not None else [offer()];calls=[]
    def fetch(req,**kwargs):
        calls.append(req)
        if req.get_method()=='GET':
            assert req.full_url==BASE and kwargs['timeout']==40
            if get_status:raise HTTPError(req.full_url,get_status,'fictional',{},io.BytesIO(b'{}'))
            return Response({'offers':rows})
        assert req.full_url==BASE+'backend-engineer/candidates?async=true' and kwargs['timeout']==60
        if failure:raise failure
        value=result if result is not None else {'candidate':{'id':456}}
        if status>=400:raise HTTPError(req.full_url,status,'fictional',{},Response(value))
        return Response(value,status)
    monkeypatch.setattr('urllib.request.urlopen',fetch)
    return calls


def job():return recruitee_job(offer(),'fictional-cloud')


def payload(req):
    message=BytesParser(policy=default).parsebytes(('Content-Type: '+req.get_header('Content-type')+'\r\n\r\n').encode()+req.data)
    return {unquote(p.get_param('name',header='content-disposition')):p.get_payload(decode=True) for p in message.iter_parts()}


def approve(data,raw,value):
    book=AnswerBook(data);field=next(f for f in schema_fields(offer(open_questions=[raw])) if f.get('question_id')==raw['id'])
    answer=book.resolve(field,dict(job(),eligibility_country='BR',form_language='en'))
    book.approve(answer['pending'],value)


@pytest.mark.parametrize('mode,expected,state',[(None,'remote','eligible'),('hybrid','hybrid','discarded'),('on_site','on-site','discarded')])
def test_collection_mapping(monkeypatch,mode,expected,state):
    raw=offer(**({mode:True} if mode else {}));calls=api(monkeypatch,[raw,offer(id=124,status='draft')])
    rows=refresh_board('recruitee','fictional-cloud');assert len(rows)==1 and len(calls)==1
    row=rows[0]
    assert row['job_key']=='recruitee:fictional-cloud:123' and row['externalId']=='123'
    assert row['url']==URL and canonical_ats(URL+'/?source=example')==URL
    assert row['description']=='Build services.\n\n3 years of experience'
    assert expected in row['location'] and row['workplaceType']==expected
    assert row['recruitee_slug']=='backend-engineer' and row['recruitee_questions']['open_questions']==[]
    assert qualify(row,{'experience.years':{'value':3}})['state']==state


@pytest.mark.parametrize('value',['recruitee:fictional-cloud',URL,{'platform':'recruitee','board':'fictional-cloud'}])
def test_board_specs(value):assert board_spec(value)=={'platform':'recruitee','board':'fictional-cloud'}


@pytest.mark.parametrize('value',['recruitee:../bad','recruitee:bad_name','https://fictional-cloud.recruitee.com.evil.invalid/o/slug','http://fictional-cloud.recruitee.com/o/slug','https://user@fictional-cloud.recruitee.com/o/slug','https://fictional-cloud.recruitee.com:444/o/slug'])
def test_invalid_boards(value):
    with pytest.raises(SafetyError):board_spec(value)


def test_registry_discovery_closure_and_queue(candidate,monkeypatch):
    data,_=candidate;rows=[offer()];calls=api(monkeypatch,rows)
    (data/'external/board-seeds.txt').write_text(URL)
    assert board_candidates(data)[0][0]['platform']=='recruitee'
    assert suggest_boards(data)['validated_boards']==1
    assert add_boards(data,['recruitee:fictional-cloud'])['added'] and len(calls)==1
    assert discover(data)['new_jobs']==1
    assert queue(data)[0]['platform']=='recruitee'
    assert refresh_imported(data)['boards'][0]['active_imported']==1
    rows[0]['status']='unpublished'
    assert discover(data)['boards'][0]['closed_imported']==1
    assert triage(data)['states']=={'closed':1} and queue(data)==[]


async def test_review_payload_and_metrics(candidate,monkeypatch):
    data,resume=candidate;raw=question(body='LinkedIn Profile',required=False)
    calls=api(monkeypatch,[offer(open_questions=[raw])])
    result=await execute_external(job(),data,resume)
    assert result['state']=='reviewed' and len(calls)==1
    review=json.loads((Path(result['evidence_dir'])/'external-review.json').read_text())['snapshot']
    fields={v['field']:v['value'] for v in review['payload']['values']}
    assert fields=={'candidate[name]':'Fictional Candidate','candidate[email]':'candidate@example.invalid','candidate[phone]':'+558100000000'}
    assert review['resume_hash']==hashlib.sha256(resume.read_bytes()).hexdigest()
    assert review['payload']['files'][0]['filename']=='resume.pdf'
    with AttemptStore(data).db() as db:
        metrics=json.loads(db.execute("SELECT payload FROM events WHERE kind='answer_metrics'").fetchone()[0])
        assert metrics['rule']==3 and metrics['pending']==1
        logs=' '.join(r[0] for r in db.execute('SELECT payload FROM events'))
        assert 'candidate@example.invalid' not in logs and '+558100000000' not in logs


async def test_required_question_pending_then_approved(candidate,monkeypatch):
    data,resume=candidate;raw=question();calls=api(monkeypatch,[offer(open_questions=[raw])])
    pending=await execute_external(job(),data,resume,'submit')
    assert pending['state']=='pending' and len(calls)==1
    questions=json.loads((Path(pending['evidence_dir'])/'pending-answers.json').read_text())
    row=AnswerBook(data).get(questions[0]['answer_id']);assert row['jobs']==[URL] and row['question']=='Nationality'
    AnswerBook(data).approve(row['id'],'Fictional nationality')
    reviewed=await execute_external(job(),data,resume)
    assert reviewed['state']=='reviewed'
    review=json.loads((Path(reviewed['evidence_dir'])/'external-review.json').read_text())
    assert any(p['value']=='Fictional nationality' for p in review['snapshot']['payload']['values'])


async def test_submit_records_intent_before_post_and_confirms(candidate,monkeypatch):
    data,resume=candidate;calls=api(monkeypatch)
    original=__import__('urllib.request',fromlist=['urlopen']).urlopen
    def checked(req,**kwargs):
        if req.get_method()=='POST':
            store=AttemptStore(data);attempt=store.list()[0]
            assert attempt['state']=='submitted' and len(store.submissions(attempt['id']))==1
            assert (Path(attempt['evidence_dir'])/'external-review.json').exists()
        return original(req,**kwargs)
    monkeypatch.setattr('urllib.request.urlopen',checked)
    result=await execute_external(job(),data,resume,'submit')
    assert result['state']=='confirmed' and len(calls)==2
    store=AttemptStore(data);assert store.submissions(result['id'])[0]['http_status']==201
    with store.db() as db:
        assert json.loads(db.execute("SELECT payload FROM events WHERE kind='recruitee_candidate'").fetchone()[0])=={'candidate_id':456}
    assert export_confirmed_setup(data)['appended']==1
    with pytest.raises(SafetyError):await execute_external(job(),data,resume,'submit')
    assert len(calls)==2


def export_confirmed_setup(data):
    (data/'external/catalog.json').write_text(json.dumps({'jobs':[job()],'boards':[]}))
    return export_confirmed(data,data/'reports/fictional.csv')


@pytest.mark.parametrize('status,state',[ (400,'blocked'),(422,'blocked'),(401,'blocked'),(403,'blocked'),(429,'uncertain'),(500,'uncertain'),(503,'uncertain')])
async def test_http_outcomes_never_retry(candidate,monkeypatch,status,state):
    data,resume=candidate;calls=api(monkeypatch,status=status,result={'error':'fictional rejection'})
    result=await execute_external(job(),data,resume,'submit')
    assert result['state']==state and len(calls)==2
    assert platform_hold(data,'recruitee')==(status in (401,403))
    if status in (400,422):assert 'fictional rejection' in (Path(result['evidence_dir'])/'recruitee-error.json').read_text()
    with pytest.raises(SafetyError):await execute_external(job(),data,resume,'submit')
    assert len(calls)==2


@pytest.mark.parametrize('failure',[TimeoutError('fictional timeout'),URLError('fictional connection failure')])
async def test_transport_uncertain_never_retry(candidate,monkeypatch,failure):
    data,resume=candidate;calls=api(monkeypatch,failure=failure)
    result=await execute_external(job(),data,resume,'submit');assert result['state']=='uncertain'
    with pytest.raises(SafetyError):await execute_external(job(),data,resume,'submit')
    assert len(calls)==2


@pytest.mark.parametrize('result',[b'not json',{}, {'candidate':{}},{'candidate':{'id':None}}])
async def test_unparseable_success_is_uncertain(candidate,monkeypatch,result):
    data,resume=candidate;api(monkeypatch,result=result)
    assert (await execute_external(job(),data,resume,'submit'))['state']=='uncertain'


async def test_multipart_choice_multi_flags_and_files(candidate,monkeypatch):
    data,resume=candidate
    questions=[question(1,'single_choice',body='Fictional preference',choices=['Option A','Option B']),
        question(2,'multi_choice',body='Fictional selections',choices=['First','Second']),
        question(3,'boolean',body='Fictional boolean'),question(4,'legal',body='Fictional consent'),
        question(5,'file',body='Fictional attachment'),question(6,'date',body='Fictional date'),
        question(7,'salary',body='Fictional amount')]
    attachment=data/'attachment.txt';attachment.write_text('fictional attachment')
    for raw,value in zip(questions,['Option B',['First','Second'],False,True,str(attachment),'2026-11-04',123]):approve(data,raw,value)
    calls=api(monkeypatch,[offer(open_questions=questions)])
    result=await execute_external(job(),data,resume,'submit');assert result['state']=='confirmed'
    req=calls[-1];parts=payload(req);prefix='candidate[open_question_answers_attributes]'
    assert b'name="candidate%5Bname%5D"' in req.data and b'name="candidate[' not in req.data
    assert parts[prefix+'[0][content]']==b'Option B'
    assert parts[prefix+'[1][multi_content][0]']==b'First' and parts[prefix+'[1][multi_content][1]']==b'Second'
    assert parts[prefix+'[2][flag]']==b'false' and parts[prefix+'[3][flag]']==b'true'
    assert parts[prefix+'[4][file]']==attachment.read_bytes() and parts['candidate[cv]']==resume.read_bytes()
    assert parts[prefix+'[5][content]']==b'2026-11-04' and parts[prefix+'[6][content]']==b'123'


@pytest.mark.parametrize('rows',[[],[offer(status='draft')]])
async def test_closed_posting_blocks_without_post(candidate,monkeypatch,rows):
    data,resume=candidate;calls=api(monkeypatch,rows)
    result=await execute_external(job(),data,resume,'submit')
    assert result['state']=='blocked' and 'closed or unpublished' in result['message'] and len(calls)==1


@pytest.mark.parametrize('status',[401,403])
async def test_fresh_fetch_token_hold(candidate,monkeypatch,status):
    data,resume=candidate;calls=api(monkeypatch,get_status=status)
    result=await execute_external(job(),data,resume,'submit')
    assert result['state']=='blocked' and platform_hold(data,'recruitee') and len(calls)==1


async def test_offer_options_off_and_required_cover_letter(candidate,monkeypatch):
    data,resume=candidate;calls=api(monkeypatch,[offer(options_cv='off',options_phone='off',options_cover_letter='required')])
    result=await execute_external(job(),data,resume,'submit');assert result['state']=='pending'
    raw=json.loads((Path(result['evidence_dir'])/'pending-answers.json').read_text())
    assert raw[0]['question']=='Cover letter' and len(calls)==1
    AnswerBook(data).approve(raw[0]['answer_id'],'Fictional letter')
    result=await execute_external(job(),data,resume,'submit');assert result['state']=='confirmed'
    assert set(payload(calls[-1]))=={'candidate[name]','candidate[email]','candidate[cover_letter]'}


@pytest.mark.parametrize('change',['facts','resume','approval'])
async def test_review_changes_block_before_intent(candidate,monkeypatch,change):
    data,resume=candidate;raw=question();approve(data,raw,'Fictional nationality');calls=api(monkeypatch,[offer(open_questions=[raw])])
    event=AttemptStore.event
    def mutate(store,aid,kind,value):
        event(store,aid,kind,value)
        if kind!='external_review':return
        if change=='resume':resume.write_bytes(b'%PDF changed')
        elif change=='facts':(data/'confirmed_facts.json').write_text('{"facts":[]}')
        else:
            book=AnswerBook(data);book.approve(book.list()[0]['id'],'Changed nationality')
    monkeypatch.setattr(AttemptStore,'event',mutate)
    result=await execute_external(job(),data,resume,'submit')
    assert result['state']=='pending' and len(calls)==1 and not AttemptStore(data).submissions(result['id'])


async def test_daily_limit_and_contact_hold(candidate,monkeypatch):
    data,resume=candidate;calls=api(monkeypatch)
    (data/'pending-contact-verification.json').write_text('{"active":true}')
    with pytest.raises(SafetyError):await execute_external(job(),data,resume,'submit')
    assert not calls
    (data/'pending-contact-verification.json').unlink()
    (data/'settings.json').write_text('{"submission_policy":{"daily_limit":1}}')
    store=AttemptStore(data);aid=store.begin('https://fictional-cloud.recruitee.com/o/other');store.intent(aid,'old','https://example.invalid')
    store.transition(aid,'uncertain')
    result=await execute_external(job(),data,resume,'submit')
    assert result['state']=='pending' and len(calls)==1 and not store.submissions(result['id'])


async def test_batch_runs_http_without_browser_and_records_batch(candidate,monkeypatch):
    from core.external_batch import BatchStore,run_batch
    data,resume=candidate;calls=api(monkeypatch)
    discover(data,['recruitee:fictional-cloud'])
    monkeypatch.setattr('core.config.load_settings',lambda:{'resume_path':str(resume)})
    store=BatchStore(data);bid=store.create(1,'submit',platform='recruitee')
    result=await run_batch(data,bid)
    assert result['new_confirmations']==1 and result['native_send_intents']==1
    assert result['items'][0]['platform']=='recruitee' and result['job_outcomes']=={'confirmed':1}
    assert len([req for req in calls if req.get_method()=='POST'])==1
    assert (await run_batch(data,bid))['native_send_intents']==0


async def test_run_platform_filter_and_export(candidate,monkeypatch):
    data,resume=candidate;calls=api(monkeypatch)
    discover(data,['recruitee:fictional-cloud'])
    monkeypatch.setattr('core.config.load_settings',lambda:{'resume_path':str(resume)})
    monkeypatch.setattr('core.external_service.update_projection',lambda j,r:update_projection(j,r,data))
    body=SimpleNamespace(execution='submit',limit=1,platform='recruitee',job_url=None,job_urls=None)
    status={};await run_external(data,body,status)
    result=json.loads(Path(status['report_path']).read_text())
    assert result['outcomes']=={'confirmed':1} and result['submit_intents']==1
    assert len([req for req in calls if req.get_method()=='POST'])==1
    assert export_confirmed(data,data/'reports/fictional.csv')['appended']==1
    assert not queue(data)


async def test_choice_ids_are_rejected_and_optional_pending_is_omitted(candidate,monkeypatch):
    data,resume=candidate;raw=question(kind='single_choice',choices=['Option A','Option B'])
    book=AnswerBook(data);field=next(f for f in schema_fields(offer(open_questions=[raw])) if f.get('question_id'))
    answer=book.resolve(field,dict(job(),eligibility_country='BR',form_language='en'))
    with pytest.raises(SafetyError):book.approve(answer['pending'],'0')
    optional=question(2,'multi_choice',False,'Fictional optional selections',['First','Second'])
    approve(data,raw,'Option A');calls=api(monkeypatch,[offer(open_questions=[raw,optional])])
    result=await execute_external(job(),data,resume,'submit');assert result['state']=='confirmed'
    parts=payload(calls[-1]);assert not any('[1]' in name for name in parts)


async def test_blocked_validation_is_removed_from_queue(candidate,monkeypatch):
    data,resume=candidate;api(monkeypatch,status=422)
    discover(data,['recruitee:fictional-cloud']);assert queue(data)
    assert (await execute_external(job(),data,resume,'submit'))['state']=='blocked'
    assert not queue(data)
