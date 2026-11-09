"""The whole application queue filled in the candidate's own Chrome by the LangHire Apply Assist extension.

Nothing here drives a browser or clicks Submit. The extension inspects each form, asks this local server
for answers, fills what is confirmed, lists what is left for the candidate, and reports the page the portal
shows after the candidate's own Submit click. Lever, Greenhouse, Gupy, Ashby, Workable and LinkedIn Easy Apply share one chain;
there is no daily ceiling because every send is a human click."""
import asyncio
import hashlib
import json
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .application_safety import AttemptStore, SafetyError, job_key
from .easy_answers import AnswerBook, normalized
from .submission_monitor import ERROR, SUCCESS

PORT = 8799
# Fixed by the "key" in tools/apply-assist-extension/manifest.json.
EXTENSION_ORIGIN = 'chrome-extension://flpmcolipdjehnjmhpkcamihempmdcic'
BOOKMARKLET_ORIGINS = ('https://jobs.lever.co', 'https://jobs.eu.lever.co')
CONFIRMED = re.compile(SUCCESS.pattern + r'|application (?:was )?sent|sua candidatura foi enviada|candidatura enviada'
                       r'|your application was already submitted|we received your previous application', re.I)
SUPPORTED = ('text','email','tel','number','url','textarea','select-one','checkbox','radio','checkbox-group','search','autocomplete')
PLATFORMS = ('lever', 'greenhouse', 'gupy', 'ashby', 'workable', 'linkedin')
METRICS = lambda: {'rule':0,'cache':0,'pending':0,'model':0,'autonomous':0}


def platform_of(url):
    host = (urlsplit(url).hostname or '').lower().removeprefix('www.')
    if host in ('jobs.lever.co', 'jobs.eu.lever.co'):return 'lever'
    if host in ('job-boards.greenhouse.io', 'boards.greenhouse.io'):return 'greenhouse'
    if host == 'linkedin.com':return 'linkedin'
    if host == 'jobs.ashbyhq.com':return 'ashby'
    if host == 'apply.workable.com':return 'workable'
    if re.fullmatch(r'(?!login\.|portal\.|www\.)[a-z0-9-]+\.gupy\.io', host):return 'gupy'
    raise SafetyError('Unsupported page for Apply Assist')


def canonical(url):
    platform = platform_of(url)
    if platform == 'linkedin':
        from .easy_apply import canonical as linkedin
        match = re.search(r'/jobs/view/(\d+)', urlsplit(url).path)
        return linkedin('https://www.linkedin.com/jobs/view/'+match[1]+'/') if match else linkedin(url)
    from .external_apply import canonical_ats
    url = url.split('?')[0].split('#')[0]
    if platform == 'gupy':url = re.sub(r'/candidates/jobs/(\d+)(?:/.*)?$', r'/jobs/\1', url)
    url = re.sub(r'/confirmation/?$', '', url) if platform == 'greenhouse' else re.sub(r'/thanks/?$', '', url)
    return canonical_ats(url)


def token(data):
    from .lever_assist import token as shared
    return shared(data)


