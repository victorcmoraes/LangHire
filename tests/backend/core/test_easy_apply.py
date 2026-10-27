import json
from pathlib import Path
import pytest
from playwright.async_api import async_playwright
from core.application_safety import AttemptStore, SafetyError
from core.easy_apply import EasyExecutor, EasyStore, canonical
from core.submission_monitor import SubmissionMonitor, linkedin_request_kind


@pytest.fixture
async def portal(tmp_path,request):
    (tmp_path/'confirmed_facts.json').write_text(json.dumps({'facts':[
        {'id':'name.full','value':'Test Candidate','source':'user'},
        {'id':'email','value':'test@example.invalid','source':'user'}]}))
    resume=tmp_path/'resume.pdf';resume.write_bytes(b'%PDF-test')
    html='''<meta charset="utf-8"><h1>Backend Engineer</h1><button onclick="document.querySelector('[role=dialog]').hidden=false">Easy Apply</button>
    <div role="dialog" class="jobs-easy-apply-modal" hidden></div><script>
    let step=0;let values={};const modal=document.querySelector('[role=dialog]');
    function render(){modal.innerHTML=step===0?'<label>Full name<input id="name" required></label><label>Email<input id="email" required></label><button onclick="next()">Next</button>':
    step===1?'<label>Resume<input type="file" hidden required id="resume"></label><button onclick="next()">Review</button>':
    '<h2>Review your application</h2><p>'+values.name+' '+values.email+' resume.pdf</p><button onclick="send()">Submit application</button>';}
    function next(){for(const e of modal.querySelectorAll('input'))values[e.id]=e.value;step++;render();}
    async function send(){const r=await fetch('/voyager/api/voyagerJobsDashOnsiteApplyApplications?action=submit',{method:'POST',body:'{}'});if(r.ok)document.body.innerHTML='Application submitted';}
    render();</script>'''
    if getattr(request,'param','en')=='pt':
        for english,portuguese in [('Easy Apply','Candidatura simplificada'),('Full name','Nome completo'),('>Next<','>Próximo<'),('>Review<','>Revisar<'),('Submit application','Enviar candidatura')]:
            html=html.replace(english,portuguese)
    if getattr(request,'param','en')=='modern':
        html=html.replace('<h1>Backend Engineer</h1>','<title>Backend Engineer | Fixture | LinkedIn</title><main><h2>About the job</h2><p>Backend Engineer</p></main>')
        html=html.replace('<button onclick="document.querySelector','<button aria-label="Use Easy Apply for this job" onclick="document.querySelector',1)
    if getattr(request,'param','en')=='delayed':
        html=html.replace('step++;render();','step++;modal.insertAdjacentHTML("beforeend","<div role=alert>Upload completed</div>");setTimeout(render,100);')
    if getattr(request,'param','en')=='profile':
        html=html.replace('render();</script>', '''const basicRender=render;render=function(){if(step===2){modal.innerHTML='<h2>Experience</h2><p>Confirmed fixture history</p><button onclick="next()">Next</button>';}else basicRender();};render();</script>''')
    received=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(channel='chrome',headless=True)
        ctx=await browser.new_context()
        async def route(r):
            if r.request.method=='POST':received.append(r.request.url);await r.fulfill(content_type='application/json',body='{"success":true}')
            else:await r.fulfill(content_type='text/html',body=html)
        await ctx.route('**/*',route)
        page=await ctx.new_page()
        yield page,ctx,tmp_path,resume,received
        await browser.close()


async def run(portal,jobid='123',execution='review',review_id=None):
    page,ctx,data,resume,received=portal
    job={'url':'https://www.linkedin.com/jobs/view/'+jobid+'/','title':'Backend Engineer','company':'Fixture '+jobid}
    store=AttemptStore(data);aid=store.begin(job['url']);monitor=SubmissionMonitor(store,aid)
    await monitor.attach_context(ctx)
    executor=EasyExecutor(page,data,job,aid,monitor)
    try: await executor.run(resume,execution,review_id)
    finally: await monitor.close()
    return aid,store.get(aid)


async def test_same_executor_reviews_three_jobs_without_sending_and_submits_once(portal):
    ids=[]
    for jid in ('123','456','789'):
        aid,result=await run(portal,jid);ids.append(aid);assert result['state']=='reviewed'
    assert portal[-1]==[]
    aid,result=await run(portal,'123','submit',ids[0])
    assert result['state']=='confirmed'
    assert len(portal[-1])==1
    with pytest.raises(SafetyError):await run(portal,'123')


