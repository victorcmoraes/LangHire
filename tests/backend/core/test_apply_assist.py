import base64
import hashlib
import json

import pytest

from backend.core import apply_assist, lever_assist
from backend.core.application_safety import AttemptStore, SafetyError
from backend.core.apply_assist import Assistant

URL = 'https://jobs.lever.co/acme/11111111-2222-3333-4444-555555555555'
OTHER = 'https://jobs.lever.co/acme/66666666-7777-8888-9999-000000000000'
GREENHOUSE = 'https://job-boards.greenhouse.io/acme/jobs/123'
LINKEDIN = 'https://www.linkedin.com/jobs/view/4242424242/'


def assistant(tmp_path, monkeypatch, platforms=('lever',), linkedin=()):
    (tmp_path/'confirmed_facts.json').write_text(json.dumps({'facts':[
        {'id':'name.full','value':'Test Candidate','source':'user'},
        {'id':'location.city','value':'Curitiba','source':'user'},
        {'id':'location.state','value':'Paraná','source':'user'}]}), encoding='utf-8')
    resume = tmp_path/'resume.pdf';resume.write_bytes(b'%PDF-1.4')
    (tmp_path/'external').mkdir()
    jobs = [{'url':URL,'platform':'lever','title':'Backend Engineer','company':'Acme'},
            {'url':OTHER,'platform':'lever','title':'Platform Engineer','company':'Acme'},
            {'url':GREENHOUSE,'platform':'greenhouse','title':'QA Engineer','company':'Acme','board':'acme'}]
    protected = lambda: {a['job_url'] for a in AttemptStore(tmp_path).list() if a['state'] in ('awaiting_human','confirmed','uncertain')}
    monkeypatch.setattr('backend.core.external_service.queue', lambda data:[j for j in jobs if j['url'] not in protected()])
    monkeypatch.setattr(Assistant, 'linkedin_jobs', lambda self:[dict(j) for j in linkedin if j['url'] not in protected()])
    projected = []
    monkeypatch.setattr(Assistant, 'project', lambda self, url, result:projected.append((url, result['state'])))
    a = Assistant(tmp_path, resume, platforms);a.projected = projected
    return a


async def test_fills_known_answers_and_leaves_the_submit_to_the_candidate(tmp_path, monkeypatch):
    a = assistant(tmp_path, monkeypatch)
    assert a.next()['url'] == URL+'/apply?lh_assist=1'
    fields = [{'label':'Full name ✱','type':'text','name':'name','required':True,'options':[],'targets':['0-0'],'value':''},
              {'label':'Resume/CV ✱','type':'file','name':'resume','required':True,'options':[],'targets':['1-0'],'value':[]},
              {'label':'Upload your transcripts ✱','type':'file','name':'cards[x]','required':True,'options':[],'targets':['2-0'],'value':[]}]
    plan = await a.plan(URL+'/apply?lever-source=x&lh_assist=1', fields)
    assert {f['label']:f['value'] for f in plan['fills']} == {'Full name ✱':'Test Candidate','Resume/CV ✱':'resume.pdf'}
    assert plan['pending'] == ['Upload your transcripts ✱']
    attempt = a.waiting(URL)
    assert attempt['state'] == 'awaiting_human' and not AttemptStore(tmp_path).submissions(attempt['id'])
    # Filling again (page reload) keeps the same attempt instead of tripping duplicate protection.
    await a.plan(URL+'/apply', fields)
    assert len(AttemptStore(tmp_path).list()) == 1
    result = a.done(URL+'/thanks', 'Application submitted!\nThanks for applying to Acme.')
    assert result['state'] == 'confirmed' and result['url'] == OTHER+'/apply?lh_assist=1'
    assert a.projected == [(URL, 'confirmed')]


async def test_portal_error_is_uncertain_and_skipped_jobs_leave_the_queue(tmp_path, monkeypatch):
    a = assistant(tmp_path, monkeypatch)
    await a.plan(URL, [])
    assert a.done(URL, 'There was an error verifying your application. Please try again.')['state'] == 'uncertain'
    with pytest.raises(SafetyError):await a.plan(URL, [])
    await a.plan(OTHER, [])
    assert a.skip(OTHER)['url'] is None
    assert AttemptStore(tmp_path).list()[0]['state'] == 'pending'


async def test_candidate_marked_uncertain_wins_over_confirmation_words(tmp_path, monkeypatch):
    a = assistant(tmp_path, monkeypatch)
    await a.plan(URL, [])
    assert a.done(URL, 'Thank you for applying', uncertain=True)['state'] == 'uncertain'


