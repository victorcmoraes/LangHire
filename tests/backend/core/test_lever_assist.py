import json

import pytest

from backend.core.application_safety import AttemptStore, SafetyError
from backend.core import lever_assist
from backend.core.lever_assist import Assistant

URL = 'https://jobs.lever.co/acme/11111111-2222-3333-4444-555555555555'
OTHER = 'https://jobs.lever.co/acme/66666666-7777-8888-9999-000000000000'


def assistant(tmp_path, monkeypatch):
    (tmp_path/'confirmed_facts.json').write_text(json.dumps({'facts':[
        {'id':'name.full','value':'Test Candidate','source':'user'},
        {'id':'location.city','value':'Curitiba','source':'user'}]}))
    resume = tmp_path/'resume.pdf';resume.write_bytes(b'%PDF-1.4')
    (tmp_path/'external').mkdir()
    jobs = [{'url':URL,'platform':'lever','title':'Backend Engineer','company':'Acme'},
            {'url':OTHER,'platform':'lever','title':'Platform Engineer','company':'Acme'}]
    protected = lambda: {a['job_url'] for a in AttemptStore(tmp_path).list() if a['state'] in ('awaiting_human','confirmed','uncertain')}
    monkeypatch.setattr('backend.core.external_service.queue', lambda data:[j for j in jobs if j['url'] not in protected()])
    return Assistant(tmp_path, resume)


async def test_fills_known_answers_and_leaves_the_submit_to_the_candidate(tmp_path, monkeypatch):
    a = assistant(tmp_path, monkeypatch)
    assert a.next()['url'] == URL+'/apply'
    fields = [{'label':'Full name ✱','type':'text','name':'name','required':True,'options':[],'targets':['0-0'],'value':''},
              {'label':'Resume/CV ✱','type':'file','name':'resume','required':True,'options':[],'targets':['1-0'],'value':[]},
              {'label':'Upload your transcripts ✱','type':'file','name':'cards[x]','required':True,'options':[],'targets':['2-0'],'value':[]}]
    plan = await a.plan(URL+'/apply?lever-source=x', fields)
    assert {f['label']:f['value'] for f in plan['fills']} == {'Full name ✱':'Test Candidate','Resume/CV ✱':'resume.pdf'}
    assert plan['pending'] == ['Upload your transcripts ✱']
    attempt = a.waiting(URL)
    assert attempt['state'] == 'awaiting_human' and not AttemptStore(tmp_path).submissions(attempt['id'])
    # Filling again (page reload) keeps the same attempt instead of tripping duplicate protection.
    await a.plan(URL+'/apply', fields)
    assert len(AttemptStore(tmp_path).list()) == 1
    result = a.done(URL, 'Application submitted!\nThanks for applying to Acme.')
    assert result['state'] == 'confirmed' and result['url'] == OTHER+'/apply'


async def test_portal_error_is_uncertain_and_skipped_jobs_leave_the_queue(tmp_path, monkeypatch):
    a = assistant(tmp_path, monkeypatch)
    await a.plan(URL, [])
    assert a.done(URL, 'There was an error verifying your application. Please try again.')['state'] == 'uncertain'
    with pytest.raises(SafetyError):await a.plan(URL, [])
    await a.plan(OTHER, [])
    assert a.skip(OTHER)['url'] is None
    assert AttemptStore(tmp_path).list()[0]['state'] == 'pending'


def test_bookmarklet_script_embeds_the_field_inspector(tmp_path):
    js = lever_assist.script('secret')
    assert "'secret'" in js and 'data-langhire-external' in js and '__' not in js.replace('__proto__','')
