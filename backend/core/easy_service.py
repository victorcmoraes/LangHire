"""App service for the bounded Easy Apply queue; all browser work uses one lease."""
import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

from .application_safety import SafetyError
from .browser_runtime import persistent_browser
from .easy_apply import EasyStore, canonical, check_session, execute_easy, EASY, read_job_metadata
from .easy_answers import AnswerBook


async def collect(data, profile, title, limit, filters, status):
    from .shared_config import read_jobs, add_collected_job
    data = Path(data)
    limit = min(limit or 10, 10)
    book = AnswerBook(data)
    city = book.facts.get('location.city', {}).get('value', '')
    country = book.facts.get('location.country', {}).get('value', '')
    params = {'keywords': title or 'backend developer', 'location': country or city, 'f_AL': 'true'}
    for key, param in {'date_posted':'f_TPR','experience_level':'f_E','work_type':'f_WT','job_type':'f_JT'}.items():
        if filters.get(key): params[param] = str(filters[key])
    jobs = read_jobs()
    existing = set()
    for url in jobs:
        try: existing.add(canonical(url))
        except SafetyError: pass
    async with persistent_browser(data) as session:
        from playwright.async_api import async_playwright
        await session.start()
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(session.cdp_url)
            page = browser.contexts[0].pages[0]
            try:
                await check_session(page)
            except Exception:
                EasyStore(data).suspend('Login verification failed; open LinkedIn login from the app')
                raise
            await page.goto('https://www.linkedin.com/jobs/search/?'+urlencode(params),wait_until='domcontentloaded')
            await page.wait_for_timeout(2000)
            links = await page.locator('a[href*="/jobs/view/"]').evaluate_all('es=>[...new Set(es.map(e=>e.href))]')
            for raw in links[:30]:
                if status.get('cancel_requested') or status.get('collected',0)>=limit: break
                try: url = canonical(raw)
                except SafetyError: continue
                if url in existing: continue
                await page.goto(url,wait_until='domcontentloaded')
                button = page.get_by_role('button',name=EASY)
                try: await button.first.wait_for(timeout=15000)
                except Exception: continue
                metadata=await read_job_metadata(page)
                title_text,text,location_text,company=(metadata[k] for k in ('title','description','location','company'))
                # Collection never asserts that an unreviewed description proves eligibility.
                jobs[url] = {'url':url,'title':title_text,'company':company,'location':location_text,
                             'description':text,'easy_apply':True,'easy_verified':True,'status':'blocked',
                             'easy_result':'qualification_pending','error':'Review mandatory requirements and geographic eligibility before application',
                             'source':'linkedin','search_title':title or '', 'qualification':None}
                existing.add(url)
                status['collected'] = status.get('collected',0)+1
                if not add_collected_job(jobs[url]):
                    status['collected']-=1
                    continue
                print('Collected Easy Apply:',title_text,flush=True)


async def run_jobs(data, body, status):
    store=EasyStore(data).attempts
    before={a['id'] for a in store.list()}
    started=time.monotonic()
    try:
        return await _run_jobs(data,body,status)
    finally:
        rows=[a for a in store.list() if a['id'] not in before]
        report={'execution':body.execution,'seconds':round(time.monotonic()-started,2),
                'attempts':[{'id':a['id'],'job_url':a['job_url'],'state':a['state'],'message':a['message'],'evidence_dir':a['evidence_dir']} for a in rows],
                'results':{s:sum(a['state']==s for a in rows) for s in ['reviewed','pending','confirmed','rejected','uncertain']},
                'submit_intents':sum(len(store.submissions(a['id'])) for a in rows),'answers':{'rule':0,'cache':0,'pending':0,'model':0}}
        with store.db() as db:
            for a in rows:
                for event in db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='answer_metrics'",(a['id'],)):
                    for key,value in json.loads(event[0]).items():report['answers'][key]+=value
        target=Path(data)/'reports';target.mkdir(exist_ok=True)
        path=target/(uuid.uuid4().hex+'.json');path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        status['report_path']=str(path)
        print('Easy Apply report:',path,report['results'],flush=True)


async def _run_jobs(data, body, status):
    from .config import load_settings
    from .shared_config import read_jobs, update_job
    jobs = read_jobs()
    urls = [body.job_url] if body.job_url else body.job_urls or [u for u,j in jobs.items() if j.get('status')=='pending' and j.get('easy_apply') is True]
    if body.execution == 'submit' and (len(urls)!=1 or not body.review_id): raise SafetyError('Individual approved review is required')
    for url in urls[:min(body.limit or 10,10)]:
        if status.get('cancel_requested'): break
        job = jobs.get(url)
        if not job or job.get('easy_apply') is not True: continue
        if job.get('status') in ('applied','uncertain'): continue
        qualification = job.get('qualification')
        if not qualification or qualification.get('state') != 'eligible' or not qualification_current(data,qualification):
            status.update(stage='qualification_pending',job_url=url)
            continue
        job = dict(job,eligibility_country=qualification.get('country',''),form_language=qualification.get('language','unknown'))
        settings = load_settings()
        for retry in range(3):
            result = await execute_easy(job,data,settings.get('resume_path',''),body.execution,body.review_id,status)
            store=EasyStore(data).attempts
            with store.db() as db:
                event=db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='easy_error' ORDER BY id DESC LIMIT 1",(result['id'],)).fetchone()
            error_type=json.loads(event[0]).get('type') if event else None
            if (result['state']!='pending' or error_type not in ('TimeoutError','ConnectionError','OSError','PermissionError')
                    or store.submissions(result['id']) or retry==2):break
            await asyncio.sleep(2**(retry+1))
        outcome = result['state']
        update_job(url,status='applied' if outcome=='confirmed' else 'uncertain' if outcome=='uncertain' else 'blocked',
                   easy_result=outcome,attempt_id=result['id'],evidence_dir=result['evidence_dir'],error=result['message'],
                   applied_at=result['updated_at'] if outcome=='confirmed' else job.get('applied_at'))
        status.update(result=outcome,stage=outcome)


def approve_qualification(data, job, country, evidence, fact_refs, language, currency='', period=''):
    """Explicit human review of eliminatory criteria; no silent LLM qualification."""
    book = AnswerBook(data)
    if country != 'BR' or not evidence.strip() or not fact_refs or any(r not in book.facts for r in fact_refs):
        raise SafetyError('Brazil eligibility, requirement evidence and existing candidate fact references are required')
    if (currency and not re.fullmatch('[A-Z]{3}',currency)) or period not in ('','monthly','annual','hourly'):
        raise SafetyError('Invalid salary currency or period')
    from .shared_config import update_job
    store = EasyStore(data).attempts
    if any(a['state'] in ('confirmed','uncertain', 'submitted','running','awaiting_human') for a in store.list() if a['job_url']==canonical(job['url'])):
        raise SafetyError('Existing protected attempt cannot be reopened')
    update_job(job['url'],qualification={'state':'eligible','country':country,'language':language,
               'evidence':evidence,'fact_refs':fact_refs,'facts_version':book.reference_version(fact_refs),'source':'human'},
               salary_currency=currency,salary_period=period,status='pending',easy_result=None,error=None)


def qualification_current(data, qualification):
    return qualification.get('facts_version') == AnswerBook(data).reference_version(qualification.get('fact_refs',[]))
