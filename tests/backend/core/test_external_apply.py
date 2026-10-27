import json
from pathlib import Path
import pytest
from playwright.async_api import async_playwright
from core.external_apply import LeverAdapter,canonical_ats,execute_external
from core.application_safety import AttemptStore,SafetyError
from core.submission_monitor import SubmissionMonitor

URL='https://jobs.lever.co/fixture/00000000-0000-0000-0000-000000000001'


@pytest.fixture
async def hosted(tmp_path):
    (tmp_path/'confirmed_facts.json').write_text(json.dumps({'facts':[
        {'id':'name.full','value':'Test Candidate','source':'user'},
        {'id':'email','value':'test@example.invalid','source':'user'},
        {'id':'phone','value':'+5581999999999','source':'user'}]}))
    pdf=tmp_path/'resume.pdf';pdf.write_bytes(b'%PDF-fixture')
    html='''<meta charset="utf-8"><h2>Backend Engineer</h2><form id="application-form">
    <div class="application-question"><div class="application-label">Resume/CV ✱</div><input id="resume-upload-input" name="resume" type="file" onchange="document.querySelector('[name=resumeStorageId]').value='stored';document.querySelector('#filename').textContent=this.files[0].name.toUpperCase()"><span id="filename"></span><input type="hidden" name="resumeStorageId"></div>
    <div class="application-question"><div class="application-label">Full name ✱</div><input name="name" type="text"></div>
    <div class="application-question"><div class="application-label">Email ✱</div><input name="email" type="email"></div>
    <div class="application-question"><div class="application-label">Phone</div><input name="phone" type="tel"></div>
    <div class="application-question"><div class="application-label">Cover letter</div><input name="cover_letter" type="file"></div>
    <div class="application-question"><div class="application-label">Pronouns (optional)</div><label><input type="checkbox" name="pronouns" value="he">He/him</label><label><input type="checkbox" name="pronouns" value="she">She/her</label></div>
    <button type="button" onclick="send()">Submit application</button></form>
    <script>async function send(){const r=await fetch(location.pathname,{method:'POST',body:JSON.stringify({name:document.querySelector('[name=name]').value,email:document.querySelector('[name=email]').value})});if(r.ok)document.body.innerHTML='Thank you for applying';}</script>'''
    received=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(channel='chrome',headless=True);ctx=await browser.new_context()
        async def serve(route):
            if route.request.method=='POST':received.append(json.loads(route.request.post_data));await route.fulfill(content_type='application/json',body='{"ok":true}')
            else:await route.fulfill(content_type='text/html',body=html)
        await ctx.route('**/*',serve)
        page=await ctx.new_page();store=AttemptStore(tmp_path);aid=store.begin(URL)
        monitor=SubmissionMonitor(store,aid);await monitor.attach_context(ctx)
        adapter=LeverAdapter(page,{'url':URL,'title':'Backend Engineer','company':'Fixture','eligibility_country':'BR','form_language':'en'},tmp_path,aid,monitor)
        yield adapter,pdf,received,store,aid,page
        await monitor.close();await browser.close()


async def test_hosted_lever_reviews_configured_cv_and_does_not_send_or_attach_cover_letter(hosted):
    adapter,pdf,received,store,aid,page=hosted
    await adapter.run(pdf,'review')
    assert store.get(aid)['state']=='reviewed' and not received
    assert await page.locator('[name=cover_letter]').evaluate('e=>e.files.length')==0
    assert await page.locator('[name=pronouns]:checked').count()==0
    assert await page.locator('[name=phone]').input_value()=='+5581999999999'
    assert json.loads((Path(store.get(aid)['evidence_dir'])/'external-review.json').read_text())['ready']


async def test_hosted_lever_submits_once_with_http_and_confirmation(hosted):
    adapter,pdf,received,store,aid,page=hosted
    await adapter.run(pdf,'submit')
    assert store.get(aid)['state']=='confirmed'
    assert received==[{'name':'Test Candidate','email':'test@example.invalid'}]
    assert len(store.submissions(aid))==1
    with pytest.raises(SafetyError):store.begin(URL+'/apply')


async def test_missing_required_fact_cannot_become_a_ready_form(hosted):
    adapter,pdf,received,store,aid,page=hosted
    original=adapter.inspect
    async def unknown():
        fields=await original();fields.append({'label':'Nationality','type':'text','id':'nationality','name':'nationality','required':True,'value':'','options':[],'targets':['unknown']});return fields
    adapter.inspect=unknown
    with pytest.raises(SafetyError,match='Unresolved external questions'):await adapter.run(pdf,'submit')
    assert not received and store.submissions(aid)==[]


def test_hosted_urls_do_not_accept_arbitrary_destinations():
    assert canonical_ats(URL+'/apply')==URL
    for url in ['http://jobs.lever.co/fixture/id','https://attacker.invalid/apply','https://jobs.lever.co/fixture/not-an-id']:
        with pytest.raises(SafetyError):canonical_ats(url)


async def test_verification_pause_blocks_only_affected_platform_sends_before_attempt_or_charge(tmp_path,monkeypatch):
    directory=tmp_path/'external';directory.mkdir()
    (directory/'lever-verification-hold.json').write_text('{"active":true}')
    job={'url':URL,'platform':'lever','title':'Backend Engineer','company':'Fixture'}
    with pytest.raises(SafetyError,match='paused for verification diagnosis'):
        await execute_external(job,tmp_path,tmp_path/'resume.pdf','submit')
    store=AttemptStore(tmp_path)
    assert store.list()==[]
    # Read-only preparations remain possible without releasing the hold.
    def unavailable_browser(_):raise SafetyError('Read-only browser preparation reached')
    monkeypatch.setattr('core.external_apply.persistent_browser',unavailable_browser)
    result=await execute_external(job,tmp_path,tmp_path/'resume.pdf','review')
    assert result['state']=='pending' and 'Read-only browser' in result['message']
    assert store.submissions(result['id'])==[]
    other=dict(job,url='https://job-boards.greenhouse.io/fixture/jobs/123',platform='greenhouse')
    result=await execute_external(other,tmp_path,tmp_path/'resume.pdf','submit')
    assert result['state']=='pending' and 'paused' not in result['message']
    assert store.submissions(result['id'])==[]
