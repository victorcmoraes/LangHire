import json
import pytest
from core.application_safety import SafetyError,job_key
from core.external_apply import canonical_ats,platform_hold
from core.external_catalog import refresh_board
from core.external_discovery import board_spec,discover
from core.external_service import queue

ASHBY='https://jobs.ashbyhq.com/fixture/00000000-0000-0000-0000-00000000000a'


def ashby_item(**changes):
    item={'id':'00000000-0000-0000-0000-00000000000a','title':'Backend Engineer','location':'Remote - Brazil',
          'secondaryLocations':[{'location':'Mexico','address':{'postalAddress':{'addressCountry':'Mexico'}}}],
          'address':{'postalAddress':{'addressCountry':'Brazil'}},'isListed':True,'isRemote':True,'workplaceType':'Remote',
          'jobUrl':ASHBY,'applyUrl':ASHBY+'/application','descriptionPlain':'Requirements:\n3 years with Python','department':'Engineering'}
    return item|changes


def test_ashby_uses_structured_arrangement_and_skips_unlisted_postings(monkeypatch):
    rows=[ashby_item(),ashby_item(id='b',title='Hidden',isListed=False),ashby_item(id='c',workplaceType='Hybrid',address={})]
    monkeypatch.setattr('core.external_catalog.fetch_json',lambda url:{'jobs':rows})
    jobs=refresh_board('ashby','fixture')
    assert [j['externalId'] for j in jobs]==[rows[0]['id'],'c']
    assert jobs[0]|{'description':''}=={'platform':'ashby','source':'ashby','board':'fixture','company':'fixture','externalId':rows[0]['id'],
        'title':'Backend Engineer','url':ASHBY,'applyUrl':ASHBY+'/application','location':'Remote - Brazil; Mexico','description':'',
        'country':'BR','workplaceType':'remote','department':'Engineering'}
    # isRemote is true for hybrid postings too; only workplaceType is trusted.
    assert jobs[1]['workplaceType']=='hybrid' and jobs[1]['country'] is None


def test_workable_and_smartrecruiters_normalize_to_the_catalog_shape(monkeypatch):
    workable={'jobs':[{'title':'Data Engineer','shortcode':'AB12CD34EF','telecommuting':True,'department':'Data',
        'url':'https://apply.workable.com/j/AB12CD34EF','application_url':'https://apply.workable.com/j/AB12CD34EF/apply',
        'locations':[{'country':'Brazil','countryCode':'BR','city':'Curitiba','region':'Parana','hidden':False}],'description':'<p>Requirements</p><ul><li>SQL</li></ul>'}]}
    monkeypatch.setattr('core.external_catalog.fetch_json',lambda url:workable)
    job=refresh_board('workable','fixture')[0]
    assert (job['externalId'],job['location'],job['country'],job['workplaceType'])==('AB12CD34EF','Curitiba, Parana, Brazil','BR','remote')
    assert 'SQL' in job['description']
    calls=[]
    def sr(url):
        calls.append(url)
        if '/postings?' in url:return {'totalFound':2,'content':[{'id':'1','name':'Backend Developer','location':{}},{'id':'2','name':'Sales Executive','location':{}}]}
        return {'id':'1','name':'Backend Developer','postingUrl':'https://jobs.smartrecruiters.com/Fixture/1-backend-developer','applyUrl':'https://jobs.smartrecruiters.com/Fixture/1-backend-developer?oga=true',
                'location':{'fullLocation':'Curitiba, Brazil','country':'br','remote':False,'hybrid':True},
                'jobAd':{'sections':{'companyDescription':{'title':'About','text':'Marketing copy'},'qualifications':{'title':'Qualifications','text':'<p>Java</p>'}}}}
    monkeypatch.setattr('core.external_catalog.fetch_json',sr)
    jobs=refresh_board('smartrecruiters','Fixture',lambda title:'Developer' in title)
    assert [u.rsplit('/',1)[-1] for u in calls]==['postings?country=br&limit=100&offset=0','1']
    assert (jobs[0]['country'],jobs[0]['workplaceType'],jobs[0]['description'])==('BR','hybrid','Qualifications\nJava')
    assert job_key(jobs[1]['url'])=='smartrecruiters:fixture:2' and jobs[1]['description']==''


def test_smartrecruiters_board_too_large_to_read_fails_instead_of_closing_jobs(monkeypatch):
    monkeypatch.setattr('core.external_catalog.fetch_json',lambda url:{'totalFound':10_000,'content':[{'id':url[-3:],'name':'x','location':{}}]*100})
    with pytest.raises(RuntimeError):refresh_board('smartrecruiters','Fixture',lambda title:False)


def test_posting_and_application_urls_share_one_duplicate_key():
    assert job_key(ASHBY)==job_key(ASHBY.upper().replace('HTTPS://JOBS.ASHBYHQ.COM','https://jobs.ashbyhq.com')+'/application')
    assert job_key('https://apply.workable.com/j/AB12CD34EF')==job_key('https://apply.workable.com/fixture/j/AB12CD34EF/apply/')=='workable:AB12CD34EF'
    assert (job_key('https://jobs.smartrecruiters.com/Fixture/744000158214075-backend-developer?oga=true')
            ==job_key('https://jobs.smartrecruiters.com/fixture/744000158214075')=='smartrecruiters:fixture:744000158214075')


def test_new_boards_are_accepted_only_by_platform_or_official_board_url():
    assert board_spec('ashby:fixture')=={'platform':'ashby','board':'fixture'}
    assert board_spec('https://jobs.ashbyhq.com/fixture?utm=1')=={'platform':'ashby','board':'fixture'}
    assert board_spec('https://apply.workable.com/fixture/')=={'platform':'workable','board':'fixture'}
    assert board_spec('https://jobs.smartrecruiters.com/Fixture')=={'platform':'smartrecruiters','board':'Fixture'}
    for raw in ['https://apply.workable.com/j/AB12CD34EF','gupy:fixture','https://fixture.gupy.io/','https://evil.invalid/ashby']:
        with pytest.raises(SafetyError):board_spec(raw)


def test_collected_platforms_cannot_send_or_execute_without_an_explicit_enablement(tmp_path):
    for platform in ('ashby','workable','smartrecruiters'):assert platform_hold(tmp_path,platform)
    assert not platform_hold(tmp_path,'greenhouse')
    with pytest.raises(SafetyError):canonical_ats(ASHBY+'/application')


def test_discovered_ashby_job_is_triaged_into_the_manual_queue_not_the_executor_queue(tmp_path,monkeypatch):
    (tmp_path/'external').mkdir()
    (tmp_path/'jobs.json').write_text('{}')
    (tmp_path/'confirmed_facts.json').write_text(json.dumps({'facts':[{'id':'experience.years','value':3},{'id':'location.country','value':'Brasil'}]}))
    monkeypatch.setattr('core.external_catalog.fetch_json',lambda url:{'jobs':[ashby_item(),ashby_item(id='x',title='Account Executive')]})
    report=discover(tmp_path,['ashby:fixture'])
    assert report['new_jobs']==1 and report['new_qualification']=={'eligible':1}
    assert json.loads((tmp_path/'jobs.json').read_text())[ASHBY]['collection_origin']=='public_ashby_board'
    assert queue(tmp_path)==[]
    manual=json.loads((tmp_path/'external/manual-queue.json').read_text())
    assert [(m['platform'],m['url']) for m in manual]==[('ashby',ASHBY)]
