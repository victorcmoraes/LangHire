"""LinkedIn-only state machine. No browser agent and no per-employer scripts."""
import asyncio
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlencode

from filelock import FileLock
from .application_safety import AttemptStore, ACTIVE, SafetyError, now
from .browser_runtime import persistent_browser
from .easy_answers import AnswerBook, digest, normalized
from .submission_monitor import SubmissionMonitor, ERROR

EASY = re.compile(r'\b(easy apply|candidatura simplificada)\b', re.I)
NEXT = re.compile(r'^(next|continue|avançar|continuar|próximo)(?:\s|$)', re.I)
REVIEW = re.compile(r'^(review|revisar|revisar candidatura)(?:\s|$)', re.I)
SUBMIT = re.compile(r'^(submit application|enviar candidatura|enviar inscrição)(?:\s|$)', re.I)


def canonical(url):
    parsed = urlsplit(url)
    match = re.fullmatch(r'/jobs/view/(\d+)/?', parsed.path)
    if parsed.scheme != 'https' or parsed.hostname not in ('www.linkedin.com', 'linkedin.com') or not match:
        raise SafetyError('Only canonical LinkedIn job URLs are supported')
    return 'https://www.linkedin.com/jobs/view/' + match[1] + '/'


FIELDS = r'''root => {
 const visible=e=>!!(e.getClientRects().length)&&getComputedStyle(e).visibility!=='hidden';
 const fields=[], groups=new Set();
 for(const [i,e] of [...root.querySelectorAll('input,select,textarea')].entries()){
  if((!visible(e)&&e.type!=='file')||['hidden','submit','button'].includes(e.type)||e.disabled)continue;
  if(/captcha/.test(e.name||''))continue;
  const box=e.closest('fieldset')||e.closest('.jobs-easy-apply-form-section__grouping')||e.parentElement;
  let label=[...(e.labels||[])].map(x=>x.innerText).join(' ')||e.getAttribute('aria-label')||box.querySelector('legend')?.innerText||'';
  let type=e.getAttribute('role')==='combobox'?'autocomplete':e.type||'text';
  if(type==='radio'){
   for(let n=e.parentElement,d=0;n&&n!==root&&d<5;n=n.parentElement,d++){
    if(/\.(pdf|docx?)\b/i.test(n.innerText||'')&&(n.innerText||'').length<400){type='resume-choice';label=n.innerText;break;}
   }
  }
  let options=e.options?[...e.options].map(o=>({text:o.text,value:o.value})):[];
  let targets=[e];
  if(type==='radio'){
   if(groups.has(e.name))continue;groups.add(e.name);
   targets=[...root.querySelectorAll('input[type="radio"]')].filter(x=>x.name===e.name);
   label=box.querySelector('legend')?.innerText||box.querySelector('.fb-dash-form-element__label')?.innerText||label;
   options=targets.map(x=>({text:[...(x.labels||[])].map(l=>l.innerText).join(' ')||x.value,value:x.value}));
  }
  targets.forEach((x,j)=>x.setAttribute('data-langhire-field',`${i}-${j}`));
  fields.push({label:label.trim(),type,required:e.required||e.getAttribute('aria-required')==='true'||/[*✱]/.test(label),
   options,targets:targets.map(x=>x.getAttribute('data-langhire-field')),name:e.name,
   value:type==='file'?[...e.files].map(f=>f.name):['checkbox','resume-choice'].includes(type)?e.checked:type==='radio'?(targets.find(x=>x.checked)?.value||''):e.value});
 }
 for(const [i,e] of [...root.querySelectorAll('[role="combobox"], [role="checkbox"], [role="radiogroup"]')].entries()){
  if(['INPUT','SELECT','TEXTAREA'].includes(e.tagName)||!visible(e))continue;
  e.setAttribute('data-langhire-field',`custom-${i}`);
  fields.push({label:e.getAttribute('aria-label')||e.innerText,type:'custom',required:e.getAttribute('aria-required')==='true',options:[],targets:[`custom-${i}`],name:'',value:''});
 }
 return fields;
}'''


