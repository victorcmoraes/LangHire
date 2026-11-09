import json

import pymupdf
import pytest

import core.resume_tailoring as tailoring
from core.application_safety import AttemptStore, SafetyError
from core.company_claims import CompanyClaims
from core.resume_tailoring import build, language, load_master, resume_for, select, trim, write_sheet

MASTER = '''
version: 1
person: {name: Test Person, email: t@example.invalid, location: {pt: "Recife", en: "Recife"}, links: [github.com/test]}
headlines:
  - {id: backend, tags: [backend, python, api], pt: "Backend PT", en: "Backend EN"}
  - {id: qa, tags: [qa, playwright], pt: "QA PT", en: "QA EN"}
summaries:
  - {id: backend, tags: [backend], pt: "Resumo backend", en: "Backend summary"}
  - {id: qa, tags: [qa], pt: "Resumo QA", en: "QA summary"}
experience:
  - id: work
    company: Acme
    role: {pt: Dev, en: Dev}
    location: {pt: Remoto, en: Remote}
    start: {pt: "Jan 2024", en: "Jan 2024"}
    end: {pt: Atual, en: Present}
    show: 3
    min: 1
    bullets:
      - {id: core, status: confirmed, always: true, priority: 3, tags: [integrations], pt: "Integrações", en: "Integrations"}
      - {id: api, status: confirmed, priority: 1, tags: [api, python], pt: "APIs em Python", en: "Python APIs"}
      - {id: tests, status: confirmed, priority: 2, tags: [qa, playwright], pt: "Testes Playwright", en: "Playwright tests"}
      - {id: secret, status: off, priority: 1, tags: [python, api, qa], pt: "Nunca", en: "Never"}
      - {id: metric, status: draft, priority: 3, tags: [data], pt: "Métrica", en: "Metric"}
projects:
  - id: main
    name: {pt: Plataforma, en: Platform}
    always: true
    show: 2
    min: 1
    bullets:
      - {id: p1, status: confirmed, tags: [qa], pt: "E2E", en: "E2E"}
      - {id: p2, status: confirmed, tags: [backend], pt: "API", en: "API"}
  - id: side
    name: {pt: Lado, en: Side}
    bullets:
      - {id: s1, status: confirmed, tags: [java], pt: "Java", en: "Java"}
education: {school: Uni, degree: {pt: Bacharel, en: BSc}, period: {pt: "2022 – 2026", en: "2022 – 2026"}}
skills:
  - {id: lang, label: {pt: Linguagens, en: Languages}, items: [Python, Java], when_asked: [Kafka]}
'''


@pytest.fixture
def data(tmp_path):
    (tmp_path/'resume').mkdir()
    (tmp_path/'resume'/'master.yaml').write_text(MASTER, encoding='utf-8')
    return tmp_path


def job(title, description='', url='https://jobs.lever.co/acme/1', company='acme'):
    return {'url':url, 'title':title, 'description':description, 'company':company}


def test_title_decides_the_track_and_off_bullets_never_appear(data):
    master = load_master(data)
    chosen = select(master, job('Analista de Testes (Automação & Backend)', 'Você vai escrever testes com Playwright para nossa API.'))
    assert chosen['headline'] == 'qa' and chosen['lang'] == 'pt'
    ids = [b['id'] for b in chosen['experience'][0]['chosen']]
    assert ids[0] == 'core' and 'secret' not in ids and ids[1] == 'tests'
    assert select(master, job('Backend Engineer (Python)', 'You will build our APIs with the team.'))['headline'] == 'backend'


def test_skills_add_when_asked_items_only_if_the_job_names_them(data):
    master = load_master(data)
    assert select(master, job('Backend', 'We use Kafka and Python'))['skills'][0]['items'][:2] == ['Python', 'Kafka']
    assert 'Kafka' not in select(master, job('Backend', 'Python'))['skills'][0]['items']


def test_drafts_are_reported_and_trimming_keeps_minimums_and_pins(data):
    master = load_master(data)
    chosen = select(master, job('Data Engineer', 'data pipelines'))
    assert 'metric' in chosen['drafts']
    while trim(chosen):pass
    assert [b['id'] for b in chosen['experience'][0]['chosen']] == ['core']
    assert [p['id'] for p in chosen['projects']] == ['main'] and len(chosen['projects'][0]['chosen']) == 1


