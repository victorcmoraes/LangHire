"""One-page resume per job, chosen from a master file of confirmed text.

The master (`<data>/resume/master.yaml`) holds every headline, summary, bullet and skill
the candidate stands behind. For a job, its title and description pick the track, the
bullets and the skill order; nothing outside the master is ever written. Each build
also leaves an interview sheet: what the job asks, what was claimed to the company,
and which master bullet backs each claim.
"""
from html import escape, unescape
import hashlib
import json
from pathlib import Path
import re

import yaml

from .application_safety import AttemptStore, SafetyError, job_key, now
from .company_claims import company_of
from .easy_answers import normalized

VERSION = 1
FILENAME = 'Victor-de-Moraes-CV.pdf'
STATUSES = ('confirmed', 'draft', 'off')

# What a tag means in a posting; any other tag matches as a whole word.
TAG_TERMS = {
    'backend': r'back ?end', 'frontend': r'front ?end', 'fullstack': r'full ?stack',
    'python': r'python|django|flask|fastapi', 'typescript': r'typescript', 'javascript': r'javascript',
    'node': r'\bnode\b|node ?js|nestjs|express', 'nestjs': r'nest ?js', 'java': r'\bjava\b(?! ?script)', 'spring': r'spring',
    'api': r'\bapis?\b|\brest(?:ful)?\b|graphql|webhook', 'graphql': r'graphql', 'webhooks': r'webhook',
    'integrations': r'integra\w*|ipaas|middleware|\berp\b|\bcrm\b', 'erp': r'\berp\b|totvs|omie|\bsap\b', 'crm': r'\bcrm\b|salesforce|hubspot',
    'sql': r'\bsql\b|banco de dados|database', 'postgres': r'postgres', 'oracle': r'oracle', 'sqlserver': r'sql server',
    'redis': r'redis', 'supabase': r'supabase', 'data': r'\bdados\b|\bdata\b|\betl\b|\belt\b', 'etl': r'\betl\b|\belt\b|data pipeline|pipeline de dados',
    'powerbi': r'power ?bi|business intelligence|\bbi\b', 'pandas': r'pandas',
    'qa': r'\bqa\b|quality|qualidade|\btest\w*|\bteste\w*|sdet', 'testing': r'\btest\w*|\bteste\w*', 'playwright': r'playwright',
    'e2e': r'\be2e\b|end to end|ponta a ponta', 'jest': r'\bjest\b', 'pytest': r'pytest',
    'cicd': r'ci ?cd|github actions|jenkins|gitlab|continuous integration|integracao continua', 'docker': r'docker|container',
    'devops': r'devops|\bsre\b|infraestrutura|infrastructure', 'cloud': r'\bcloud\b|\baws\b|azure|\bgcp\b|vercel', 'release': r'release|deploy',
    'react': r'react', 'angular': r'angular', 'nextjs': r'next ?js', 'mobile': r'mobile|react native|android|\bios\b',
    'automation': r'automa\w*|\brpa\b|scraping', 'ai': r'\bai\b|\bia\b|\bllms?\b|gen ?ai|machine learning',
    'finance': r'financ\w*|fintech|banc\w*|pagament\w*|payments?|credito|credit', 'hexagonal': r'hexagonal|clean architecture|\bddd\b|solid',
    'async': r'async\w*|assincron\w*|mensageria|\bfilas?\b|queue|kafka|rabbitmq|event', 'serverless': r'serverless|lambda',
}
PT_WORDS = r'\b(?:voce|vaga|requisitos|experiencia|conhecimento|desenvolvimento|para|com|sobre|nossa|atuar|beneficios|pessoa)\b'
EN_WORDS = r'\b(?:you|the|and|with|requirements|experience|knowledge|our|team|benefits|about|role|will)\b'


def master_path(data):
    return Path(data)/'resume'/'master.yaml'


def tailoring_enabled(data):
    path = Path(data)/'settings.json'
    settings = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    return (settings.get('resume_tailoring') or {}).get('enabled') is True and master_path(data).exists()


