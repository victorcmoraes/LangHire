import json
import asyncio
import re
import pytest
from playwright.async_api import async_playwright
from core.application_safety import AttemptStore,SafetyError
from core.greenhouse_apply import GreenhouseAdapter
from core.submission_monitor import SubmissionMonitor,is_submission,confirmation_matches_job

URL='https://job-boards.greenhouse.io/fixture/jobs/123'
SCHEMA={'id':123,'title':'Backend Engineer','questions':[
    {'label':label,'required':True,'fields':[{'name':name,'type':'input_text'}]}
    for name,label in [('first_name','First Name'),('last_name','Last Name'),('email','Email'),('linkedin','LinkedIn Profile')]
]+[
    {'label':'English Level','required':True,'fields':[{'name':'english','type':'multi_value_single_select','values':[
        {'label':'Full professional proficiency','value':2},{'label':'Native or bilingual proficiency','value':3}]}]},
    {'label':'Resume/CV','required':False,'fields':[{'name':'resume','type':'input_file'},{'name':'resume_text','type':'textarea'}]},
    {'label':'Cover letter','required':False,'fields':[{'name':'cover_letter','type':'input_file'}]}
]}

@pytest.fixture
async def hosted_gh(tmp_path):
    facts=[{'id':k,'value':v,'source':'user'} for k,v in {'name.first':'Test','name.last':'Candidate','email':'test@example.invalid','url.linkedin':'https://example.invalid/profile','level.english':'Proficient'}.items()]
    (tmp_path/'confirmed_facts.json').write_text(json.dumps({'facts':facts}))
    pdf=tmp_path/'resume.pdf';pdf.write_bytes(b'%PDF-fixture')
    html='''<meta charset="utf-8"><h1>Backend Engineer</h1><form id="application-form">
    <input id="first_name" required><input id="last_name" required><input id="email" required><input id="linkedin" required>
    <div class="select__container"><label for="english">English Level</label><div class="select__single-value"></div>
    <input id="english" role="combobox" aria-required="true" onclick="document.getElementById('choices').hidden=false">
    <input class="select__requiredInput" required style="opacity:0;width:1px;height:1px" value="">
    <div id="choices" hidden><div role="option" onclick="choose(this,'2')">Full professional proficiency</div><div role="option" onclick="choose(this,'3')">Native or bilingual proficiency</div></div></div>
    <div role="group" aria-labelledby="upload-label-resume"><div id="upload-label-resume">Resume/CV</div><input type="file" id="resume" onchange="if(!window.uploaderReady)throw Error('Uploader not initialized');document.getElementById('filename').textContent=this.files[0].name;this.remove()"><span id="filename"></span></div>
    <input type="file" id="cover_letter"><button>Submit application</button></form>
    <script>
    fetch('/uncacheable_attributes/presigned_fields').then(r=>r.json()).then(()=>window.uploaderReady=true);
    function choose(e,value){const container=e.closest('.select__container');container.querySelector('.select__single-value').textContent=e.textContent;container.querySelector('.select__requiredInput').value=value;container.querySelector('#english').value='';document.getElementById('choices').hidden=true;}
    document.querySelector('form').onsubmit=async e=>{e.preventDefault();const code=[...document.querySelectorAll('[id^="security-input-"]')].map(e=>e.value).join('');const r=await fetch('https://boards.greenhouse.io/fixture/jobs/123',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_application:{first_name:document.getElementById('first_name').value,email:document.getElementById('email').value},...(code?{security_code:code}:{})})});const result=await r.json();if(r.status===428&&result.code==='captcha-failed'){const field=document.createElement('div');field.className='email-verification';field.textContent='A verification code was sent. Enter the 8-character code.';for(let i=0;i<8;i++){const input=document.createElement('input');input.id='security-input-'+i;input.setAttribute('aria-required','true');input.maxLength=1;field.append(input);}e.target.append(field);}else{if(r.ok)history.replaceState(null,'','/fixture/jobs/123/confirmation');document.body.innerText=r.ok?'Thank you for applying':'There was an error verifying your application';}};
    </script>'''
    received=[];state={'status':200,'email_code_required':False}
    async with async_playwright() as p:
        browser=await p.chromium.launch(channel='chrome',headless=True);ctx=await browser.new_context()
        async def serve(route):
            headers={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'Content-Type','Access-Control-Allow-Methods':'POST,GET,OPTIONS'}
            if route.request.url.endswith('/uncacheable_attributes/presigned_fields'):
                await route.fulfill(content_type='application/json',body='{"resume":{"fields":{}}}')
            elif route.request.method=='POST':
                payload=json.loads(route.request.post_data);received.append(payload)
                required=state['email_code_required'] and not payload.get('security_code')
                body='{"code":"captcha-failed","security_code_recipient":"test@example.invalid"}' if required else '{"ok":true}'
                await route.fulfill(status=428 if required else state['status'],content_type='application/json',headers=headers,body=body)
            else:await route.fulfill(content_type='text/html',headers=headers,body=html)
        await ctx.route('**/*',serve);page=await ctx.new_page()
        store=AttemptStore(tmp_path);aid=store.begin(URL);monitor=SubmissionMonitor(store,aid);await monitor.attach_context(ctx)
        adapter=GreenhouseAdapter(page,{'url':URL,'title':'Backend Engineer','company':'Fixture','eligibility_country':'BR','form_language':'en'},tmp_path,aid,monitor)
        async def load():adapter.schema=json.loads(json.dumps(SCHEMA))
        adapter.load_schema=load
        yield adapter,pdf,received,store,aid,page,state
        await monitor.close();await browser.close()