class Assistant:
    def __init__(self, data, resume, platforms=PLATFORMS):
        self.data = Path(data);self.resume = Path(resume);self.store = AttemptStore(self.data)
        if not self.resume.is_file() or self.resume.suffix.lower() != '.pdf':raise SafetyError('Configured PDF missing')
        self.platforms = tuple(platforms)
        self.skipped_path = self.data/'external'/'assist-skipped.json'
        self.jobs = {}
        # Resume built for each planned job; the extension fetches the one matching its page.
        self.tailored = {};self.current = self.resume

    async def prepare_resume(self, job):
        """This job's tailored PDF when tailoring is on; the configured PDF otherwise or when the build fails."""
        from .resume_tailoring import resume_for
        path = await resume_for(self.data, job, self.resume)
        self.tailored[job_key(job['url'])] = path;self.current = path
        return path

    def resume_bytes(self, url=None):
        path = None
        if url:
            try:path = self.tailored.get(job_key(canonical(url)))
            except SafetyError:path = None
        return (path or self.current).read_bytes()

    def skipped(self):
        keys = set()
        for path in (self.skipped_path, self.data/'external'/'lever-assist-skipped.json'):
            if path.exists():keys |= set(json.loads(path.read_text(encoding='utf-8')))
        return keys

    def linkedin_jobs(self):
        from .shared_config import read_jobs
        from .easy_service import qualification_current
        protected = {job_key(a['job_url']) for a in self.store.list() if a['state'] in ('confirmed','uncertain','running','submitted')}
        jobs = []
        for url, job in read_jobs().items():
            if job.get('easy_apply') is not True or job.get('status') in ('applied', 'uncertain'):continue
            qualification = job.get('qualification') or {}
            if qualification.get('state') != 'eligible' or not qualification_current(self.data, qualification):continue
            try:url = canonical(url)
            except SafetyError:continue
            if job_key(url) in protected:continue
            jobs.append(dict(job, url=url, platform='linkedin', eligibility_country=qualification.get('country', ''),
                             form_language=qualification.get('language', 'unknown')))
        return jobs

    def queue(self):
        from .external_service import queue
        external = queue(self.data)
        jobs = []
        for platform in self.platforms:
            jobs += self.linkedin_jobs() if platform == 'linkedin' else [j for j in external if j['platform'] == platform]
        skipped = self.skipped()
        jobs = [j for j in jobs if job_key(j['url']) not in skipped]
        self.jobs.update({job_key(j['url']):j for j in jobs})
        return jobs

    @staticmethod
    def opening(job):
        url = job['url']+{'lever':'/apply', 'ashby':'/application', 'workable':'/apply'}.get(job['platform'], '')
        return url + ('&' if '?' in url else '?') + 'lh_assist=1'

    def next(self):
        jobs = self.queue()
        counts = {p:sum(j['platform'] == p for j in jobs) for p in self.platforms}
        if not jobs:return {'url':None, 'remaining':0, 'by_platform':counts}
        return {'url':self.opening(jobs[0]), 'platform':jobs[0]['platform'], 'remaining':len(jobs), 'by_platform':counts}

    def job(self, url):
        key = job_key(canonical(url))
        job = self.jobs.get(key) or next((j for j in self.queue() if job_key(j['url']) == key), None)
        if job is None:raise SafetyError('Vaga fora da fila elegível (já enviada, incerta, pulada ou desqualificada)')
        return job

    def waiting(self, url):
        return next((a for a in self.store.list('awaiting_human') if job_key(a['job_url']) == job_key(url)), None)

    def attempt(self, url):
        attempt = self.waiting(url)
        if attempt:return attempt['id']
        aid = self.store.begin(url)
        self.store.transition(aid, 'awaiting_human', 'Preenchido no Chrome do candidato pela extensão; CAPTCHA e envio são dele')
        return aid

    async def answer(self, book, field, job, metrics):
        from .automatic_answers import resolve_answer
        if not field.get('required') and field.get('value') in ('', False, [], None):
            answer = book.direct(field, book.context(field['label'], job))
            if answer is None:return None
            return answer
        return await resolve_answer(book, field, job, metrics)

    async def plan(self, url, fields, step=0):
        job = self.job(url);platform = job['platform']
        job = dict(job, url=canonical(url))
        job.setdefault('eligibility_country', 'BR');job.setdefault('form_language', 'en' if platform == 'lever' else 'unknown')
        book = AnswerBook(self.data);metrics = METRICS()
        resume = await self.prepare_resume(job)
        if platform == 'greenhouse':fills, pending = await self.greenhouse(book, job, fields, metrics)
        else:fills, pending = await self.generic(book, job, fields, metrics, platform)
        aid = self.attempt(job['url'])
        self.store.event(aid, 'assisted_fill', {'platform':platform, 'step':step,
            'answers':[{'question':f['label'],'value':f['value'],'source':f.get('source','rule')} for f in fills],
            'pending':pending, 'metrics':metrics, 'resume':{'name':resume.name, 'path':str(resume),
            'sha256':hashlib.sha256(resume.read_bytes()).hexdigest()}})
        return {'fills':fills, 'pending':pending, 'title':job.get('title'), 'company':job.get('company'), 'platform':platform}

    async def generic(self, book, job, fields, metrics, platform):
        """Lever forms and LinkedIn Easy Apply steps: controls read from the live DOM."""
        fills = [];pending = []
        separate_country = any(normalized(f['label']) in ('country code','phone country code','codigo do pais','codigo de pais') for f in fields)
        for field in fields:
            kind = field['type']
            if kind == 'tel':field = dict(field, separate_country_code=separate_country)
            if kind == 'resume-choice':continue  # The extension uploads the configured PDF instead of trusting a stored one.
            if kind == 'file':
                purpose = normalized(' '.join(str(field.get(k,'')) for k in ('id','name','label')))
                if re.search(r'resume|curriculo|\bcv\b', purpose) and not re.search(r'cover|carta|additional|outro', purpose):
                    fills.append(dict(field, value=self.current.name))
                elif field['required']:pending.append(field['label'])
                continue
            if kind == 'location':
                city = book.facts.get('location.city',{}).get('value')
                if city:fills.append(dict(field, value=city, state=book.facts.get('location.state',{}).get('value')))
                elif field['required']:pending.append(field['label'])
                continue
            if kind not in SUPPORTED:
                if field['required']:pending.append(field['label'] or 'Controle sem rótulo')
                continue
            if kind == 'autocomplete':field = dict(field, type='text')
            answer = await self.answer(book, field, job, metrics)
            if answer is None:continue
            metrics[answer.get('source', 'rule')] = metrics.get(answer.get('source', 'rule'), 0)+1
            if 'pending' in answer:
                if field['required'] or field.get('value') not in ('', False, [], None):pending.append(field['label'])
                continue
            fill = dict(field, type=kind, value=answer['value'], source=answer['source'])
            if kind == 'autocomplete' and 'location.city' in answer.get('refs', []):
                fill['state'] = book.facts.get('location.state',{}).get('value', '')
            fills.append(fill)
        return fills, pending

    async def greenhouse(self, book, job, present, metrics):
        """Greenhouse controls come from the board's public schema; the page reports which ids it rendered."""
        from .greenhouse_apply import fetch_schema, schema_fields
        schema = await fetch_schema(job)
        job['form_language'] = schema.get('language') or job.get('form_language', 'unknown')
        rendered = {f['id']:f for f in present}
        fills = [];pending = []
        for field in schema_fields(schema):
            live = rendered.get(field['id'])
            if live is None:
                if field['required']:pending.append(field['label'])
                continue
            field = dict(field, value=live.get('value', ''), render=live.get('render', ''))
            if field['type'] == 'file':
                if field['id'] == 'resume':fills.append(dict(field, value=self.current.name, targets=[field['id']]))
                elif field['required']:pending.append(field['label'])
                continue
            if field['id'] == 'phone' and not field['required']:continue
            if field['type'] == 'location':
                city = book.facts.get('location.city',{}).get('value')
                if city:fills.append(dict(field, value=city, state=book.facts.get('location.state',{}).get('value', ''), targets=[field['id']]))
                elif field['required']:pending.append(field['label'])
                continue
            if field['type'] not in ('text','textarea','select-one','checkbox','checkbox-group','phone-country','location'):
                if field['required']:pending.append(field['label'])
                continue
            answer = await self.answer(book, field, job, metrics)
            if answer is None:continue
            metrics[answer.get('source', 'rule')] = metrics.get(answer.get('source', 'rule'), 0)+1
            if 'pending' in answer:
                if field['required']:pending.append(field['label'])
                continue
            value = answer['value'];texts = {o['value']:o['text'] for o in field['options']}
            fill = dict(field, value=value, source=answer['source'], targets=[field['id']])
            if field['type'] == 'select-one':fill['text'] = texts.get(str(value), str(value))
            if field['type'] == 'checkbox-group':fill['text'] = [texts.get(str(v), str(v)) for v in value]
            if field['type'] == 'location':fill['state'] = book.facts.get('location.state',{}).get('value', '')
            fills.append(fill)
        return fills, pending

    def done(self, url, text, uncertain=False):
        url = canonical(url);attempt = self.waiting(url);state = None
        if attempt:
            self.store.event(attempt['id'], 'assisted_portal_page', {'url':url, 'text':text[:4000], 'candidate_marked_uncertain':uncertain})
            if uncertain:self.store.transition(attempt['id'], 'uncertain', 'Candidate reported no portal confirmation after submitting; do not resend')
            elif CONFIRMED.search(text):self.store.transition(attempt['id'], 'confirmed', 'Confirmation shown in the candidate\'s Chrome')
            elif ERROR.search(text):self.store.transition(attempt['id'], 'uncertain', 'Portal reported an error after the candidate\'s submit; do not resend')
            else:self.store.transition(attempt['id'], 'uncertain', 'Candidate submitted without a recognizable confirmation page')
            result = self.store.get(attempt['id']);state = result['state']
            self.project(url, result)
            self.interview_sheet(url)
        return dict(self.next(), state=state)

    def project(self, url, result):
        job = self.jobs.get(job_key(url))
        if job is None:return
        status = 'applied' if result['state'] == 'confirmed' else 'uncertain'
        if job['platform'] == 'linkedin':
            from .shared_config import read_jobs, update_job
            for stored in read_jobs():
                try:same = job_key(canonical(stored)) == job_key(url)
                except SafetyError:same = False
                if same:update_job(stored, status=status, easy_result=result['state'], attempt_id=result['id'],
                                   evidence_dir=result['evidence_dir'], error=result['message'],
                                   applied_at=result['updated_at'] if result['state'] == 'confirmed' else None)
        else:
            from .external_service import update_projection
            update_projection(job, result, self.data)

    def interview_sheet(self, url):
        """Refresh the job's interview sheet now that its answers were sent; never blocks the queue."""
        from .resume_tailoring import tailoring_enabled, write_sheet
        job = self.jobs.get(job_key(url))
        if job is None or not tailoring_enabled(self.data):return
        try:write_sheet(self.data, dict(job, url=url))
        except Exception as error:print(json.dumps({'interview_sheet_failed':type(error).__name__, 'url':url}), flush=True)

    def submitted(self, url):
        attempt = self.waiting(canonical(url))
        if attempt:self.store.event(attempt['id'], 'assisted_submit_click', {'url':canonical(url)})
        return {'recorded':bool(attempt)}

    def recover(self):
        """Pages abandoned before Submit go back to the queue; a Submit click without an outcome is never resent."""
        recovered = {'pending':0, 'uncertain':0}
        waiting = self.store.list('awaiting_human')
        with self.store.db() as db:
            rows = [(a, {r[0] for r in db.execute('SELECT kind FROM events WHERE attempt_id=?', (a['id'],))}) for a in waiting]
        for attempt, seen in rows:
            if 'assisted_fill' not in seen or self.store.submissions(attempt['id']):continue
            if 'assisted_submit_click' in seen:
                self.store.transition(attempt['id'], 'uncertain', 'Candidate clicked Submit but no outcome was reported; do not resend')
                recovered['uncertain'] += 1
            else:
                self.store.transition(attempt['id'], 'pending', 'Assisted page closed before Submit; back in the queue')
                recovered['pending'] += 1
        return recovered

    def skip(self, url):
        key = job_key(canonical(url));skipped = set()
        if self.skipped_path.exists():skipped = set(json.loads(self.skipped_path.read_text(encoding='utf-8')))
        self.skipped_path.parent.mkdir(parents=True, exist_ok=True)
        self.skipped_path.write_text(json.dumps(sorted(skipped|{key}), indent=2), encoding='utf-8')
        attempt = self.waiting(canonical(url))
        if attempt:self.store.transition(attempt['id'], 'pending', 'Pulada pelo candidato antes do envio')
        return self.next()


