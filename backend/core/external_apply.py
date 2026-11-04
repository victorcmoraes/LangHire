"""Deterministic hosted ATS execution. Unknown facts and controls stay pending."""
import asyncio
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from contextlib import asynccontextmanager

from filelock import FileLock
from .cornerstone import job_parts,job_url
from .application_safety import AttemptStore, SafetyError, ACTIVE, job_key
from .browser_runtime import persistent_browser
from .easy_answers import AnswerBook, normalized, digest, damaged_text
from .submission_monitor import SubmissionMonitor, ERROR, clean_url

FIELDS=r'''root=>{
 const result=[],groups=new Set();
 const visible=e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
 for(const [i,e] of [...root.querySelectorAll('input,select,textarea')].entries()){
  if(e.disabled||['hidden','button','submit'].includes(e.type)||(!visible(e)&&e.type!=='file'))continue;
  if(/captcha/i.test(e.name||''))continue;
  const row=e.closest('.application-question')||e.closest('fieldset')||e.parentElement;
  const associated=[...(e.labels||[])].map(l=>l.innerText).join(' ').trim();
  let label=row.querySelector('.application-label')?.innerText||e.getAttribute('aria-label')||associated||'';
  let type=e.type||'text';if(e.id==='location-input')type='location';
  let targets=[e],options=e.options?[...e.options].map(o=>({text:o.text,value:o.value})):[];
  if(type==='checkbox'&&e.name){
   const choices=[...root.querySelectorAll('input[type=checkbox]')].filter(x=>x.name===e.name);
   if(choices.length>1){if(groups.has('check:'+e.name))continue;groups.add('check:'+e.name);type='checkbox-group';targets=choices;
    options=choices.map(x=>({text:[...(x.labels||[])].map(l=>l.innerText).join(' ').trim()||x.value,value:x.value}));}
  }
  if(type==='radio'){
   if(groups.has(e.name))continue;groups.add(e.name);
   targets=[...root.querySelectorAll('input[type=radio]')].filter(x=>x.name===e.name);
   label=row.querySelector('legend')?.innerText||label;
   options=targets.map(x=>({text:[...(x.labels||[])].map(l=>l.innerText).join(' ').trim()||x.value,value:x.value}));
  }
  targets.forEach((x,j)=>x.setAttribute('data-langhire-external',`${i}-${j}`));
  result.push({label:label.trim(),type,id:e.id,name:e.name,required:e.required||['name','email'].includes(e.name)||e.getAttribute('aria-required')==='true'||/[*✱]/.test(label),
   options,targets:targets.map(x=>x.getAttribute('data-langhire-external')),
   value:type==='file'?[...e.files].map(f=>f.name):type==='checkbox'?e.checked:type==='radio'?(targets.find(x=>x.checked)?.value||''):e.value});
 }
 return result;
}'''


def canonical_ats(url):
    try:cornerstone=job_parts(url)
    except ValueError as error:raise SafetyError(str(error)) from error
    if cornerstone:return job_url(*cornerstone)
    p=urlsplit(url);host=p.hostname or ''
    if p.scheme!='https':raise SafetyError('Hosted ATS must use HTTPS')
    if host=='apply.workable.com' and re.fullmatch(r'/[\w-]+/j/[A-Za-z0-9]+(?:/apply)?/?',p.path):
        return 'https://'+host+p.path.rstrip('/').removesuffix('/apply')
    if host in ('jobs.lever.co','jobs.eu.lever.co') and re.fullmatch(r'/[\w-]+/[0-9a-f-]{36}(?:/apply)?/?',p.path,re.I):
        return 'https://'+host+p.path.rstrip('/').removesuffix('/apply')
    if host in ('job-boards.greenhouse.io','boards.greenhouse.io') and re.fullmatch(r'/[\w-]+/jobs/\d+/?',p.path):
        return 'https://job-boards.greenhouse.io'+p.path.rstrip('/')
    if host=='jobs.ashbyhq.com' and re.fullmatch(r'/[\w-]+/[0-9a-f-]{36}(?:/application)?/?',p.path,re.I):
        return 'https://'+host+p.path.rstrip('/').removesuffix('/application')
    raise SafetyError('Unsupported hosted ATS URL')


def field_key(field):
    return (field.get('id') or field.get('name') or normalized(field['label']),field['type'])


def platform_hold(data,platform,job=None):
    path=Path(data)/'external'/(platform+'-verification-hold.json')
    if not path.exists():return False
    hold=json.loads(path.read_text(encoding='utf-8'))
    if not hold.get('active',False):return False
    # A diagnosed transport problem may be tested ONCE on an explicitly selected
    # new job. Account restrictions never have this exception; CAPTCHA stays human.
    if (job is not None and hold.get('scope')=='submission_diagnostics'
            and hold.get('account_restriction_confirmed') is False
            and job_key(job['url']) in hold.get('diagnostic_allow_once',{})):
        store=AttemptStore(Path(data))
        previous=[a for a in store.list() if job_key(a['job_url'])==job_key(job['url'])]
        if not any(store.submissions(a['id']) for a in previous):return False
    return True