class EasyStore:
    def __init__(self, data):
        self.attempts = AttemptStore(data)
        with self.attempts.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS easy_reviews (
              id TEXT PRIMARY KEY, job_url TEXT NOT NULL, digest TEXT NOT NULL,
              facts_version TEXT NOT NULL, resume_hash TEXT NOT NULL, payload TEXT NOT NULL,
              created_at TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS easy_channel (
              id INTEGER PRIMARY KEY CHECK(id=1), suspended INTEGER NOT NULL, reason TEXT NOT NULL);''')

    def reviews(self):
        with self.attempts.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM easy_reviews WHERE consumed=0 ORDER BY created_at DESC')]

    def save(self, aid, job, snapshot, facts_version, resume_hash):
        with self.attempts.db() as db:
            db.execute('INSERT INTO easy_reviews VALUES(?,?,?,?,?,?,?,0)',
                       (aid, job['url'], digest(snapshot), facts_version, resume_hash, json.dumps(snapshot), now()))

    def consume(self, review_id, job_url, snapshot, facts_version, resume_hash):
        with self.attempts.db() as db:
            r = db.execute('SELECT * FROM easy_reviews WHERE id=? AND consumed=0', (review_id,)).fetchone()
            if not r or (r['job_url'], r['digest'], r['facts_version'], r['resume_hash']) != (job_url, digest(snapshot), facts_version, resume_hash):
                raise SafetyError('Review changed or expired; prepare a new review')
            db.execute('UPDATE easy_reviews SET consumed=1 WHERE id=?', (review_id,))

    def suspend(self, reason):
        with self.attempts.db() as db:
            db.execute('INSERT OR REPLACE INTO easy_channel VALUES(1,1,?)', (reason,))

    def channel(self):
        with self.attempts.db() as db:
            r = db.execute('SELECT * FROM easy_channel WHERE id=1').fetchone()
        return dict(r) if r else {'suspended': 0, 'reason': ''}


async def check_session(page):
    await page.goto('https://www.linkedin.com/feed/', wait_until='domcontentloaded', timeout=60000)
    if re.search(r'/(login|checkpoint|authwall|uas/)', page.url):
        raise SafetyError('LinkedIn login or account intervention required')
    await page.locator('a[href*="/messaging"]').first.wait_for(timeout=15000)
    await page.locator('a[href*="/jobs"]').first.wait_for(timeout=15000)


async def read_job_metadata(page):
    """Authenticated LinkedIn's current layout has no h1; use its document title."""
    title_parts=[p.strip() for p in (await page.title()).split('|')]
    heading=page.locator('h1').first
    title=await heading.inner_text() if await heading.count() else ''
    company=''
    if len(title_parts)>=3 and normalized(title_parts[-1])=='linkedin':
        title=title or re.sub(r'^\(\d+\)\s*','',title_parts[0])
        company=title_parts[-2]
        await page.get_by_role('heading',name=re.compile(r'^(Sobre a vaga|About the job)$',re.I)).first.wait_for(timeout=15000)
    if not title:raise SafetyError('Job title metadata unavailable')
    main=page.locator('main').first
    text=await main.inner_text() if await main.count() else ''
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    location=''
    for i,line in enumerate(lines[:-1]):
        if normalized(line)==normalized(title):location=lines[i+1].split('·')[0].strip();break
    description=text
    for marker in ('Sobre a vaga','About the job'):
        if marker in description:description=description.split(marker,1)[1];break
    for marker in ('Sobre a empresa','About the company'):
        if marker in description:description=description.split(marker,1)[0];break
    return {'title':title,'company':company,'location':location,'description':description}


class EasyExecutor:
    def __init__(self, page, data, job, attempt, monitor, *, status=None):
        self.page, self.data, self.job, self.attempt, self.monitor = page, Path(data), job, attempt, monitor
        self.answers = AnswerBook(data)
        self.store = EasyStore(data)
        self.directory = Path(self.store.attempts.get(attempt)['evidence_dir'])
        self.status = status if status is not None else {}
        self.used = []
        self.resume = None
        self.resume_hash = ''
        self.resume_verified = False
        self.counters = {'rule': 0, 'cache': 0, 'pending': 0, 'model': 0}

    def stage(self, value):
        self.status.update(stage=value, job_url=self.job['url'], attempt_id=self.attempt)
        self.store.attempts.event(self.attempt, 'easy_stage', {'stage': value})

    async def modal(self):
        # Accessible button names differ by language; scope by the Easy Apply container first.
        containers = self.page.locator('.jobs-easy-apply-modal, [role="dialog"][aria-label*="Apply"], [role="dialog"][aria-label*="Candidatura"], [data-easy-apply]')
        if await containers.count(): return containers.first
        dialogs = self.page.get_by_role('dialog')
        if await dialogs.count() == 1: return dialogs
        raise SafetyError('Easy Apply modal could not be identified uniquely')

    async def inspect(self, modal):
        if self.monitor.blocked_request:
            raise SafetyError('Unsupported portal request was blocked; inspect evidence before continuing')
        fields = await modal.evaluate(FIELDS)
        errors = await modal.locator('.artdeco-inline-feedback--error, .fb-dash-form-element__error-field').all_inner_texts()
        alerts=await modal.locator('[role="alert"]').all_inner_texts()
        errors.extend(a for a in alerts if ERROR.search(a) or re.search(r'campo obrigat.rio|invalid|required field|preencha|selecione',a,re.I))
        if await modal.locator('[aria-invalid="true"]').count():errors.append('A control reports aria-invalid=true')
        text = await modal.inner_text()
        if any(e.strip() for e in errors) or ERROR.search(text):
            raise SafetyError('Portal validation error: ' + '; '.join(e.strip() for e in errors)[:400])
        return fields

    async def wait_stage(self, modal):
        controls=modal.get_by_role('button',name=re.compile(NEXT.pattern+'|'+REVIEW.pattern+'|'+SUBMIT.pattern,re.I))
        await controls.first.wait_for(timeout=20000)
        for _ in range(80):
            if await controls.first.is_enabled():return
            await asyncio.sleep(0.25)
        raise SafetyError('Form stage remains disabled or loading')

    async def apply_field(self, modal, field, answer):
        value = answer['value']
        target = modal.locator('[data-langhire-field="' + field['targets'][0] + '"]')
        kind = field['type']
        if kind == 'select-one': await target.select_option(str(value))
        elif kind == 'checkbox': await target.set_checked(value)
        elif kind == 'radio':
            index = next(i for i, o in enumerate(field['options']) if o['value'] == value)
            target = modal.locator('[data-langhire-field="' + field['targets'][index] + '"]')
            await target.check()
        elif kind == 'autocomplete':
            await target.fill('')
            await target.press_sequentially(str(value), delay=30)
            pattern = re.compile('^'+re.escape(str(value))+'$',re.I)
            if 'location.city' in answer['refs'] and self.job.get('eligibility_country')=='BR':
                from .pilot_review import brazil_location_pattern
                state = self.answers.facts.get('location.state',{}).get('value','')
                if state:
                    pattern = brazil_location_pattern(str(value),state)
                    answer['refs'] = sorted(set(answer['refs']+['location.state','location.country']))
            options = self.page.get_by_role('option').filter(has_text=pattern)
            await options.first.wait_for(timeout=7000)
            if await options.count() != 1: raise SafetyError('Autocomplete has ambiguous options')
            await options.click()
            value = await target.input_value()
        else: await target.fill(str(value))
        actual = await target.is_checked() if kind in ('checkbox', 'radio') else await target.input_value()
        if (kind == 'radio' and not actual) or (kind != 'radio' and str(actual) != str(value)):
            raise SafetyError('Control did not accept answer: ' + field['label'])
        return value

    async def fill(self, modal):
        self.stage('resolve_fields')
        upload=modal.get_by_role('button',name=re.compile(r'^(Carregar curr[ií]culo|Upload resume|Upload CV)$',re.I))
        if not self.resume_verified and await upload.count():
            before=len(self.monitor.upload_receipts)
            async with self.page.expect_file_chooser() as chooser:
                await upload.first.click()
            await (await chooser.value).set_files(str(self.resume))
            await modal.get_by_text(self.resume.name,exact=True).first.wait_for(timeout=15000)
            for _ in range(60):
                receipts=self.monitor.upload_receipts[before:]
                if all(any(r['kind']==kind and 200<=r['status']<300 for r in receipts) for kind in ('binary','binding')):break
                if self.monitor.blocked_request:raise SafetyError('Resume upload endpoint requires review')
                await asyncio.sleep(0.25)
            else:raise SafetyError('Resume upload or selection did not receive confirmation')
            if self.monitor.blocked_request:raise SafetyError('Resume upload endpoint requires review')
            self.resume_verified=True
        fields = await self.inspect(modal)
        if not fields and not await modal.get_by_role('button',name=SUBMIT).count():
            text=await modal.inner_text()
            content='\n'.join(line.strip() for line in text.splitlines() if line.strip()
                              and not re.search(r'^(candidate-se|apply to|\d+\s+(?:de|of)\s+\d+|voltar$|avan.ar$|back$|next$|adicionar mais$|add more$)',line.strip(),re.I))
            question={'label':'Confirma estes dados do teu perfil LinkedIn para candidaturas?',
                      'type':'checkbox','required':True,'options':[],'profile_text':content}
            answer=self.answers.resolve(question,self.job)
            self.counters[answer['source']]+=1
            if answer.get('value') is not True:
                raise SafetyError('Read-only LinkedIn profile details require confirmation in Q&A')
            self.used.append({'question':question['label'],'profile_text':content,'value':True,'refs':answer['refs'],'source':answer['source']})
        separate_country=any(normalized(f['label']) in ('country code','phone country code','codigo do pais','codigo de pais') for f in fields)
        pending = []
        for field in fields:
            if field['type']=='tel':field['separate_country_code']=separate_country
            if field['type']=='resume-choice':
                if not self.resume_verified:pending.append('Configured resume must be uploaded and verified')
                continue
            if field['type'] == 'file':
                if not re.search(r'resume|curr[ií]culo|cv\b', field['label'], re.I):
                    if field['required']: pending.append('Unsupported mandatory document')
                    continue
                target = modal.locator('[data-langhire-field="'+field['targets'][0]+'"]')
                await target.set_input_files(str(self.resume))
                names = await target.evaluate('e=>[...e.files].map(f=>f.name)')
                if self.resume.name not in names: raise SafetyError('Resume upload not accepted')
                self.resume_verified = True
                continue
            if field['type'] not in ('text', 'email', 'tel', 'url', 'number', 'textarea', 'select-one', 'radio', 'checkbox', 'autocomplete', 'search'):
                if field['required']: pending.append('Unsupported control: ' + field['label'])
                continue
            # Never trust a mandatory prefilled answer without confirmed provenance.
            if (not field['required'] and field['value'] in ('',False,[])
                    and self.answers.direct(field,self.answers.context(field['label'],self.job)) is None):
                continue
            answer = self.answers.resolve(field, self.job)
            self.counters[answer['source']] += 1
            if 'pending' in answer:
                if field['required'] or field['value'] not in ('', False, []): pending.append(str(answer['pending']))
                continue
            value = await self.apply_field(modal, field, answer)
            self.used.append({'question': field['label'], 'type': field['type'], 'value': value,
                              'refs': answer['refs'], 'source': answer['source']})
        if pending: raise SafetyError('Unresolved fields: ' + ', '.join(pending))
        remaining = await self.inspect(modal)
        for field in remaining:
            if field['type']=='resume-choice':continue
            if field['required'] and (field['value'] in ('',None,[]) or (field['type']=='checkbox' and not field['value'])):
                raise SafetyError('Mandatory control not accepted: ' + field['label'])
        return fields

    async def snapshot(self, modal):
        await self.inspect(modal)
        # Include the portal's actual review text, not only values we intended to type.
        text = normalized(await modal.inner_text())
        return {'job_url': self.job['url'], 'title': self.job.get('title', ''), 'company': self.job.get('company', ''),
                'review_text': text, 'answers': self.used, 'resume_hash': self.resume_hash,
                'qualification': self.job.get('qualification')}

    async def run(self, resume, execution='review', review_id=None):
        self.resume = Path(resume)
        if not self.resume.is_file() or self.resume.suffix.lower() != '.pdf': raise SafetyError('Configured PDF resume missing')
        self.resume_hash = hashlib.sha256(self.resume.read_bytes()).hexdigest()
        self.job['url'] = canonical(self.job['url'])
        self.stage('open_job')
        await self.page.goto(self.job['url'], wait_until='domcontentloaded', timeout=60000)
        if re.search(r'/(login|checkpoint|authwall|uas/)', self.page.url):
            self.store.suspend('Login or account restriction; human intervention required')
            raise SafetyError('LinkedIn login required')
        if canonical(self.page.url) != self.job['url']: raise SafetyError('Job identity changed')
        actual_title = (await read_job_metadata(self.page))['title']
        if self.job.get('title') and normalized(actual_title) != normalized(self.job['title']):
            raise SafetyError('Job title changed; qualify the current listing again')
        button = self.page.get_by_role('button', name=EASY).first
        await button.wait_for(timeout=15000)
        self.stage('open_modal')
        await button.click()
        modal = await self.modal()
        seen = set()
        for step in range(15):
            self.stage('inspect_step')
            await self.wait_stage(modal)
            if urlsplit(self.page.url).hostname not in ('linkedin.com', 'www.linkedin.com'): raise SafetyError('External redirect unsupported')
            fields = await self.fill(modal)
            (self.directory/f'step-{step:02}.json').write_text(json.dumps({'fields':fields,'accepted_answers':self.used},ensure_ascii=False,indent=2),encoding='utf-8')
            field_signature = digest([{'label': f['label'], 'type': f['type'], 'options': f['options']} for f in fields])
            signature = digest({'fields':field_signature,'progress':re.findall(r'\d+\s+(?:de|of)\s+\d+',await modal.inner_text(),re.I)})
            final = modal.get_by_role('button', name=SUBMIT)
            if await final.count() and await final.first.is_visible():
                if not self.resume_verified:
                    raise SafetyError('No verified configured resume upload; existing document cannot be trusted by name alone')
                if normalized(self.resume.name) not in normalized(await modal.inner_text()):
                    raise SafetyError('Configured resume is absent from the portal review')
                self.stage('review')
                snapshot = await self.snapshot(modal)
                if AnswerBook(self.data).version != self.answers.version:
                    raise SafetyError('Candidate facts changed during preparation; review again')
                (self.directory/'review.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
                await self.page.screenshot(path=str(self.directory/'review.png'), full_page=True)
                if execution == 'review':
                    self.store.save(self.attempt, self.job, snapshot, self.answers.version, self.resume_hash)
                    self.store.attempts.transition(self.attempt, 'reviewed', 'Ready for individual review in the app; no submit click')
                    return 'reviewed'
                self.store.consume(review_id, self.job['url'], snapshot, self.answers.version, self.resume_hash)
                async def ready():
                    # The portal may replace the review with a spinner before fetch.
                    # The exact DOM review is checked immediately before arming the click.
                    unchanged = (AnswerBook(self.data).version == self.answers.version
                                 and hashlib.sha256(self.resume.read_bytes()).hexdigest()==self.resume_hash)
                    return {'ready': unchanged, 'reason': 'Review, facts or resume changed'}
                self.monitor.pre_submit_review = ready
                if digest(await self.snapshot(modal)) != digest(snapshot):
                    raise SafetyError('Review changed immediately before submit')
                self.monitor.arm_submission(self.job['url'])
                self.stage('submit')
                await final.first.click()
                result = await self.monitor.finalize()
                return result['state']
            if signature in seen: raise SafetyError('Repeated form step; stopped without submitting')
            seen.add(signature)
            next_button = modal.get_by_role('button', name=NEXT)
            if not await next_button.count(): next_button = modal.get_by_role('button', name=REVIEW)
            if not await next_button.count(): raise SafetyError('Unknown form stage')
            self.stage('advance')
            before = re.findall(r'\d+\s+(?:de|of)\s+\d+',await modal.inner_text(),re.I)
            await next_button.first.click()
            for _ in range(100):
                await asyncio.sleep(0.2)
                await self.inspect(modal)
                current=await modal.evaluate(FIELDS)
                current_signature=digest([{'label':f['label'],'type':f['type'],'options':f['options']} for f in current])
                progress=re.findall(r'\d+\s+(?:de|of)\s+\d+',await modal.inner_text(),re.I)
                controls=modal.get_by_role('button',name=re.compile(NEXT.pattern+'|'+REVIEW.pattern+'|'+SUBMIT.pattern,re.I))
                has_final=await modal.get_by_role('button',name=SUBMIT).count()
                if await controls.count() and await controls.first.is_enabled() and (current_signature!=field_signature or progress!=before or has_final):break
            else: raise SafetyError('Stage did not advance')
        raise SafetyError('Form stage limit reached')


async def execute_easy(job, data, resume, execution='review', review_id=None, status=None):
    data = Path(data)
    with FileLock(str(data/'application-worker.lock'), timeout=0):
        store = AttemptStore(data)
        store.recover()
        easy = EasyStore(data)
        if easy.channel()['suspended']: raise SafetyError(easy.channel()['reason'])
        job = dict(job, url=canonical(job['url']))
        attempt = store.begin(job['url'])
        try:
            async with persistent_browser(data) as session:
                monitor = SubmissionMonitor(store, attempt, cancel=status)
                executor = None
                try:
                    await monitor.attach(session)
                    context = monitor.contexts[0]
                    page = context.pages[0] if context.pages else await context.new_page()
                    executor = EasyExecutor(page, data, job, attempt, monitor, status=status)
                    await executor.run(resume, execution, review_id)
                except BaseException as error:
                    store.event(attempt, 'easy_error', {'type': type(error).__name__, 'message': str(error)[:1000]})
                    try: await monitor.capture('failure')
                    except Exception as capture_error:
                        store.event(attempt,'capture_error',{'stage':'failure','error':str(capture_error)[:300]})
                    if store.get(attempt)['state'] in ACTIVE:
                        store.transition(attempt, 'uncertain' if store.submissions(attempt) else 'pending', str(error)[:1000])
                    if isinstance(error, asyncio.CancelledError): raise
                finally:
                    if executor: store.event(attempt, 'answer_metrics', executor.counters)
                    await monitor.close()
        except BaseException as error:
            store.event(attempt,'easy_error',{'type':type(error).__name__,'message':str(error)[:1000]})
            if store.get(attempt)['state'] in ACTIVE:
                store.transition(attempt,'uncertain' if store.submissions(attempt) else 'pending',str(error)[:1000])
            if isinstance(error,asyncio.CancelledError):raise
        return store.get(attempt)