def load_master(data):
    path = master_path(data)
    if not path.exists():raise SafetyError('Resume master missing: '+str(path))
    master = yaml.safe_load(path.read_text(encoding='utf-8'))
    problems = []
    for key in ('person', 'headlines', 'summaries', 'experience', 'projects', 'education', 'skills'):
        if not master.get(key):problems.append('missing '+key)
    ids = set()
    for section in ('experience', 'projects'):
        for entry in master.get(section, []):
            for bullet in entry.get('bullets', []):
                # YAML 1.1 reads a bare `off` as false.
                if bullet.get('status') is False:bullet['status'] = 'off'
                for key in ('id', 'pt', 'en'):
                    if not bullet.get(key):problems.append(f"{entry.get('id')}: bullet without {key}")
                if bullet.get('status', 'draft') not in STATUSES:problems.append(f"{bullet.get('id')}: status must be one of {STATUSES}")
                if not isinstance(bullet.get('tags', []), list):problems.append(f"{bullet.get('id')}: tags must be a list")
                if bullet.get('id') in ids:problems.append(f"duplicate bullet id {bullet.get('id')}")
                ids.add(bullet.get('id'))
    if problems:raise SafetyError('Invalid resume master: '+'; '.join(problems))
    master['digest'] = hashlib.sha256(path.read_bytes()).hexdigest()
    return master


def plain(text):
    return normalized(unescape(re.sub(r'<[^>]+>', ' ', str(text or ''))))


def term(tag):
    return TAG_TERMS.get(tag) or r'\b'+re.escape(normalized(tag))+r'\b'


def job_terms(job):
    """Tags a posting mentions: 3 for its title, 1 for its description only."""
    title, body = plain(job.get('title')), plain(job.get('description'))
    def weight(tag):
        if re.search(term(tag), title):return 3
        return 1 if re.search(term(tag), body) else 0
    return weight


def language(job):
    """The posting's language; the form language (often a fixed default) only when the posting has no text."""
    text = plain((job.get('title') or '')+' '+(job.get('description') or ''))
    pt, en = len(re.findall(PT_WORDS, text)), len(re.findall(EN_WORDS, text))
    if pt == en and job.get('form_language') in ('pt', 'en'):return job['form_language']
    return 'pt' if pt > en else 'en'


def localized(value, lang):
    return value.get(lang) or value.get('en') or value.get('pt') if isinstance(value, dict) else value


def select(master, job):
    """Which master text this job gets, with the reason for each choice."""
    weight = job_terms(job);lang = language(job)
    score = lambda item:sum(weight(t) for t in item.get('tags', []))
    title = plain(job.get('title'))
    # The role name comes before the first "(", "|", "[" or " - "; what follows only qualifies it.
    role = plain(re.split(r'[(|\[]| [-–] ', str(job.get('title') or ''))[0])
    def track(item):
        """Role-name terms first, then any title term, then the description per tag, then the term named first."""
        hits = lambda text:[m.start() for t in item.get('tags', []) for m in [re.search(term(t), text)] if m]
        found = hits(title)
        return (len(hits(role)), len(found), round(score(item)/max(1, len(item.get('tags', []))), 3), -min(found, default=len(title)+1))
    headline = max(master['headlines'], key=lambda i:(track(i), -master['headlines'].index(i)))
    summary = next((s for s in master['summaries'] if s['id'] == headline['id']),
                   max(master['summaries'], key=lambda i:(track(i), -master['summaries'].index(i))))

    def bullets(entry):
        usable = [b for b in entry.get('bullets', []) if b.get('status', 'draft') != 'off']
        ranked = sorted(usable, key=lambda b:(not b.get('always'), -score(b), b.get('priority', 2), usable.index(b)))
        # A pinned bullet never loses to a better-scoring one when the page is trimmed.
        return [dict(b, score=99 if b.get('always') else score(b)) for b in ranked[:entry.get('show', 2)]]

    experience = [dict(e, chosen=bullets(e)) for e in master['experience']]
    projects = [p for p in master['projects'] if any(b.get('status', 'draft') != 'off' for b in p.get('bullets', []))]
    fixed = [p for p in projects if p.get('always')]
    others = sorted((p for p in projects if not p.get('always')),
                    key=lambda p:(-max((score(b) for b in p['bullets'] if b.get('status', 'draft') != 'off'), default=0), projects.index(p)))
    chosen_projects = [dict(p, chosen=bullets(p)) for p in fixed+others[:2]]

    asked = lambda item:bool(re.search(r'\b'+re.escape(normalized(localized(item, 'en')))+r'\b', plain((job.get('title') or '')+' '+(job.get('description') or ''))))
    skills = []
    for group in master['skills']:
        items = [i for i in group.get('items', [])]+[i for i in group.get('when_asked', []) if asked(i)]
        items.sort(key=lambda i:not asked(i))
        skills.append({'id':group['id'], 'label':localized(group['label'], lang), 'items':[localized(i, lang) for i in items],
                       'score':sum(asked(i) for i in items)})
    skills.sort(key=lambda g:-g['score'])
    used = [b for e in experience+chosen_projects for b in e['chosen']]
    return {'lang':lang, 'headline':headline['id'], 'summary':summary['id'], 'experience':experience, 'projects':chosen_projects,
            'skills':skills, 'drafts':[b['id'] for b in used if b.get('status', 'draft') == 'draft'],
            'job_tags':sorted(t for t in TAG_TERMS if weight(t)), 'master':master['digest']}