def test_language_follows_the_posting_not_a_fixed_form_default():
    assert language(dict(job('Desenvolvedor', 'Você vai atuar com nossa equipe. Requisitos: experiência com APIs.'), form_language='en')) == 'pt'
    assert language(dict(job('Developer', ''), form_language='pt')) == 'pt'


def test_invalid_master_is_rejected(tmp_path):
    (tmp_path/'resume').mkdir()
    (tmp_path/'resume'/'master.yaml').write_text(MASTER.replace('status: draft', 'status: maybe'), encoding='utf-8')
    with pytest.raises(SafetyError):load_master(tmp_path)


def fake_pdf(pages):
    document = pymupdf.open()
    for _ in range(pages):document.new_page()
    return document.tobytes()


async def test_build_trims_to_one_page_caches_and_writes_the_sheet(data, monkeypatch):
    calls = []
    async def render(html):
        calls.append(html);return fake_pdf(2 if len(calls) == 1 else 1)
    monkeypatch.setattr(tailoring, 'pdf_bytes', render)
    posting = job('Backend Engineer', 'Python APIs and Playwright tests')
    pdf = await build(data, posting)
    meta = json.loads((pdf.parent/'selection.json').read_text(encoding='utf-8'))
    assert pdf.name == tailoring.FILENAME and meta['pages'] == 1 and len(calls) == 2
    assert '<li>' in (pdf.parent/'resume.html').read_text(encoding='utf-8') and (pdf.parent/'interview.md').exists()
    assert await build(data, posting) == pdf and len(calls) == 2
    await build(data, dict(posting, description='Different text'))
    assert len(calls) == 3


def test_sheet_lists_claims_to_the_company_and_gaps(data):
    claims = CompanyClaims(AttemptStore(data))
    claims.apply({'label':'How many years with Python?', 'type':'text'}, job('x', url='https://jobs.lever.co/acme/2'), {'value':'3'})
    claims.apply({'label':'How long with .NET?', 'type':'text'}, job('x', url='https://jobs.lever.co/acme/3'), {'value':'2'})
    sheet = write_sheet(data, job('Backend Engineer', 'Python, Java and machine learning')).read_text(encoding='utf-8')
    assert '**python**: 3 anos' in sheet and 'Python APIs' in sheet
    assert '**dotnet**: 2 anos' in sheet and 'Sem história no mestre' in sheet
    assert '**ai** (descrição): **sem evidência no mestre' in sheet


async def test_resume_for_falls_back_when_disabled_or_broken(data, tmp_path):
    fallback = tmp_path/'configured.pdf'
    assert await resume_for(data, job('Backend'), fallback) == fallback
    (data/'settings.json').write_text(json.dumps({'resume_tailoring':{'enabled':True}}))
    (data/'resume'/'master.yaml').write_text('version: 1', encoding='utf-8')
    assert await resume_for(data, job('Backend'), fallback) == fallback


async def test_assist_serves_each_planned_job_its_own_pdf(data, tmp_path, monkeypatch):
    from core.apply_assist import Assistant
    configured = tmp_path/'resume-en.pdf';configured.write_bytes(fake_pdf(1))
    built = {}
    async def fake_resume_for(directory, posting, fallback):
        path = tmp_path/(posting['url'].rsplit('/', 1)[1]+'.pdf');path.write_bytes(posting['url'].encode());built[posting['url']] = path
        return path
    monkeypatch.setattr(tailoring, 'resume_for', fake_resume_for)
    assistant = Assistant(data, configured)
    first, second = ('https://jobs.lever.co/acme/'+u for u in ('0d1a3c6e-1111-4c2b-9b1a-6f1e2d3c4b5a', '7e2b4d6f-2222-4d3c-8c2b-7a2f3e4d5c6b'))
    await assistant.prepare_resume(job('A', url=first));await assistant.prepare_resume(job('B', url=second))
    assert assistant.resume_bytes(first) == first.encode() and assistant.resume_bytes(second+'/apply?lh_assist=1') == second.encode()
    # An unknown page (LinkedIn's search URL) gets the last planned job's PDF.
    assert assistant.resume_bytes('https://www.linkedin.com/jobs/search/?currentJobId=1') == second.encode()