async def test_greenhouse_schema_controls_upload_and_known_language_without_send(hosted_gh):
    adapter,pdf,received,store,aid,page,_=hosted_gh
    await adapter.run(pdf,'review')
    assert store.get(aid)['state']=='reviewed' and not received
    assert await page.locator('#cover_letter').evaluate('e=>e.files.length')==0
    assert await page.locator('.select__single-value').inner_text()=='Full professional proficiency'
    await page.locator('#last_name').fill('Other')
    assert not (await adapter.review())['ready']

async def test_greenhouse_native_final_shape_confirms_once(hosted_gh):
    adapter,pdf,received,store,aid,page,_=hosted_gh
    await adapter.run(pdf,'submit')
    assert store.get(aid)['state']=='confirmed' and len(received)==1 and len(store.submissions(aid))==1
    with pytest.raises(SafetyError):store.begin(URL)

async def test_greenhouse_unknown_required_field_never_sends(hosted_gh):
    adapter,pdf,received,store,aid,page,_=hosted_gh
    load=adapter.load_schema
    async def with_unknown():
        await load();adapter.schema['questions'].append({'label':'Nationality','required':True,'fields':[{'name':'nationality','type':'input_text'}]})
    adapter.load_schema=with_unknown
    with pytest.raises(SafetyError,match='Unresolved Greenhouse questions'):await adapter.run(pdf,'submit')
    assert not received and not store.submissions(aid)


async def test_checkbox_group_uses_native_controls_and_detects_changed_approval(hosted_gh):
    adapter,pdf,received,store,aid,page,_=hosted_gh
    label='Acknowledgement of the reviewed notice'
    old={'label':label,'type':'checkbox-group','required':True,'options':[{'text':'I acknowledge','value':'old'}]}
    pending=adapter.book.resolve(old,adapter.job)['pending'];adapter.book.approve(pending,['old'])
    load=adapter.load_schema
    async def with_group():
        await load();adapter.schema['questions'].append({'label':label,'required':True,'fields':[
            {'name':'ack[]','type':'multi_value_multi_select','values':[{'label':'I acknowledge','value':'new'}]}]})
    adapter.load_schema=with_group
    async def insert_group():
        await page.locator('#application-form').evaluate("f=>{const group=document.createElement('fieldset');group.id='ack[]';const e=document.createElement('input');e.type='checkbox';e.name='ack[]';e.value='new';group.append(e);f.append(group)}")
    fill=adapter.fill
    async def fill_group():await insert_group();await fill()
    adapter.fill=fill_group
    await adapter.run(pdf,'review')
    assert not received and await page.locator('[name="ack[]"]').is_checked()
    used=next(a for a in adapter.used if a['question']==label)
    assert used['value']==['new'] and used['approval_value']==['old']
    assert (await adapter.review())['ready']
    with store.db() as db:db.execute("UPDATE easy_answers SET state='pending' WHERE id=?",(pending,))
    assert not (await adapter.review())['ready']