def trim(selection):
    """Drop the weakest bullet that is above its entry's minimum; False when nothing can go."""
    entries = [e for e in selection['experience']+selection['projects'] if len(e['chosen']) > e.get('min', 1)]
    if not entries:
        optional = [p for p in selection['projects'] if not p.get('always')]
        if not optional:return False
        selection['projects'].remove(optional[-1]);return True
    entry = min(entries, key=lambda e:min(b['score'] for b in e['chosen']))
    entry['chosen'].remove(min(entry['chosen'], key=lambda b:(b['score'], -b.get('priority', 2))))
    return True


LABELS = {'pt':{'summary':'Resumo', 'experience':'Experiência', 'projects':'Projetos', 'education':'Formação', 'skills':'Habilidades', 'languages':'Idiomas'},
          'en':{'summary':'Summary', 'experience':'Experience', 'projects':'Projects', 'education':'Education', 'skills':'Skills', 'languages':'Languages'}}
STYLE = '''@page{size:A4;margin:13mm 14mm}*{box-sizing:border-box}body{font-family:"Segoe UI",Arial,sans-serif;font-size:9.6pt;line-height:1.36;color:#111;margin:0}
h1{font-size:19pt;margin:0;letter-spacing:.2px}.headline{font-size:11pt;margin:2px 0 3px;color:#222}.contact{font-size:8.8pt;color:#333}
h2{font-size:10.4pt;text-transform:uppercase;letter-spacing:.6px;border-bottom:1px solid #999;margin:10px 0 4px;padding-bottom:1px}
.row{display:flex;justify-content:space-between;gap:12px}.row b{font-size:9.9pt}.meta{color:#333;white-space:nowrap}.sub{color:#333;font-style:italic}
ul{margin:2px 0 6px 15px;padding:0}li{margin:0 0 1.5px}p{margin:0 0 4px}.skills p{margin:0 0 2px}'''


