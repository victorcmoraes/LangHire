"""Hosted Greenhouse forms: schema-backed controls, review and one final click."""
import asyncio
import hashlib
import json
from pathlib import Path
import re
import urllib.request
from datetime import datetime
from urllib.parse import urlsplit

from .application_safety import SafetyError,now
from .easy_answers import AnswerBook,normalized,digest,damaged_text
from .automatic_answers import resolve_answer
from .submission_monitor import ERROR,greenhouse_job_id,clean_url


async def fetch_schema(job):
    board,identifier=greenhouse_job_id(job['url'])
    def fetch():
        url=f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{identifier}?questions=true'
        with urllib.request.urlopen(url,timeout=30) as response:return json.load(response)
    schema=await asyncio.to_thread(fetch)
    if str(schema.get('id'))!=identifier or normalized(schema['title'])!=normalized(job['title']):
        raise SafetyError('Greenhouse job/schema identity changed; refresh qualification')
    return schema


def schema_fields(schema):
    """Include auxiliary hosted sections, not just ordinary custom questions."""
    fields=[]
    kinds={'input_text':'text','input_file':'file','textarea':'textarea',
           'multi_value_single_select':'select-one','multi_value_multi_select':'checkbox-group'}
    for question in schema.get('questions',[]):
        alternatives=question.get('fields',[])
        chosen=[f for f in alternatives if f['type']=='input_file'] or alternatives
        for raw in chosen:
            if raw['name']=='phone' and question.get('required'):
                fields.append({'id':'country','name':'country','label':'Phone country code','type':'phone-country',
                               'required':True,'options':[]})
            fields.append({'id':raw['name'],'name':raw['name'],'label':question['label'],
                'type':kinds.get(raw['type'],'unsupported'),'control':raw['type'],'required':bool(question.get('required')),
                'separate_country_code':raw['name']=='phone' and bool(question.get('required')),
                'options':[{'text':o['label'],'value':str(o['value'])} for o in raw.get('values',[])]})
    locations=[q for q in schema.get('location_questions',[]) if any(f['name']=='location' for f in q.get('fields',[]))]
    if locations:
        fields.append({'id':'candidate-location','name':'location','label':'Location (City)','type':'location',
                       'required':bool(locations[0].get('required')),'options':[]})
    for question in (schema.get('demographic_questions') or {}).get('questions',[]):
        fields.append({'id':str(question['id']),'name':str(question['id']),'label':question['label'],
            'type':kinds.get(question['type'],'unsupported'),'control':question['type'],
            'required':bool(question.get('required')),'category':'demographic',
            'options':[{'text':o['label'],'value':str(o['id']),'decline_to_answer':o.get('decline_to_answer',False)}
                       for o in question.get('answer_options',[])]})
    return fields


async def preflight_greenhouse(data,job):
    """Resolve mandatory facts before opening a browser or uploading a resume."""
    schema=await fetch_schema(job);book=AnswerBook(data);pending=[];metrics={'rule':0,'cache':0,'pending':0,'model':0}
    job=dict(job,form_language=schema.get('language') or job.get('form_language','unknown'))
    for field in schema_fields(schema):
        if not field['required']:continue
        if field['type']=='file' and field['id']=='resume':continue
        if field['type'] in ('file','unsupported'):
            pending.append({'question':field['label'],'reason':'unsupported_required_control','control':field.get('control')});metrics['pending']+=1;continue
        answer=await resolve_answer(book,field,job,metrics);metrics[answer['source']]+=1
        if 'pending' in answer:pending.append({'question':field['label'],'answer_id':answer['pending'],
            'reason':'approval_required' if answer.get('approval_required') else 'unconfirmed_answer',
            'proposal_state':answer.get('proposal_state'),'proposal_reason':answer.get('proposal_reason')})
    result={'url':job['url'],'checked_at':now(),'ready_for_browser':not pending,'pending':pending,'answer_metrics':metrics,'facts_version':book.version}
    directory=Path(data)/'external/preflight';directory.mkdir(parents=True,exist_ok=True)
    board,identifier=greenhouse_job_id(job['url'])
    path=directory/(board+'-'+identifier+'.json');path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