async def test_auxiliary_phone_location_and_demographic_sections_are_filled_from_native_options(hosted_gh):
    from core.easy_answers import AnswerBook
    adapter,pdf,received,store,aid,page,_=hosted_gh
    path=adapter.data/'confirmed_facts.json';facts=json.loads(path.read_text())
    facts['facts'] += [{'id':k,'value':v,'source':'user'} for k,v in {
        'phone':'+5581999999999','phone.countryCode':'+55','location.city':'Olinda','location.state':'Pernambuco','location.country':'Brazil'}.items()]
    path.write_text(json.dumps(facts));adapter.book=AnswerBook(adapter.data)
    load=adapter.load_schema
    async def with_auxiliary():
        await load();adapter.schema['questions'].append({'label':'Phone','required':True,'fields':[{'name':'phone','type':'input_text'}]})
        adapter.schema['location_questions']=[{'label':'Location','required':True,'fields':[{'name':'location','type':'input_text'},{'name':'latitude','type':'input_hidden'}]}]
        adapter.schema['demographic_questions']={'questions':[
            {'id':1,'label':'Race','required':True,'type':'multi_value_single_select','answer_options':[
                {'id':11,'label':'White','decline_to_answer':False},{'id':12,'label':"I don't wish to answer",'decline_to_answer':True}]},
            {'id':2,'label':'Disability type','required':True,'type':'multi_value_multi_select','answer_options':[
                {'id':21,'label':'Visual impairment','decline_to_answer':False},{'id':22,'label':"I don't wish to answer",'decline_to_answer':True}]}]}
        adapter.schema['data_compliance']=[{'demographic_data_consent_applies':True}]
    adapter.load_schema=with_auxiliary
    extra='''<input id="phone" type="tel" required>
    <div class="select__container"><div class="select__single-value"></div><input id="country" role="combobox" aria-required="true" onclick="this.parentElement.querySelector('.options').hidden=false">
    <input class="select__requiredInput" required style="opacity:0;width:1px;height:1px"><div class="options" hidden><div role="option">Brazil +55</div></div></div>
    <div class="select__container"><div class="select__single-value"></div><input id="candidate-location" role="combobox" aria-required="true" onclick="this.parentElement.querySelector('.options').hidden=false">
    <div class="options" hidden><div role="option">Olinda, Hawaii, United States</div><div role="option">Olinda, Pernambuco, Brazil</div></div></div>
    <div class="select__container"><div class="select__single-value"></div><input id="1" role="combobox" aria-required="true" onclick="this.parentElement.querySelector('.options').hidden=false">
    <div class="options" hidden><div role="option">White</div><div role="option">I don't wish to answer</div></div></div>
    <div class="select__container" data-multi><input id="2" role="combobox" aria-required="true" onclick="this.parentElement.querySelector('.options').hidden=false">
    <div class="options" hidden><div role="option">Visual impairment</div><div role="option">I don't wish to answer</div></div></div>
    <label for="demographic-consent">By checking this box, I consent to Fixture collecting, storing, and processing my responses to the demographic data surveys above.</label>
    <input id="demographic-consent" type="checkbox" required>'''
    fill=adapter.fill
    async def fill_auxiliary():
        await page.locator('#application-form').evaluate('(f,html)=>f.insertAdjacentHTML("beforeend",html)',extra)
        await page.evaluate('''()=>document.querySelectorAll('.options [role=option]').forEach(e=>e.onclick=()=>{
            const container=e.closest('.select__container');if(container.hasAttribute('data-multi')){
                const chip=document.createElement('span');chip.className='select__multi-value__label';chip.textContent=e.textContent;container.append(chip);
            }else container.querySelector('.select__single-value').textContent=e.textContent;
            e.parentElement.hidden=true;const helper=container.querySelector('.select__requiredInput');if(helper)helper.value='accepted';
        })''')
        await fill()
    adapter.fill=fill_auxiliary
    await adapter.run(pdf,'review')
    assert not received and store.get(aid)['state']=='reviewed'
    assert await page.locator('#phone').input_value()=='81999999999'
    used={a['question']:a['value'] for a in adapter.used}
    assert used['Phone country code']=='+55' and used['Location (City)']=='Olinda, Pernambuco, Brazil'
    assert used['Race']=='12' and used['Disability type']==['22']
    assert await page.locator('#demographic-consent').is_checked()
    assert (await adapter.review())['ready']
    await page.locator('#candidate-location').evaluate("e=>e.parentElement.querySelector('.select__single-value').textContent='Olinda, Hawaii, United States'")
    assert not (await adapter.review())['ready']