def render_html(master, selection):
    lang = selection['lang'];L = LABELS[lang];e = lambda s:escape(str(s))
    person = master['person']
    headline = next(h for h in master['headlines'] if h['id'] == selection['headline'])
    summary = next(s for s in master['summaries'] if s['id'] == selection['summary'])
    contact = ' · '.join([localized(person['location'], lang), person['email']]+person.get('links', []))
    parts = [f'<h1>{e(person["name"])}</h1><div class="headline">{e(headline[lang])}</div><div class="contact">{e(contact)}</div>',
             f'<h2>{L["summary"]}</h2><p>{e(summary[lang])}</p>', f'<h2>{L["experience"]}</h2>']
    for entry in selection['experience']:
        period = f'{localized(entry["start"], lang)} – {localized(entry["end"], lang)}'
        parts.append(f'<div class="row"><b>{e(localized(entry["role"], lang))} · {e(entry["company"])}</b><span class="meta">{e(period)}</span></div>'
                     f'<div class="sub">{e(localized(entry["location"], lang))}</div><ul>'+''.join(f'<li>{e(b[lang])}</li>' for b in entry['chosen'])+'</ul>')
    parts.append(f'<h2>{L["projects"]}</h2>')
    for entry in selection['projects']:
        link = f' · {e(entry["link"])}' if entry.get('link') else ''
        parts.append(f'<div class="row"><b>{e(localized(entry["name"], lang))}</b><span class="meta">{e(entry.get("stack", ""))}</span></div>'
                     +(f'<div class="sub">{link[3:]}</div>' if link else '')+'<ul>'+''.join(f'<li>{e(b[lang])}</li>' for b in entry['chosen'])+'</ul>')
    edu = master['education']
    parts.append(f'<h2>{L["education"]}</h2><div class="row"><b>{e(localized(edu["degree"], lang))} · {e(edu["school"])}</b>'
                 f'<span class="meta">{e(localized(edu["period"], lang))}</span></div>')
    parts.append(f'<h2>{L["skills"]}</h2><div class="skills">'+''.join(f'<p><b>{e(g["label"])}:</b> {e(", ".join(g["items"]))}</p>' for g in selection['skills'] if g['items'])+'</div>')
    if master.get('spoken_languages'):parts.append(f'<h2>{L["languages"]}</h2><p>{e(localized(master["spoken_languages"], lang))}</p>')
    return f'<!doctype html><html lang="{"pt-BR" if lang == "pt" else "en"}"><head><meta charset="utf-8"><title>{e(person["name"])}</title><style>{STYLE}</style></head><body>'+''.join(parts)+'</body></html>'


async def pdf_bytes(html):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome')
        try:
            page = await browser.new_page();await page.set_content(html, wait_until='load')
            return await page.pdf(format='A4', print_background=False, prefer_css_page_size=True)
        finally:await browser.close()


def pages(data):
    import pymupdf
    with pymupdf.open(stream=data, filetype='pdf') as document:return document.page_count


def folder(data, job):
    return Path(data)/'tailored_resumes'/hashlib.sha256(job_key(job['url']).encode()).hexdigest()[:16]


async def build(data, job, master=None):
    """The job's resume PDF, rebuilt only when the master or this code changed."""
    master = master or load_master(data);target = folder(data, job);pdf = target/FILENAME
    stamp = {'version':VERSION, 'code':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'master':master['digest'], 'job':job_key(job['url']), 'title':job.get('title'),
             'description':hashlib.sha256(str(job.get('description', '')).encode()).hexdigest()}
    meta = target/'selection.json'
    if pdf.exists() and meta.exists() and json.loads(meta.read_text(encoding='utf-8')).get('stamp') == stamp:return pdf
    selection = select(master, job)
    for _ in range(12):
        html = render_html(master, selection);data_pdf = await pdf_bytes(html)
        if pages(data_pdf) <= 1 or not trim(selection):break
    target.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(data_pdf);(target/'resume.html').write_text(html, encoding='utf-8')
    summary = {'stamp':stamp, 'built_at':now(), 'url':job['url'], 'company':job.get('company'), 'lang':selection['lang'],
               'headline':selection['headline'], 'pages':pages(data_pdf), 'drafts':selection['drafts'], 'job_tags':selection['job_tags'],
               'bullets':[b['id'] for e in selection['experience']+selection['projects'] for b in e['chosen']],
               'projects':[p['id'] for p in selection['projects']], 'sha256':hashlib.sha256(data_pdf).hexdigest()}
    meta.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    write_sheet(data, job, master)
    return pdf


