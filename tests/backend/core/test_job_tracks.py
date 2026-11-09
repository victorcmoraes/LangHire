import json
import pytest

from core.application_safety import AttemptStore,SafetyError
from core.external_qualification import qualify,triage
from core.job_tracks import load_job_tracks,matches_track


FACTS={'experience.years':{'value':3}}
ALL_TRACKS={'include':'.*','exclude':r'\b(?:staff|principal|manager|diretor|director|head|architect|arquiteto|master(?!\s+data)|lead|leader)\b',
            'allow_mobile':True,'allow_pure_frontend':True}


def rules(data,config):
    (data/'settings.json').write_text(json.dumps({'job_tracks':config}),encoding='utf-8')
    return load_job_tracks(data)


def job(title):
    return {'platform':'greenhouse','source':'greenhouse','board':'fixture','externalId':'123','title':title,
            'url':'https://job-boards.greenhouse.io/fixture/jobs/123','location':'Brazil (Remote)','description':'Requirements:\n3 years of experience'}


@pytest.mark.parametrize('title',['Full Stack Engineer','Platform Engineer','Site Reliability Engineer','QA Automation',
                                 'Mobile Developer','Data Scientist','Agentic AI / Python Engineer','Frontend Engineer'])
def test_explicit_all_tracks_accepts_specialties_without_changing_default(tmp_path,title):
    assert qualify(job(title),FACTS)['state']=='discarded'
    assert qualify(job(title),FACTS,rules(tmp_path,ALL_TRACKS))['state']=='eligible'


@pytest.mark.parametrize('title',['AI Performance Video Designer','Cinematic AI Video Creator - Generative AI - (DTC Ads)',
                                 'AI Product Concept Designer & Interior Stylist – Home Décor','AI Conversation Recording Contributor',
                                 'AI Research Engineer (Agentic Reinforcement Learning)','Web Development Specialist - Freelance AI Trainer Project',
                                 'Account Executive','2D Designer','Product Owner/IA','Líder Técnico de IA - Fábrica agentica'])
def test_non_engineering_roles_are_discarded_however_broad_the_tracks(tmp_path,title):
    tracks=rules(tmp_path,ALL_TRACKS)
    assert not matches_track(title,tracks)
    assert qualify(job(title),FACTS,tracks)['reason']=='non_engineering_role'


@pytest.mark.parametrize('title',['Analista de Suporte Pleno (Developer L3)','Business Intelligence II','Back-end JR l Pesquisa e Desenvolvimento',
                                 'Data Scientist - AI & Experimentation','AI Engineer (Enabling Team)'])
def test_technical_titles_near_the_non_engineering_words_still_match(tmp_path,title):
    assert matches_track(title,rules(tmp_path,ALL_TRACKS))


@pytest.mark.parametrize('title',['Staff Software Engineer','Principal Platform Engineer','Master AI Engineer','Tech Lead FullStack',
                                 'Engineering Manager','Engineering Director','Head of Product','Software Architect'])
def test_all_tracks_keeps_role_exclusions(tmp_path,title):
    assert qualify(job(title),FACTS,rules(tmp_path,ALL_TRACKS))['reason']=='outside_seniority_or_specialty'


def test_all_tracks_preserves_evidence_requirements_and_specific_filters(tmp_path):
    tracks=rules(tmp_path,ALL_TRACKS)
    assert qualify(dict(job('Data Scientist'),location='Remote'),FACTS,tracks)['reason']=='geographic_eligibility_needs_evidence'
    assert qualify(dict(job('Mobile Developer'),description='Requirements:\n5 years of experience'),FACTS,tracks)['reason']=='mandatory_duration_above_confirmed'
    assert qualify(job('ABAP Developer'),FACTS,tracks)['reason']=='specialized_requirement_needs_evidence'
    assert qualify(job('Data Scientist (Women Applicants Only)'),FACTS,tracks)['reason']=='affirmative_eligibility_not_confirmed'
    assert qualify(job('Software Developer - Flutter'),FACTS,rules(tmp_path,{'include':'.*'}))['reason']=='mobile_outside_tracks'
    assert qualify(job('Frontend Developer'),FACTS,rules(tmp_path,{'include':'.*'}))['reason']=='pure_frontend_outside_tracks'


@pytest.mark.parametrize('config',[None,[],{'include':'['},{'exclude':''},{'include':True},{'allow_mobile':'false'},{'allow_pure_frontend':1},{'typo':True}])
def test_invalid_configuration_blocks_instead_of_silently_broadening(tmp_path,config):
    with pytest.raises(SafetyError):rules(tmp_path,config)


def test_missing_configuration_uses_original_defaults_and_normalizes_titles(tmp_path):
    assert matches_track('DESENVOLVEDOR',load_job_tracks(tmp_path))
    assert not matches_track('Data Scientist',load_job_tracks(tmp_path))
    assert qualify(job('Backend Engineer'),FACTS,load_job_tracks(tmp_path))['state']=='eligible'


def test_triage_loads_local_rules_and_preserves_confirmed_identity(tmp_path):
    (tmp_path/'external').mkdir()
    row=job('Data Scientist')
    (tmp_path/'jobs.json').write_text(json.dumps({row['url']:row}))
    (tmp_path/'external/catalog.json').write_text(json.dumps({'jobs':[row],'boards':[]}))
    (tmp_path/'confirmed_facts.json').write_text(json.dumps({'facts':[{'id':'experience.years','value':3}]}))
    assert triage(tmp_path)['states']=={'discarded':1}
    rules(tmp_path,ALL_TRACKS)
    assert triage(tmp_path)['states']=={'eligible':1}
    store=AttemptStore(tmp_path);aid=store.begin(row['url']);store.transition(aid,'confirmed','fixture receipt')
    assert triage(tmp_path)['states']=={'protected':1}
