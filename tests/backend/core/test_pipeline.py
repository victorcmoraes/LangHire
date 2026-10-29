import json
from collections import Counter
import sqlite3
from core.pipeline import build_pipeline,read_pipeline,reason_bucket
from core.application_safety import job_key


def job(n):
    return {'url':f'https://www.linkedin.com/jobs/view/{n}','title':f'Engineer {n}','company':'Example Labs','source':'linkedin'}


def attempt(n,state,**extra):
    return {'id':str(n),'job_url':job(n)['url'],'state':state,'created_at':'2026-01-01','updated_at':'2026-01-02',**extra}


def test_paths_conserve_every_job_and_confirmed_outcomes():
    jobs=[job(n) for n in range(1,10)]
    decisions=[dict(job(n),state=state,reason=reason,evidence=['x'*1000]*5) for n,state,reason in [
        (1,'eligible','requirements_supported_and_brazil_location'),(2,'protected','existing_confirmed_uncertain_or_active'),
        (3,'discarded','outside_tracks'),(4,'pending','missing_requirements'),(5,'closed','not_in_current_complete_public_board'),
        (6,'eligible','ok'),(7,'eligible','ok'),(8,'eligible','ok'),(9,'eligible','ok')]]
    result=build_pipeline(jobs=jobs,decisions=decisions,attempts=[attempt(1,'confirmed'),attempt(2,'uncertain',send_intent=True),attempt(6,'pending'),attempt(8,'confirmed'),attempt(9,'confirmed')],
        outcomes=[{'url':job(1)['url'],'stage':'interview','body':'never expose'}, {'url':job(8)['url'],'stage':'rejected'}],questions=[job(6)['url']])
    assert result['totals']=={'collected':9,'eligible':2,'sent':4,'confirmed':3,'advanced':1}
    assert len({j['job_key'] for j in result['jobs']})==9
    incoming=Counter();outgoing=Counter();expected=Counter()
    for j in result['jobs']:
        assert len(j['path'])==len(set(j['path']))
        expected.update(zip(j['path'],j['path'][1:]))
    for link in result['sankey']['links']:
        incoming[link['target']]+=link['value'];outgoing[link['source']]+=link['value']
        assert expected[(link['source'],link['target'])]==link['value']
    assert outgoing['collected']==9
    for node in result['sankey']['nodes']:
        if incoming[node['id']] and outgoing[node['id']]:assert incoming[node['id']]==outgoing[node['id']]
    assert sum(incoming[id] for id in incoming if not outgoing[id])==9
    first=result['jobs'][0]
    assert first['platform']=='linkedin' and first['application_status']=='confirmed'
    assert first['path'][-1]=='outcome:advanced' and first['outcome']['stage']=='interview'
    assert len(first['triage']['evidence'])==3 and len(first['triage']['evidence'][0])==500
    assert 'body' not in json.dumps(result)
    assert result['jobs'][1]['triage']['state']=='protected'
    assert result['jobs'][5]['application_status']=='blocked-by-question'


def test_paused_and_manual_platforms_are_not_reported_as_not_attempted():
    rows=[{'url':'https://jobs.lever.co/fixture/00000000-0000-0000-0000-000000000001','title':'Engineer','platform':'lever'},
          {'url':'https://jobs.ashbyhq.com/fixture/00000000-0000-0000-0000-000000000002','title':'Engineer','platform':'ashby'},
          {'url':'https://job-boards.greenhouse.io/fixture/jobs/3','title':'Engineer','platform':'greenhouse'}]
    result=build_pipeline(jobs=rows,decisions=[dict(r,state='eligible',reason='ok') for r in rows],paused={'lever'})
    assert sorted(j['application_status'] for j in result['jobs'])==['manual-only','not-attempted','platform-paused']
    assert {n['id'] for n in result['sankey']['nodes']}>={'application:platform-paused','application:manual-only','application:not-attempted'}


def test_buckets():
    assert reason_bucket('outside_tracks')=='Outside tracks'
    assert reason_bucket('mandatory_duration_above_confirmed')=='Experience above confirmed years'
    assert reason_bucket('onsite_or_hybrid_outside_home_region')=='Location / hybrid'
    assert reason_bucket('mandatory_schedule_not_confirmed')=='Language/schedule'
    assert reason_bucket('missing_requirements')=='Needs evidence'
    assert reason_bucket('new_reason')=='Other'


def test_duplicates_legacy_and_attempt_trail():
    url='https://job-boards.greenhouse.io/example/jobs/123'
    old={'url':url,'source':'greenhouse','board':'example','externalId':'123','collected_at':'2026-01-01'}
    new=dict(old,platform='greenhouse',title='Developer')
    rows=[dict(attempt(1,'pending'),job_url=url,id='old'),dict(attempt(1,'confirmed'),job_url=url+'/confirmation',id='new',created_at='2026-01-03')]
    result=build_pipeline(catalog=[new],jobs=[old],attempts=rows)
    assert result['totals']['collected']==1
    assert [a['id'] for a in result['jobs'][0]['attempts']]==['old','new']
    assert result['jobs'][0]['collected_at']=='2026-01-01'
    assert result['jobs'][0]['job_key']==job_key(url)


def test_read_only_missing_and_populated_snapshots(tmp_path):
    missing=tmp_path/'missing'
    assert read_pipeline(missing)['jobs']==[]
    assert not missing.exists()
    (tmp_path/'jobs.json').write_text(json.dumps({job(1)['url']:job(1)}))
    (tmp_path/'reports').mkdir()
    for date,stage in [('2026-01-01','rejected'),('2026-02-01','interview')]:
        (tmp_path/'reports'/f'outcomes-{date}.json').write_text(json.dumps({'applications':[{'url':job(1)['url'],'stage':stage}]}))
    dbpath=tmp_path/'application_attempts.sqlite'
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE attempts (id TEXT,job_url TEXT,state TEXT,created_at TEXT,updated_at TEXT,message TEXT)')
        db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?)',('a',job(1)['url'],'confirmed','2026-01-01','2026-01-02','receipt'))
    before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result=read_pipeline(tmp_path)
    assert result['jobs'][0]['outcome']['stage']=='interview'
    assert before=={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