def write_sheet(data, job, master=None):
    """Interview prep for one job: asked stacks, gaps, claims made to the company and the stories that back them."""
    from collections import defaultdict
    master = master or load_master(data);lang = language(job);weight = job_terms(job)
    bullets = [(e, b) for e in master['experience']+master['projects'] for b in e.get('bullets', []) if b.get('status', 'draft') != 'off']
    def stories(tags):
        ranked = sorted(bullets, key=lambda eb:-sum(t in eb[1].get('tags', []) for t in tags))
        return [eb for eb in ranked if any(t in eb[1].get('tags', []) for t in tags)][:2]
    asked = [t for t in TAG_TERMS if weight(t)]
    covered = {t for _, b in bullets for t in b.get('tags', [])}
    store = AttemptStore(data);company = company_of(job)
    with store.db() as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        claims = db.execute('SELECT stack,low,high,question,job_url FROM company_claims WHERE company=? ORDER BY at', (company,)).fetchall() if 'company_claims' in tables else []
        answers = db.execute("SELECT a.question,a.answer FROM easy_answers a JOIN easy_answer_jobs j ON j.answer_id=a.id WHERE j.job_url=? AND a.answer IS NOT NULL",
                             (job['url'],)).fetchall() if 'easy_answers' in tables else []
    span = lambda lo, hi:(f'{lo:g}+' if hi == float('inf') else (f'{lo:g}' if lo == hi else f'{lo:g}–{hi:g}'))
    lines = [f"# {job.get('title', '')} · {job.get('company', '')}", '', job['url'], '',
             '## O que a vaga pede', '']
    for tag in sorted(asked, key=lambda t:-weight(t)):
        mark = 'título' if weight(tag) == 3 else 'descrição'
        lines.append(f'- **{tag}** ({mark}): '+('tem história no mestre' if tag in covered else '**sem evidência no mestre: estudar ou preparar resposta honesta**'))
    by_stack = defaultdict(list)
    for stack, lo, hi, question, url in claims:by_stack[stack].append((span(lo, hi), question, url == job['url']))
    lines += ['', f'## O que você já declarou para {company or "esta empresa"}', '']
    if not by_stack:lines.append('- Nada registrado ainda.')
    for stack, items in by_stack.items():
        values = sorted({v for v, _, _ in items})
        lines.append(f'- **{stack}**: {", ".join(values)} anos'+(' (**valores diferentes já enviados**)' if len(values) > 1 else ''))
        for e, b in stories([t for t in stack.split('+')]+(['backend', 'integrations'] if stack == 'general' else [])):
            lines.append(f'  - História: {b[lang]} _({e.get("company") or localized(e.get("name"), lang)})_')
        if not stories(stack.split('+')) and stack != 'general':lines.append('  - **Sem história no mestre para isso: prepare um exemplo concreto.**')
    lines += ['', '## Respostas enviadas nesta vaga', '']
    if not answers:lines.append('- (preenchidas depois do envio)')
    for question, answer in answers:
        value = json.loads(answer);value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        lines.append(f'- {question.strip()[:160]} → {value[:300]}')
    target = folder(data, job);target.mkdir(parents=True, exist_ok=True)
    (target/'interview.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return target/'interview.md'


async def resume_for(data, job, fallback):
    """The PDF to upload for a job: tailored when enabled, the configured one otherwise or when the build fails."""
    if not tailoring_enabled(data):return fallback
    try:return await build(data, job)
    except Exception as error:  # A broken master or Chrome must not block the application.
        print(json.dumps({'resume_tailoring_failed':type(error).__name__, 'detail':str(error)[:300], 'url':job.get('url')}, ensure_ascii=False), flush=True)
        return fallback


def drafts(master):
    """Bullets still waiting for the candidate's review."""
    return [(e.get('company') or localized(e.get('name'), 'pt'), b) for e in master['experience']+master['projects']
            for b in e.get('bullets', []) if b.get('status', 'draft') == 'draft']