class LeverAdapter:
    def __init__(self,page,job,data,attempt,monitor,status=None):
        self.page=page;self.job=job;self.data=Path(data);self.attempt=attempt;self.monitor=monitor
        self.book=AnswerBook(data);self.used=[];self.expected=[];self.resume_verified=False
        self.directory=Path(self.book.store.get(attempt)['evidence_dir'])
        self.metrics={'rule':0,'cache':0,'pending':0,'model':0,'autonomous':0}
        self.status=status if status is not None else {}

    def stage(self,stage):
        self.status.update(stage=stage,job_url=self.job['url'],attempt_id=self.attempt)
        self.book.store.event(self.attempt,'external_stage',{'stage':stage})

    async def inspect(self):
        if self.monitor.blocked_request:raise SafetyError('Unrecognized portal request blocked; inspect diagnostics')
        form=self.page.locator('#application-form')
        if await form.count()!=1:raise SafetyError('Lever hosted form not found uniquely')
        return await form.evaluate(FIELDS)

    async def locate(self,field,index=0):
        matches=[f for f in await self.inspect() if field_key(f)==field_key(field)]
        if len(matches)!=1:raise SafetyError('Control changed or is ambiguous: '+field['label'])
        return self.page.locator('[data-langhire-external="'+matches[0]['targets'][index]+'"]')

    async def set(self,field,answer):
        target=await self.locate(field);value=answer['value']
        if field['type']=='select-one':await target.select_option(str(value))
        elif field['type']=='checkbox':await target.set_checked(value)
        elif field['type']=='radio':
            i=next(i for i,o in enumerate(field['options']) if o['value']==value)
            target=await self.locate(field,i);await target.check()
        elif field['type']=='location':
            from .pilot_review import brazil_location_pattern,accepted_brazil_location
            city=self.book.facts.get('location.city',{}).get('value');state=self.book.facts.get('location.state',{}).get('value')
            if not city or not state:raise SafetyError('Confirmed city/state needed for required autocomplete')
            await target.fill('');await target.press_sequentially(str(city),delay=35)
            option=self.page.locator('.dropdown-results > *').filter(has_text=brazil_location_pattern(city,state))
            await option.first.wait_for(timeout=12000)
            if await option.count()!=1:raise SafetyError('Location has ambiguous suggestions')
            await option.click()
            for _ in range(20):
                token=await self.page.locator('#selected-location').input_value()
                if accepted_brazil_location(token,city,state):break
                await asyncio.sleep(.3)
            else:raise SafetyError('Location was not accepted by the portal')
            value=await (await self.locate(field)).input_value()
        else:await target.fill(str(value))
        target=await self.locate(field,next((i for i,o in enumerate(field['options']) if o['value']==value),0) if field['type']=='radio' else 0)
        actual=await target.is_checked() if field['type'] in ('radio','checkbox') else await target.input_value()
        if (field['type']=='radio' and not actual) or (field['type']!='radio' and str(actual)!=str(value)):
            raise SafetyError('Portal did not accept answer: '+field['label'])
        self.expected.append((field,value))
        self.used.append({'question':field['label'],'value':value,'refs':answer['refs'],'source':answer['source'],
                          'answer_id':answer.get('answer_id'),'approval_value':answer.get('approval_value',value)})

    async def upload(self,field):
        purpose=normalized(' '.join(str(field.get(k,'')) for k in ('id','name','label')))
        if re.search(r'cover|carta|additional|outro',purpose) or not re.search(r'resume|curriculo|\bcv\b',purpose):
            if field['required']:raise SafetyError('Unsupported mandatory attachment: '+field['label'])
            return
        target=await self.locate(field);await target.set_input_files(str(self.resume))
        # Hosted resume parsing sometimes takes longer than 15 seconds. Wait for
        # the portal's stored attachment before treating the upload as complete.
        for _ in range(240):
            names=await target.evaluate('e=>[...e.files].map(f=>({name:f.name,size:f.size}))')
            stored=self.page.locator('input[name="resumeStorageId"]')
            storage=await stored.input_value() if await stored.count() else ''
            filename=self.resume.name.casefold() in (await self.page.locator('#application-form').inner_text()).casefold()
            if storage and filename:
                self.resume_verified=True;break
            if not await stored.count() and any(x['name']==self.resume.name and x['size']==self.resume.stat().st_size for x in names):
                self.resume_verified=True;break
            await asyncio.sleep(.25)
        if not self.resume_verified:raise SafetyError('Resume upload not confirmed by portal attachment state')
        self.used.append({'question':field['label'],'value':self.resume.name,'refs':['configured_resume'],'source':'rule','sha256':self.resume_hash})

    async def fill(self):
        self.stage('fill_external')
        missing=[];pending=[]
        for original in await self.inspect():
            current=[f for f in await self.inspect() if field_key(f)==field_key(original)]
            if not current:continue  # A conditional control may have disappeared.
            field=current[0]
            if field['type']=='file':await self.upload(field);continue
            if field['type']=='location' and not field['required']:
                # Optional location enrichment must not block a valid application.
                # Clear the visible optional field through its native input handler.
                target=await self.locate(field);await target.fill('')
                self.expected.append((field,''))
                self.used.append({'question':field['label'],'value':'','refs':[],'source':'rule'})
                continue
            answer=None
            if not field['required'] and field['value'] in ('',False,[]):
                answer=self.book.direct(field,self.book.context(field['label'],self.job))
                if answer is None:continue
            if field['type'] not in ('text','email','tel','number','url','textarea','select-one','checkbox','radio','location'):
                if field['required']:
                    missing.append('Unsupported control: '+field['label']);pending.append({'question':field['label'],'reason':'unsupported_required_control','control':field['type']})
                continue
            if field['type']=='location':
                value=self.book.facts.get('location.city',{}).get('value')
                answer={'value':value,'refs':['location.city','location.state','location.country'],'source':'rule'} if value else {'pending':'location','source':'pending'}
            else:
                from .automatic_answers import resolve_answer
                answer=answer or await resolve_answer(self.book,field,self.job,self.metrics)
            self.metrics[answer['source']]+=1
            if 'pending' in answer:
                missing.append(str(answer['pending']));pending.append({'question':field['label'],'answer_id':answer['pending'],'reason':answer.get('proposal_reason','unconfirmed_answer')});continue
            await self.set(field,answer)
        if missing:
            (self.directory/'pending-answers.json').write_text(json.dumps(pending,ensure_ascii=False,indent=2),encoding='utf-8')
            raise SafetyError('Unresolved external questions: '+', '.join(missing))
        if not self.resume_verified:raise SafetyError('No verified configured resume')

    async def review(self):
        problems=[]
        if self.book.version!=AnswerBook(self.data).version:problems.append('Candidate facts changed')
        if hashlib.sha256(self.resume.read_bytes()).hexdigest()!=self.resume_hash:problems.append('Configured PDF changed')
        for field,value in self.expected:
            target=await self.locate(field,next((i for i,o in enumerate(field['options']) if o['value']==value),0) if field['type']=='radio' else 0)
            actual=await target.is_checked() if field['type'] in ('radio','checkbox') else await target.input_value()
            if (field['type']=='radio' and not actual) or (field['type']!='radio' and str(actual)!=str(value)):problems.append(field['label'])
            if field['type']=='location' and field['required']:
                from .pilot_review import accepted_brazil_location
                token=await self.page.locator('#selected-location').input_value()
                city=self.book.facts.get('location.city',{}).get('value')
                state=self.book.facts.get('location.state',{}).get('value')
                if not accepted_brazil_location(token,city,state):problems.append('Location selection changed')
        approved={q['id']:q for q in self.book.list()}
        for answer in self.used:
            if answer.get('answer_id'):
                current=approved.get(answer['answer_id'],{})
                if current.get('state')!='approved' or current.get('answer')!=answer.get('approval_value',answer['value']):problems.append('Approved answer changed')
        for field in await self.inspect():
            if field['required'] and field['type']!='file' and field['value'] in ('',False,[]):problems.append(field['label'])
        text=await self.page.locator('body').inner_text()
        if ERROR.search(text):problems.append('Portal verification/submission error')
        for element in await self.page.locator('.application-error,.application-field-error,[aria-invalid=true],#resume-error').all():
            if await element.is_visible() and ((await element.inner_text()).strip() or await element.get_attribute('aria-invalid')=='true'):
                problems.append('Visible portal validation error')
        if damaged_text(text):problems.append('Damaged text encoding')
        snapshot={'job_url':self.job['url'],'title':self.job['title'],'company':self.job['company'],'answers':self.used,
                  'resume_hash':self.resume_hash,'fields':await self.inspect(),'facts_version':self.book.version}
        return {'ready':not problems,'reason':'; '.join(problems),'digest':digest(snapshot),'snapshot':snapshot}

    async def run(self,resume,execution='review'):
        self.stage('open_external')
        self.resume=Path(resume)
        if not self.resume.is_file() or self.resume.suffix.lower()!='.pdf':raise SafetyError('Configured PDF missing')
        self.resume_hash=hashlib.sha256(self.resume.read_bytes()).hexdigest()
        await self.page.goto(self.job['url']+'/apply',wait_until='domcontentloaded',timeout=45000)
        await self.page.locator('#application-form').wait_for(timeout=20000)
        if canonical_ats(self.page.url)!=self.job['url']:raise SafetyError('Application redirected to another job/platform')
        heading=self.page.locator('.posting-headline h2,h2').first
        if normalized(await heading.inner_text())!=normalized(self.job['title']):raise SafetyError('Job title changed; refresh qualification')
        await self.fill();review=await self.review()
        self.stage('review_external')
        (self.directory/'external-review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf-8')
        await self.page.screenshot(path=str(self.directory/'external-review.png'),full_page=True)
        if not review['ready']:raise SafetyError('External review blocked: '+review['reason'])
        self.book.store.event(self.attempt,'external_review',{'ready':True,'digest':review['digest'],'facts_version':self.book.version})
        if execution=='review':self.book.store.transition(self.attempt,'reviewed','Hosted Lever form filled and independently reviewed; no submit');return
        async def preflight():
            current=await self.review()
            return {'ready':current['ready'] and current['digest']==review['digest'],'reason':current['reason'] or 'Form changed'}
        if not (await preflight())['ready']:raise SafetyError('Form changed before final click')
        async def request_ready():
            # The native submit handler may disable or replace controls after the click.
            ready=self.book.version==AnswerBook(self.data).version and hashlib.sha256(self.resume.read_bytes()).hexdigest()==self.resume_hash
            return {'ready':ready,'reason':'' if ready else 'Candidate facts or configured PDF changed after review'}
        self.monitor.pre_submit_review=request_ready
        self.stage('submit_external')
        self.monitor.arm_submission(self.job['url']+'/apply')
        await self.page.get_by_role('button',name=re.compile(r'^Submit application$',re.I)).click()
        await self.monitor.finalize()