async def test_review_mismatch_blocks_before_any_submission(portal):
    aid,_=await run(portal)
    portal[3].write_bytes(b'%PDF-changed')
    with pytest.raises(SafetyError,match='Review changed'):
        await run(portal,execution='submit',review_id=aid)
    assert not portal[-1]


def test_linkedin_classifier_does_not_treat_drafts_or_reads_as_final_submit():
    base='https://www.linkedin.com/voyager/api/'
    assert linkedin_request_kind(base+'voyagerJobsDashOnsiteApplyApplications?action=submit','POST')=='submit'
    assert linkedin_request_kind(base+'voyagerJobsDashOnsiteApplyApplications','POST')=='unsupported'
    assert linkedin_request_kind(base+'jobs/onsiteApplyDrafts','POST')=='draft'
    assert linkedin_request_kind(base+'graphql?queryId=voyagerJobsDashJobCards.1','POST')=='read'
    assert linkedin_request_kind(base+'graphql?queryId=voyagerJobsDashSubmitApplication.1','POST')=='unsupported'
    with pytest.raises(SafetyError):canonical('https://example.com/jobs/view/123')


@pytest.mark.parametrize('portal',['pt'],indirect=True)
async def test_same_executor_handles_portuguese_labels(portal):
    _,result=await run(portal)
    assert result['state']=='reviewed'
    assert not portal[-1]


@pytest.mark.parametrize('portal',['modern'],indirect=True)
async def test_modern_linkedin_without_h1_or_short_apply_accessible_name(portal):
    _,result=await run(portal)
    assert result['state']=='reviewed'
    assert not portal[-1]


@pytest.mark.parametrize('portal',['delayed'],indirect=True)
async def test_loading_and_success_toast_do_not_look_like_a_failed_or_repeated_step(portal):
    _,result=await run(portal)
    assert result['state']=='reviewed'
    assert not portal[-1]


@pytest.mark.parametrize('portal',['profile'],indirect=True)
async def test_readonly_profile_details_require_one_confirmation_then_reuse(portal):
    from core.easy_answers import AnswerBook
    with pytest.raises(SafetyError,match='profile details require confirmation'):await run(portal)
    data=portal[2];store=AttemptStore(data);store.transition(store.list()[0]['id'],'pending','Awaiting profile confirmation')
    book=AnswerBook(data);q=book.list()[0]
    assert 'Confirmed fixture history' in q['field']['profile_text']
    book.approve(q['id'],True)
    _,result=await run(portal)
    assert result['state']=='reviewed'
    assert not portal[-1]


async def test_unknown_required_question_becomes_reusable_pending_without_send(portal):
    from core.easy_answers import AnswerBook
    page,ctx,data,resume,received=portal
    html='''<h1>Backend Engineer</h1><button onclick="document.querySelector('[role=dialog]').hidden=false">Easy Apply</button>
    <div role="dialog" class="jobs-easy-apply-modal" hidden><label>Years of Kotlin experience<input type="number" required></label><button>Next</button></div>'''
    await ctx.route('**/*',lambda route:route.fulfill(content_type='text/html',body=html))
    with pytest.raises(SafetyError,match='Unresolved fields'):
        await run(portal)
    assert not received
    assert AnswerBook(data).list()[0]['question']=='Years of Kotlin experience'


async def test_global_verification_error_never_produces_ready_review(portal):
    page,ctx,data,resume,received=portal
    html='''<h1>Backend Engineer</h1><button onclick="document.querySelector('[role=dialog]').hidden=false">Easy Apply</button>
    <div role="dialog" class="jobs-easy-apply-modal" hidden><p>There was an error verifying your application</p><button>Submit application</button></div>'''
    await ctx.route('**/*',lambda route:route.fulfill(content_type='text/html',body=html))
    with pytest.raises(SafetyError,match='validation error'):
        await run(portal)
    assert not EasyStore(data).reviews()
    assert not received


async def test_browser_startup_failure_is_pending_and_does_not_leave_an_active_attempt(tmp_path,monkeypatch):
    from contextlib import asynccontextmanager
    from core.easy_apply import execute_easy
    @asynccontextmanager
    async def unavailable(*args,**kwargs):
        raise RuntimeError('Browser startup failed')
        yield
    monkeypatch.setattr('core.easy_apply.persistent_browser',unavailable)
    result=await execute_easy({'url':'https://www.linkedin.com/jobs/view/123/'},tmp_path,tmp_path/'resume.pdf')
    assert result['state']=='pending'
    assert not AttemptStore(tmp_path).submissions(result['id'])
