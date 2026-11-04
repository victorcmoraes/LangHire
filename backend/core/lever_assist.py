"""Lever forms filled in the candidate's own Chrome; the candidate solves the CAPTCHA and clicks Submit.

Lever answered HTTP 400 ("error verifying your application") to submissions from the automated
browser even after a human CAPTCHA, so nothing here drives a browser: a bookmarklet asks this
local server for answers, fills the page, and reports the page the portal shows afterwards."""
import asyncio
import json
import re
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .application_safety import AttemptStore, SafetyError, job_key
from .easy_answers import AnswerBook, normalized
from .external_apply import FIELDS, canonical_ats
from .submission_monitor import ERROR

PORT = 8799
ORIGINS = ('https://jobs.lever.co', 'https://jobs.eu.lever.co')
CONFIRMED = re.compile(r'application (?:was |has been )?(?:submitted|received)|thank(?:s| you) for (?:applying|your application)', re.I)
SUPPORTED = ('text','email','tel','number','url','textarea','select-one','checkbox','radio','checkbox-group')


def token(data):
    path = Path(data)/'lever-assist-token'
    if not path.exists():path.write_text(secrets.token_hex(24), encoding='utf-8')
    return path.read_text(encoding='utf-8').strip()


class Assistant:
    def __init__(self, data, resume):
        self.data = Path(data);self.resume = Path(resume);self.store = AttemptStore(self.data)
        if not self.resume.is_file() or self.resume.suffix.lower() != '.pdf':raise SafetyError('Configured PDF missing')
        self.skipped_path = self.data/'external'/'lever-assist-skipped.json'
        self.jobs = {}

    def skipped(self):
        return set(json.loads(self.skipped_path.read_text(encoding='utf-8'))) if self.skipped_path.exists() else set()

    def queue(self):
        from .external_service import queue
        jobs = [j for j in queue(self.data) if j['platform'] == 'lever' and job_key(j['url']) not in self.skipped()]
        self.jobs.update({job_key(j['url']):j for j in jobs})
        return jobs

    def next(self):
        jobs = self.queue()
        return {'url':jobs[0]['url']+'/apply', 'remaining':len(jobs)} if jobs else {'url':None, 'remaining':0}

    def waiting(self, url):
        return next((a for a in self.store.list('awaiting_human') if job_key(a['job_url']) == job_key(url)), None)

    async def plan(self, url, fields):
        from .automatic_answers import resolve_answer
        url = canonical_ats(url)
        job = self.jobs.get(job_key(url)) or next((j for j in self.queue() if job_key(j['url']) == job_key(url)), None)
        if job is None:raise SafetyError('Vaga fora da fila elegível do Lever (já enviada, incerta ou desqualificada)')
        job = dict(job, url=url, eligibility_country='BR', form_language='en')
        book = AnswerBook(self.data);fills = [];pending = []
        metrics = {'rule':0,'cache':0,'pending':0,'model':0,'autonomous':0}
        for field in fields:
            if field['type'] == 'file':
                purpose = normalized(' '.join(str(field.get(k,'')) for k in ('id','name','label')))
                if re.search(r'resume|curriculo|\bcv\b', purpose) and not re.search(r'cover|carta|additional|outro', purpose):fills.append(dict(field, value=self.resume.name))
                elif field['required']:pending.append(field['label'])
                continue
            if field['type'] == 'location':
                city = book.facts.get('location.city',{}).get('value')
                if city:fills.append(dict(field, value=city, state=book.facts.get('location.state',{}).get('value')))
                elif field['required']:pending.append(field['label'])
                continue
            answer = None
            if not field['required'] and field['value'] in ('',False,[]):
                answer = book.direct(field, book.context(field['label'], job))
                if answer is None:continue
            if field['type'] not in SUPPORTED:
                if field['required']:pending.append(field['label'])
                continue
            answer = answer or await resolve_answer(book, field, job, metrics)
            if 'pending' in answer:pending.append(field['label']);continue
            fills.append(dict(field, value=answer['value'], source=answer['source']))
        attempt = self.waiting(url)
        if attempt is None:
            aid = self.store.begin(url)
            self.store.transition(aid, 'awaiting_human', 'Preenchido no Chrome do candidato; CAPTCHA e envio são dele')
        else:aid = attempt['id']
        self.store.event(aid, 'assisted_fill', {'answers':[{'question':f['label'],'value':f['value'],'source':f.get('source','rule')} for f in fills],
                                                 'pending':pending, 'metrics':metrics})
        return {'fills':fills, 'pending':pending, 'title':job.get('title'), 'company':job.get('company')}

    def done(self, url, text):
        attempt = self.waiting(url)
        if attempt:
            self.store.event(attempt['id'], 'assisted_portal_page', {'url':url, 'text':text[:4000]})
            if CONFIRMED.search(text):self.store.transition(attempt['id'], 'confirmed', 'Lever confirmation page in the candidate\'s Chrome')
            elif ERROR.search(text):self.store.transition(attempt['id'], 'uncertain', 'Portal reported an error after the candidate\'s submit; do not resend')
            else:self.store.transition(attempt['id'], 'uncertain', 'Candidate submitted without a recognizable confirmation page')
        return dict(self.next(), state=self.store.get(attempt['id'])['state'] if attempt else None)

    def skip(self, url):
        key = job_key(canonical_ats(url));skipped = self.skipped()|{key}
        self.skipped_path.write_text(json.dumps(sorted(skipped), indent=2), encoding='utf-8')
        attempt = self.waiting(url)
        if attempt:self.store.transition(attempt['id'], 'pending', 'Pulada pelo candidato antes do envio')
        return self.next()