@asynccontextmanager
async def application_browser(data,existing=None):
    if existing is not None:yield existing
    else:
        async with persistent_browser(data) as session:yield session


async def execute_external(job,data,resume,execution='review',status=None,lease_held=False,session=None):
    from contextlib import nullcontext
    if job['platform'] not in ('lever','greenhouse'):raise SafetyError('Unsupported hosted ATS; not using a generic agent')
    data=Path(data);job=dict(job,url=canonical_ats(job['url']),eligibility_country='BR',form_language='en')
    contact=data/'pending-contact-verification.json'
    if execution=='submit' and contact.exists() and json.loads(contact.read_text(encoding='utf-8')).get('active'):
        raise SafetyError('Candidate email confirmation pending; no additional send')
    if execution=='submit' and platform_hold(data,job['platform'],job):
        raise SafetyError('Platform submissions paused for verification diagnosis: '+job['platform'])
    with (nullcontext() if lease_held else FileLock(str(data/'application-worker.lock'),timeout=0)):
        store=AttemptStore(data);store.recover();aid=store.begin(job['url'])
        if job.get('_batch_id'):store.event(aid,'external_batch',{'batch_id':job['_batch_id'],'attempt_id':aid})
        if job.get('external_qualification'):store.event(aid,'external_qualification',job['external_qualification'])
        try:
            adapter_type=LeverAdapter
            if job['platform']=='greenhouse':
                from .greenhouse_apply import GreenhouseAdapter
                adapter_type=GreenhouseAdapter
            async with application_browser(data,session) as active_session:
                monitor=SubmissionMonitor(store,aid,cancel=status)
                adapter=None
                try:
                    await monitor.attach(active_session)
                    adapter=adapter_type(monitor.contexts[0].pages[0],job,data,aid,monitor,status)
                    await adapter.run(resume,execution)
                except BaseException as error:
                    aid=monitor.attempt_id
                    import traceback
                    # Code locations only; frames never include page content or answers.
                    where=[f'{Path(f.filename).name}:{f.lineno} {f.name}' for f in traceback.extract_tb(error.__traceback__)][-6:]
                    store.event(aid,'external_error',{'type':type(error).__name__,'message':str(error)[:700],'where':where})
                    await monitor.capture('external-failure')
                    if store.get(aid)['state'] in ACTIVE:store.transition(aid,'uncertain' if store.submissions(aid) else 'pending',str(error)[:700])
                    if isinstance(error,asyncio.CancelledError):raise
                finally:
                    aid=monitor.attempt_id
                    if adapter:store.event(aid,'answer_metrics',adapter.metrics)
                    await monitor.close()
        except Exception as error:
            if store.get(aid)['state'] in ACTIVE:store.transition(aid,'uncertain' if store.submissions(aid) else 'pending',str(error)[:700])
        return store.get(aid)