async def test_preflight_includes_auxiliary_questions_before_opening_browser(tmp_path,monkeypatch):
    from core.greenhouse_apply import preflight_greenhouse
    schema={'questions':[],'location_questions':[{'label':'Location','required':True,'fields':[{'name':'location','type':'input_text'}]}],
            'demographic_questions':{'questions':[{'id':1,'label':'Race','type':'multi_value_single_select','required':True,
                'answer_options':[{'id':2,'label':"I don't wish to answer",'decline_to_answer':True}]}]}}
    async def fetch(_):return schema
    monkeypatch.setattr('core.greenhouse_apply.fetch_schema',fetch)
    result=await preflight_greenhouse(tmp_path,{'url':URL,'eligibility_country':'BR'})
    assert not result['ready_for_browser']
    assert [p['question'] for p in result['pending']]==['Location (City)']
    assert result['answer_metrics']['rule']==1

async def test_greenhouse_rejection_is_not_confirmed(hosted_gh):
    adapter,pdf,received,store,aid,page,state=hosted_gh
    state['status']=400
    await adapter.run(pdf,'submit')
    assert store.get(aid)['state']=='uncertain' and len(received)==1

def test_greenhouse_final_format_and_confirmation_are_job_bound():
    body=json.dumps({'job_application':{'first_name':'Test'},'fingerprint':'fixture'})
    assert is_submission('https://boards.greenhouse.io/fixture/jobs/123','POST',body)
    assert not is_submission('https://boards.greenhouse.io/fixture/jobs/123','POST','{}')
    assert confirmation_matches_job(URL,'https://job-boards.greenhouse.io/fixture/jobs/123/confirmation')
    assert not confirmation_matches_job(URL,'https://job-boards.greenhouse.io/fixture/jobs/456/confirmation')
    assert not confirmation_matches_job(URL,'https://boards.greenhouse.io:8443/fixture/jobs/123/confirmation')
    # Some real descriptions list "Application submitted (you are here)" as
    # their interview process. The posting itself is never a GH receipt page.
    assert not confirmation_matches_job(URL,URL)


async def test_email_code_waits_for_human_then_creates_a_second_counted_attempt(hosted_gh):
    adapter,pdf,received,store,parent,page,state=hosted_gh
    state['email_code_required']=True
    running=asyncio.create_task(adapter.run(pdf,'submit'))
    for _ in range(250):
        if store.get(parent)['state']=='awaiting_human':break
        await asyncio.sleep(.02)
    assert store.get(parent)['state']=='awaiting_human' and not running.done()
    assert len(received)==1 and len(store.submissions(parent))==1
    for i,character in enumerate('ABCDEFGH'):
        await page.locator('#security-input-'+str(i)).fill(character)
    store.acknowledge(parent)
    await asyncio.wait_for(running,10)
    child=adapter.monitor.attempt_id
    assert child!=parent and store.get(parent)['state']=='blocked'
    assert store.get(child)['state']=='confirmed'
    assert len(received)==2 and len(store.submissions(child))==1
    assert store.submissions(parent)[0]['http_status']==428
    assert store.submissions(child)[0]['http_status']==200
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0]==2
        assert 'ABCDEFGH' not in ' '.join(row[0] for row in db.execute('SELECT payload FROM events'))
    with pytest.raises(SafetyError):store.begin(URL)