class GreenhouseAdapter:
    def __init__(self,page,job,data,attempt,monitor,status=None):
        self.page=page;self.job=job;self.data=Path(data);self.attempt=attempt;self.monitor=monitor
        self.book=AnswerBook(data);self.directory=Path(self.book.store.get(attempt)['evidence_dir'])
        self.status=status if status is not None else {};self.used=[];self.expected=[];self.resume_verified=False
        self.metrics={'rule':0,'cache':0,'pending':0,'model':0}
        self.monitor.email_verification_handler=self.handle_email_verification

    def stage(self,stage):
        self.status.update(stage=stage,job_url=self.job['url'],attempt_id=self.attempt)
        self.book.store.event(self.attempt,'external_stage',{'stage':stage})

    def target(self,name):
        return self.page.locator('[id='+json.dumps(name)+']')

    async def load_schema(self):
        self.schema=await fetch_schema(self.job)
        self.job['form_language']=self.schema.get('language') or self.job.get('form_language','unknown')
        (self.directory/'greenhouse-schema.json').write_text(json.dumps(self.schema,ensure_ascii=False,indent=2),encoding='utf-8')

    async def inspect(self):
        fields=[];mapped=set()
        for original in schema_fields(self.schema):
            field=dict(original,value='',present=False);name=field['id'];kind=field['type'];options=field['options'];target=self.target(name)
            if kind=='checkbox-group' and (await target.count()!=1 or await target.get_attribute('role')!='combobox'):
                controls=self.page.locator('input[type=checkbox][name='+json.dumps(name)+']')
                field['present']=bool(await controls.count());field['value']=await controls.evaluate_all('es=>es.filter(e=>e.checked).map(e=>e.value)')
                if field['present']:mapped.add(name)
                fields.append(field);continue
            if await target.count()==1:
                field['present']=True;mapped.add(name)
                field['render']=await target.get_attribute('role') or await target.evaluate('e=>e.tagName.toLowerCase()')
                if kind=='file':field['value']=await target.evaluate('e=>[...e.files].map(f=>f.name)')
                elif field['render']=='combobox':
                    label=await target.evaluate("e=>e.closest('.select__container')?.querySelector('.select__single-value')?.innerText||''")
                    if kind=='checkbox-group':
                        labels=await target.evaluate("e=>[...e.closest('.select__container').querySelectorAll('.select__multi-value__label')].map(n=>n.innerText)")
                        field['value']=[o['value'] for label in labels for o in options if normalized(o['text'])==normalized(label)]
                        if len(field['value'])!=len(labels):field['value']=[]
                    elif kind=='phone-country':field['value']='+55' if re.search(r'\+55\b',label) else label
                    elif kind=='location':field['value']=label
                    else:
                        match=[o['value'] for o in options if normalized(o['text'])==normalized(label)]
                        field['value']=match[0] if len(match)==1 else ''
                else:field['value']=await target.input_value()
            fields.append(field)
        # Helpers within mapped React selects are not separate questions. Genuine
        # newly visible required controls must nevertheless block the review.
        unknown=await self.page.locator('#application-form').evaluate(r'''(form,mapped)=>[...form.querySelectorAll('input,select,textarea')].filter(e=>{
            if(e.disabled||e.type==='hidden'||e.type==='file'||/captcha/i.test(e.name||e.id))return false;
            if(/^security-input-\d+$/.test(e.id)&&e.closest('.email-verification'))return false;
            if(!(e.required||e.getAttribute('aria-required')==='true'))return false;
            if(mapped.includes(e.id)||mapped.includes(e.name))return false;
            const container=e.closest('.select__container');
            if(container&&[...container.querySelectorAll('[id]')].some(n=>mapped.includes(n.id)))return false;
            return !!e.getClientRects().length;
        }).map(e=>({id:e.id,name:e.name,type:e.type,checked:e.checked,label:e.getAttribute('aria-label')||[...(e.labels||[])].map(l=>l.innerText).join(' ')||e.id||'Unmapped required control'}))''',list(mapped))
        demographics=[f for f in fields if f.get('category')=='demographic']
        declines_only=bool(demographics) and all(
            (not f['required'] and not f['value']) or (f['present'] and bool(f['value']) and all(
                any(str(v)==o['value'] and o.get('decline_to_answer') is True for o in f['options'])
                for v in (f['value'] if isinstance(f['value'],list) else [f['value']]))) for f in demographics)
        has_consent=any(c.get('demographic_data_consent_applies') is True for c in self.schema.get('data_compliance',[]))
        requires_processing=any(c.get('requires_processing_consent') is True for c in self.schema.get('data_compliance',[]))
        for e in unknown:
            field={'id':e['id'],'name':e['name'],'label':e['label'],'type':'unsupported','required':True,'present':True,'value':'','options':[]}
            if (has_consent and e['type']=='checkbox' and e['id']
                    and re.search(r'consent.{0,180}process.{0,80}demographic data|consinto.{0,180}process.{0,80}dados demograficos',normalized(e['label']),re.I)):
                field.update(type='checkbox',category='demographic-consent',declines_only=declines_only,value=e['checked'])
            elif (requires_processing and e['type']=='checkbox' and e['name']=='gdpr_processing_consent_given'
                    and re.fullmatch(r'gdpr_processing_consent_given_\d+',e['id'])
                    and re.search(r'store and process my data.{0,130}current application for employment',normalized(e['label']))):
                field.update(type='checkbox',category='application-processing-consent',current_application_only=True,value=e['checked'])
            fields.append(field)
        return fields

    async def set(self,field,answer):
        target=self.target(field['id']);value=answer['value']
        if field['type']=='checkbox':await target.set_checked(value)
        elif field['type']=='phone-country':
            if value!='+55':raise SafetyError('Unsupported phone-country combination')
            await target.click();await target.fill('55')
            await self.page.get_by_role('option',name=re.compile(r'^(Brazil|Brasil)\s+\+55$')).click(timeout=10000)
        elif field['type']=='location':
            city=self.book.facts['location.city']['value'];state=self.book.facts['location.state']['value']
            await target.click();await target.fill(str(city))
            await self.page.get_by_role('option').first.wait_for(timeout=15000)
            matches=[label for label in await self.page.get_by_role('option').all_text_contents()
                     if re.fullmatch(re.escape(normalized(city))+r' (?:state of )?'+re.escape(normalized(state))+r' (?:brazil|brasil)',normalized(label))]
            if len(matches)!=1:raise SafetyError('Location suggestions are missing or ambiguous')
            value=matches[0];await self.page.get_by_role('option',name=value,exact=True).click()
        elif field['type']=='checkbox-group' and field.get('render')=='combobox':
            container=target.locator('xpath=ancestor::*[contains(concat(" ",normalize-space(@class)," ")," select__container ")][1]')
            if field['value']:
                clear=container.get_by_role('button',name='Clear selections',exact=True)
                if await clear.count()!=1:raise SafetyError('Cannot safely replace selected survey answers')
                await clear.click()
            for choice in value:
                label=next(o['text'] for o in field['options'] if o['value']==str(choice))
                await target.click();await target.fill(label)
                await self.page.get_by_role('option',name=label,exact=True).click(timeout=10000)
        elif field['type']=='checkbox-group':
            controls=self.page.locator('input[type=checkbox][name='+json.dumps(field['name'])+']')
            actual_options=await controls.evaluate_all('es=>es.map(e=>e.value)')
            if set(actual_options)!=set(o['value'] for o in field['options']):
                raise SafetyError('Greenhouse checkbox options changed: '+field['label'])
            for control in await controls.all():
                await control.set_checked((await control.input_value()) in value)
        elif field.get('render')=='combobox':
            option=next(o['text'] for o in field['options'] if o['value']==str(value))
            await target.click();await target.fill(option)
            await self.page.get_by_role('option',name=option,exact=True).click(timeout=10000)
        elif field['type']=='select-one':await target.select_option(str(value))
        else:await target.fill(str(value))
        current=next(f for f in await self.inspect() if f['id']==field['id'])
        if field['id']=='phone':
            digits=lambda s:re.sub(r'\D','',str(s))
            if digits(current['value'])!=digits(value):raise SafetyError('Greenhouse did not accept phone number')
            value=current['value']
        accepted=sorted(current['value'])==sorted(value) if field['type']=='checkbox-group' else str(current['value'])==str(value)
        if not accepted:raise SafetyError('Greenhouse did not accept answer: '+field['label'])
        self.expected.append((field['id'],value))
        self.used.append({'question':field['label'],'value':value,'refs':answer['refs'],'source':answer['source'],
                          'answer_id':answer.get('answer_id'),'approval_value':answer.get('approval_value',value)})

    async def upload(self,field):
        if field['id']!='resume':
            if field['required']:raise SafetyError('Unsupported mandatory attachment: '+field['label'])
            return
        target=self.target('resume');await target.set_input_files(str(self.resume))
        for _ in range(120):
            group=self.page.locator('[role=group][aria-labelledby="upload-label-resume"]')
            label=await group.inner_text() if await group.count() else await target.locator('..').inner_text()
            # Greenhouse replaces its file input with the accepted attachment.
            # Do not wait for the old input to reappear after a successful upload.
            files=await target.evaluate('e=>[...e.files].map(f=>({name:f.name,size:f.size}))') if await target.count() else []
            if self.resume.name.casefold() in label.casefold() and (not files or any(f['name']==self.resume.name and f['size']==self.resume.stat().st_size for f in files)):
                self.resume_verified=True;break
            await asyncio.sleep(.25)
        if not self.resume_verified:raise SafetyError('Greenhouse resume attachment not confirmed')
        self.used.append({'question':field['label'],'value':self.resume.name,'sha256':self.resume_hash,'refs':['configured_resume'],'source':'rule'})

    async def fill(self):
        self.stage('fill_external');missing=[];pending=[]
        for field in await self.inspect():
            if field.get('category')=='demographic-consent':continue  # Resolve after survey choices are accepted.
            if not field['present']:
                if field['required']:missing.append('Missing required control: '+field['label'])
                continue
            if field['type']=='file':await self.upload(field);continue
            # The optional phone's country picker is a separate control; do not
            # leave a phone entered without its own verified country selection.
            if field['id']=='phone' and not field['required']:continue
            if field['type'] not in ('text','textarea','select-one','checkbox','checkbox-group','phone-country','location'):
                if field['required']:
                    missing.append('Unsupported control: '+field['label']);pending.append({'question':field['label'],'reason':'unsupported_required_control','control':field['type']})
                continue
            answer=await resolve_answer(self.book,field,self.job,self.metrics) if field['required'] else self.book.direct(field,self.book.context(field['label'],self.job))
            if not answer:continue
            self.metrics[answer['source']]+=1
            if 'pending' in answer:
                missing.append(str(answer['pending']));pending.append({'question':field['label'],'answer_id':answer['pending'],'reason':answer.get('proposal_reason','unconfirmed_answer')});continue
            await self.set(field,answer)
        for field in await self.inspect():
            if field.get('category')!='demographic-consent':continue
            answer=await resolve_answer(self.book,field,self.job,self.metrics);self.metrics[answer['source']]+=1
            if 'pending' in answer:missing.append(str(answer['pending']))
            else:await self.set(field,answer)
        if missing:
            (self.directory/'pending-answers.json').write_text(json.dumps(pending,ensure_ascii=False,indent=2),encoding='utf-8')
            (self.directory/'unresolved-controls.json').write_text(json.dumps(await self.inspect(),ensure_ascii=False,indent=2),encoding='utf-8')
            raise SafetyError('Unresolved Greenhouse questions: '+', '.join(missing))
        if not self.resume_verified:raise SafetyError('No configured PDF attachment verified')

    async def review(self):
        fields=await self.inspect();by={f['id']:f for f in fields};problems=[]
        if self.book.version!=AnswerBook(self.data).version:problems.append('Candidate facts changed')
        if hashlib.sha256(self.resume.read_bytes()).hexdigest()!=self.resume_hash:problems.append('Configured PDF changed')
        if greenhouse_job_id(self.page.url)!=greenhouse_job_id(self.job['url']):problems.append('Wrong job page')
        for name,value in self.expected:
            actual=by.get(name,{}).get('value')
            equal=sorted(actual)==sorted(value) if isinstance(actual,list) and isinstance(value,list) else str(actual)==str(value)
            if not equal:problems.append('Accepted answer changed: '+name)
        for field in fields:
            if field['required'] and (not field['present'] or field['value'] in ('',[],False)):
                if field['id']!='resume' or not self.resume_verified:problems.append(field['label'])
        approved={q['id']:q for q in self.book.list()}
        for used in self.used:
            if used.get('answer_id'):
                row=approved.get(used['answer_id'],{})
                if row.get('state')!='approved' or row.get('answer')!=used.get('approval_value',used['value']):problems.append('Approved answer changed')
        for error in await self.page.locator('[aria-invalid=true],[role=alert],.error-message,.application--error').all():
            if await error.is_visible() and ((await error.inner_text()).strip() or await error.get_attribute('aria-invalid')=='true'):problems.append('Visible portal validation error')
        text=await self.page.locator('body').inner_text()
        if ERROR.search(text):problems.append('Portal application error')
        # Employer copy can contain legitimate punctuation such as "produção?Se".
        # Validate submitted values, rather than treating the entire job posting
        # as candidate-generated text.
        if any(damaged_text(value) for field in fields
               for value in (field['value'] if isinstance(field['value'],list) else [field['value']])):
            problems.append('Damaged answer text encoding')
        snapshot={'job_url':self.job['url'],'title':self.job['title'],'answers':self.used,'fields':fields,'resume_hash':self.resume_hash,'facts_version':self.book.version}
        return {'ready':not problems,'reason':'; '.join(problems),'snapshot':snapshot,'digest':digest(snapshot)}

    async def handle_email_verification(self):
        self.stage('awaiting_email_verification')
        automatic=await self.fill_email_code()
        if not automatic:
            await self.monitor.wait_for_intervention(
                'Insira o codigo de 8 caracteres recebido por email na janela aberta. Nao clique em enviar; confirme no terminal/app depois.',
                allow_email_transition=False)
        if self.monitor.terminal:return
        complete=await self.page.locator('.email-verification [id^="security-input-"]').evaluate_all(
            'es=>es.length===8&&es.every(e=>e.value.trim().length===1&&e.getAttribute("aria-invalid")!=="true")')
        if not complete:raise SafetyError('Email verification code not complete; no additional click')
        current=await self.review()
        if not current['ready'] or current['digest']!=self.review_digest:
            raise SafetyError('Reviewed form or facts changed during email verification')
        await self.monitor.continue_email_verification()
        self.attempt=self.monitor.attempt_id;self.directory=self.monitor.directory
        self.stage('submit_verified_email')
        self.monitor.arm_submission(self.job['url'])
        await self.page.get_by_role('button',name=re.compile(r'^(Submit application|Enviar candidatura|Enviar inscrição)$',re.I)).click()

    async def fill_email_code(self):
        from .gmail_mailbox import GmailMailbox,MailError,expected_email,wait_for_code
        from .external_service import remaining_budget
        if not GmailMailbox(self.data).status()['authorized']:return False
        if remaining_budget(self.book.store)==0:
            self.book.store.event(self.attempt,'gmail_verification_unavailable',{'reason':'daily_limit'})
            return False
        try:
            if self.monitor.response_tasks:
                await asyncio.gather(*list(self.monitor.response_tasks),return_exceptions=True)
            email=expected_email(self.data)
            text=await self.page.locator('.email-verification').inner_text()
            if email.lower() not in text.lower():raise MailError('Portal verification recipient differs from candidate email')
            sends=self.book.store.submissions(self.attempt)
            if len(sends)!=1:raise MailError('Email verification requires one original send')
            self.stage('waiting_for_email_code')
            code=await wait_for_code(self.data,self.attempt,
                [self.schema.get('company_name',''),self.job.get('company',''),self.job.get('board','')],
                datetime.fromisoformat(sends[0]['at']).timestamp(),self.status)
            if not code:
                self.book.store.event(self.attempt,'gmail_verification_unavailable',{'reason':'mail_timeout'})
                return False
            outcome=self.monitor.observed_outcome(await self.monitor.observe())
            if outcome:await self.monitor.finish(*outcome);return True
            controls=self.page.locator('.email-verification [id^="security-input-"]')
            if await controls.count()!=8:raise MailError('The original email verification session is no longer available')
            for i,character in enumerate(code.code):await self.target('security-input-'+str(i)).fill(character)
            self.book.store.event(self.attempt,'gmail_verification_filled',{'message_id':code.message_id,'received_at':code.received_at})
            return True
        except MailError as error:
            self.book.store.event(self.attempt,'gmail_verification_unavailable',{'reason':str(error)})
            return False

    async def run(self,resume,execution='review'):
        self.resume=Path(resume)
        if not self.resume.is_file() or self.resume.suffix.lower()!='.pdf':raise SafetyError('Configured PDF missing')
        self.resume_hash=hashlib.sha256(self.resume.read_bytes()).hexdigest();self.stage('open_external')
        await self.load_schema()
        # Native uploader configuration arrives after the form renders. Setting
        # the file too early triggers the site's undefined.uploadFile error.
        def uploader_response(response):
            parsed=urlsplit(response.url)
            return parsed.hostname in ('boards.greenhouse.io','job-boards.greenhouse.io','boards-api.greenhouse.io') and parsed.path.endswith('/uncacheable_attributes/presigned_fields')
        async with self.page.expect_response(uploader_response,timeout=30000) as initialization:
            await self.page.goto(self.job['url'],wait_until='domcontentloaded',timeout=45000)
        response=await initialization.value
        await response.finished()
        configuration=await response.json() if response.ok else {}
        self.book.store.event(self.attempt,'greenhouse_upload_initialization',{'status':response.status,'resume_available':'resume' in configuration})
        if not response.ok or 'resume' not in configuration:raise SafetyError('Greenhouse uploader configuration unavailable: HTTP '+str(response.status))
        await self.page.evaluate('()=>new Promise(resolve=>setTimeout(resolve,0))')
        await self.page.locator('#application-form').wait_for(timeout=20000)
        heading=self.page.locator('h1').first
        if normalized(await heading.inner_text())!=normalized(self.job['title']):raise SafetyError('Greenhouse page title changed')
        await self.fill();review=await self.review();self.stage('review_external')
        (self.directory/'external-review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf-8')
        await self.page.screenshot(path=str(self.directory/'external-review.png'),full_page=True)
        if not review['ready']:raise SafetyError('Greenhouse review blocked: '+review['reason'])
        self.review_digest=review['digest']
        self.book.store.event(self.attempt,'external_review',{'ready':True,'digest':review['digest'],'facts_version':self.book.version})
        if execution=='review':self.book.store.transition(self.attempt,'reviewed','Hosted Greenhouse form filled and reviewed; no submit');return
        current=await self.review()
        if not current['ready'] or current['digest']!=review['digest']:raise SafetyError('Greenhouse form changed before submit')
        async def request_ready():
            ready=self.book.version==AnswerBook(self.data).version and hashlib.sha256(self.resume.read_bytes()).hexdigest()==self.resume_hash
            return {'ready':ready,'reason':'' if ready else 'Facts or PDF changed after review'}
        self.monitor.pre_submit_review=request_ready
        self.stage('submit_external');self.monitor.arm_submission(self.job['url'])
        await self.page.get_by_role('button',name=re.compile(r'^(Submit application|Enviar candidatura|Enviar inscrição)$',re.I)).click()
        await self.monitor.finalize()