def script(secret):
    return (Path(__file__).with_name('lever_assist.js').read_text(encoding='utf-8')
            .replace('__TOKEN__', secret).replace('__FIELDS__', FIELDS).replace('__PORT__', str(PORT)))


def bookmarklet():
    return ("javascript:(()=>{const s=document.createElement('script');"
            f"s.src='http://127.0.0.1:{PORT}/assist.js?'+Date.now();document.head.append(s)}})()")


def serve(data, resume, open_first=True):
    assistant = Assistant(data, resume);secret = token(data);lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):pass

        def cors(self):
            origin = self.headers.get('Origin', '')
            if origin in ORIGINS:
                self.send_header('Access-Control-Allow-Origin', origin)
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
            return self.headers.get('Origin', '') in ORIGINS and self.headers.get('X-LangHire-Assist') == secret

        def do_GET(self):
            path = self.path.split('?')[0]
            if path == '/':
                return self.reply(200, '<p>Arraste para a barra de favoritos: <a href="'+bookmarklet().replace('"','&quot;')+'">Preencher LangHire</a></p>', 'text/html; charset=utf-8')
            if path == '/assist.js':return self.reply(200, script(secret), 'text/javascript; charset=utf-8')
            if path == '/go':
                # A fixed address to get back into the chain from anywhere.
                with lock:url = assistant.next()['url']
                if not url:return self.reply(200, 'Fila do Lever vazia.', 'text/plain; charset=utf-8')
                self.send_response(302);self.send_header('Location', url);self.end_headers();return
            if not self.allowed():return self.reply(403, {'error':'forbidden'})
            with lock:
                if path == '/next':return self.reply(200, assistant.next())
                if path == '/resume':return self.reply(200, assistant.resume.read_bytes(), 'application/pdf')
            self.reply(404, {'error':'not found'})

        def do_POST(self):
            if not self.allowed():return self.reply(403, {'error':'forbidden'})
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            try:
                with lock:
                    if self.path == '/plan':return self.reply(200, asyncio.run(assistant.plan(body['url'], body['fields'])))
                    if self.path == '/done':return self.reply(200, assistant.done(body['url'], body.get('text', '')))
                    if self.path == '/skip':return self.reply(200, assistant.skip(body['url']))
            except SafetyError as error:return self.reply(409, {'error':str(error)})
            self.reply(404, {'error':'not found'})

    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    first = assistant.next()
    print(json.dumps({'setup':f'http://127.0.0.1:{PORT}/', 'remaining':first['remaining'], 'first':first['url']}, ensure_ascii=False), flush=True)
    if open_first and first['url']:webbrowser.open(first['url'])
    server.serve_forever()