async def test_one_chain_runs_through_every_platform_without_a_daily_ceiling(tmp_path, monkeypatch):
    linkedin = [{'url':LINKEDIN,'platform':'linkedin','title':'Backend','company':'Initech','eligibility_country':'BR','form_language':'pt'}]
    a = assistant(tmp_path, monkeypatch, ('linkedin','greenhouse','lever'), linkedin)
    first = a.next()
    assert first['url'] == LINKEDIN+'?lh_assist=1' and first['by_platform'] == {'linkedin':1,'greenhouse':1,'lever':2}
    fields = [{'label':'Nome completo','type':'text','name':'n','required':True,'options':[],'targets':['0-0'],'value':''},
              {'label':'Carregar currículo','type':'file','name':'jobs-document-upload-file-input-upload-resume','required':False,
               'options':[],'targets':['1-0'],'value':[]},
              {'label':'Currículo antigo.pdf','type':'resume-choice','name':'r','required':False,'options':[],'targets':['2-0'],'value':True},
              {'label':'Escolha','type':'custom','name':'','required':True,'options':[],'targets':['custom-0'],'value':''}]
    plan = await a.plan('https://www.linkedin.com/jobs/view/4242424242/?lh_assist=1', fields, step=0)
    assert {f['label'] for f in plan['fills']} == {'Nome completo','Carregar currículo'} and plan['pending'] == ['Escolha']
    assert a.done(LINKEDIN, 'Sua candidatura foi enviada para Initech')['state'] == 'confirmed'
    assert a.next()['platform'] == 'greenhouse'


async def test_greenhouse_answers_come_from_the_schema_for_rendered_ids(tmp_path, monkeypatch):
    schema = {'id':123,'title':'QA Engineer','language':'en','questions':[
        {'label':'First Name','required':True,'fields':[{'name':'first_name','type':'input_text'}]},
        {'label':'Resume/CV','required':True,'fields':[{'name':'resume','type':'input_file'},{'name':'resume_text','type':'textarea'}]},
        {'label':'Cover Letter','required':False,'fields':[{'name':'cover_letter','type':'input_file'}]},
        {'label':'Portfolio','required':True,'fields':[{'name':'question_9','type':'input_text'}]}],
        'location_questions':[{'required':True,'fields':[{'name':'location'}]}]}
    async def fetch(job):return schema
    monkeypatch.setattr('backend.core.greenhouse_apply.fetch_schema', fetch)
    a = assistant(tmp_path, monkeypatch, ('greenhouse',))
    present = [{'id':'first_name','render':'input','value':''},{'id':'resume','render':'input','value':[]},
               {'id':'candidate-location','render':'combobox','value':''}]
    plan = await a.plan(GREENHOUSE+'?lh_assist=1', present)
    fills = {f['id']:f for f in plan['fills']}
    assert fills['resume']['value'] == 'resume.pdf' and fills['candidate-location']['value'] == 'Curitiba'
    assert fills['candidate-location']['state'] == 'Paraná'
    assert 'Portfolio' in plan['pending'] and 'Cover Letter' not in plan['pending']


async def test_restart_requeues_abandoned_pages_but_never_a_clicked_submit(tmp_path, monkeypatch):
    a = assistant(tmp_path, monkeypatch)
    await a.plan(URL, []);await a.plan(OTHER, [])
    a.submitted(OTHER+'/apply')
    assert a.recover() == {'pending':1, 'uncertain':1}
    states = {x['job_url']:x['state'] for x in AttemptStore(tmp_path).list()}
    assert states == {URL:'pending', OTHER:'uncertain'}
    assert [j['url'] for j in a.queue() if j['platform'] == 'lever'] == [URL]


def test_urls_canonicalize_across_outcome_pages():
    assert apply_assist.canonical('https://job-boards.greenhouse.io/acme/jobs/123/confirmation') == GREENHOUSE
    assert apply_assist.canonical(URL+'/thanks') == URL
    assert apply_assist.canonical('https://www.linkedin.com/jobs/view/4242424242/?lh_assist=1') == LINKEDIN
    with pytest.raises(SafetyError):apply_assist.canonical('https://example.com/jobs/1')


def test_extension_field_inspectors_and_origin_match_the_backend():
    assert (apply_assist.EXTENSION/'fields.js').read_text(encoding='utf-8') == apply_assist.extension_fields(), \
        'run: python -m cli.external assist --write-extension'
    manifest = json.loads((apply_assist.EXTENSION/'manifest.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]
    assert apply_assist.EXTENSION_ORIGIN == 'chrome-extension://'+''.join(chr(ord('a')+int(c, 16)) for c in digest)


def test_bookmarklet_script_embeds_the_field_inspector():
    js = lever_assist.script('secret')
    assert "'secret'" in js and 'data-langhire-external' in js and '__' not in js.replace('__proto__','')