def serve(data, resume, open_first=True, platforms=PLATFORMS):
    from . import lever_assist
    assistant = Assistant(data, resume, platforms);secret = token(data);lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):pass

        def origin(self):return self.headers.get('Origin', '')

        def cors(self):
            if self.origin() in BOOKMARKLET_ORIGINS+(EXTENSION_ORIGIN,):
                self.send_header('Access-Control-Allow-Origin', self.origin())
                self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-LangHire-Assist')
                self.send_header('Access-Control-Allow-Private-Network', 'true')

        def reply(self, status, body, kind='application/json'):
            data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode()
            self.send_response(status);self.cors()
            self.send_header('Content-Type', kind);self.send_header('Content-Length', str(len(data)));self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):
            self.send_response(204);self.cors();self.send_header('Access-Control-Allow-Methods', 'GET, POST');self.end_headers()

        def allowed(self):
            # Pages cannot forge Origin; only this extension id or the Lever bookmarklet with its token gets answers.
            return self.origin() == EXTENSION_ORIGIN or (self.origin() in BOOKMARKLET_ORIGINS and self.headers.get('X-LangHire-Assist') == secret)

        def do_GET(self):
            path = self.path.split('?')[0]
            if path == '/':
                return self.reply(200, '<p>LangHire Apply Assist ativo. Extensão: tools/apply-assist-extension. '
                                  'Favorito só para Lever: <a href="'+lever_assist.bookmarklet().replace('"','&quot;')+'">Preencher LangHire</a></p>',
                                  'text/html; charset=utf-8')
            if path == '/assist.js':return self.reply(200, lever_assist.script(secret), 'text/javascript; charset=utf-8')
            if path == '/go':
                with lock:url = assistant.next()['url']
                if not url:return self.reply(200, 'Fila vazia.', 'text/plain; charset=utf-8')
                self.send_response(302);self.send_header('Location', url);self.end_headers();return
            if not self.allowed():return self.reply(403, {'error':'forbidden'})
            with lock:
                if path == '/next':return self.reply(200, assistant.next())
                if path == '/resume':
                    url = parse_qs(urlsplit(self.path).query).get('url', [None])[0]
                    return self.reply(200, assistant.resume_bytes(url), 'application/pdf')
            self.reply(404, {'error':'not found'})

        def do_POST(self):
            if not self.allowed():return self.reply(403, {'error':'forbidden'})
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            path = self.path.split('?')[0]
            try:
                with lock:
                    if path == '/next':return self.reply(200, assistant.next())
                    if path == '/resume':return self.reply(200, assistant.resume_bytes(body.get('url')), 'application/pdf')
                    if path == '/status':
                        try:job = assistant.job(body['url']);return self.reply(200, {'queued':True, 'platform':job['platform']})
                        except SafetyError:return self.reply(200, {'queued':False})
                    if path == '/plan':return self.reply(200, asyncio.run(assistant.plan(body['url'], body['fields'], body.get('step', 0))))
                    if path == '/done':return self.reply(200, assistant.done(body['url'], body.get('text', ''), body.get('uncertain') is True))
                    if path == '/submitted':return self.reply(200, assistant.submitted(body['url']))
                    if path == '/skip':return self.reply(200, assistant.skip(body['url']))
            except SafetyError as error:return self.reply(409, {'error':str(error)})
            self.reply(404, {'error':'not found'})

    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    recovered = assistant.recover()
    first = assistant.next()
    print(json.dumps({'setup':f'http://127.0.0.1:{PORT}/', 'remaining':first['remaining'], 'by_platform':first['by_platform'],
                      'recovered':recovered, 'first':first['url']}, ensure_ascii=False), flush=True)
    if open_first and first['url']:webbrowser.open(first['url'])
    server.serve_forever()


EXTENSION = Path(__file__).resolve().parents[2]/'tools'/'apply-assist-extension'


def extension_fields():
    """The extension's field inspectors are the executors' own, generated so the two never drift."""
    from .external_apply import FIELDS as LEVER
    from .easy_apply import FIELDS as LINKEDIN
    return ('// Generated by `python -m cli.external assist --write-extension`; do not edit.\n'
            'globalThis.LH_FIELDS = {\nlever: ' + LEVER.strip() + ',\nlinkedin: ' + LINKEDIN.strip() + '\n};\n')


def write_extension():
    (EXTENSION/'fields.js').write_text(extension_fields(), encoding='utf-8', newline='\n')
    return EXTENSION
