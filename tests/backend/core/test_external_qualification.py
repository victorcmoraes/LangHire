from core.external_qualification import qualify,required_text

FACTS={'experience.years':{'value':3}}

def job(description,**fields):
    return dict({'platform':'greenhouse','title':'Backend Engineer','location':'Brazil (Remote)','description':description},**fields)

def test_team_tenure_and_desired_experience_do_not_become_mandatory_duration():
    description='Our team has engineers with more than 10 years of experience.\nWork Experience:\n3+ years as a backend developer\nMust-Have Experience:\nJava and REST APIs\nNice-to-Have Experience:\n5 years of experience with Go'
    assert qualify(job(description),FACTS)['state']=='eligible'
    assert '10 years' not in required_text(description)
    assert '5 years' not in required_text(description)

def test_required_duration_with_words_between_years_and_experience_blocks():
    description='Requirements:\n4 years of hands-on Java development experience'
    assert qualify(job(description),FACTS)['reason']=='mandatory_duration_above_confirmed'
    assert qualify(job('Requirements:\n4+ years building backend services'),FACTS)['reason']=='mandatory_duration_above_confirmed'

def test_required_active_cnpj_is_not_inferred_from_cpf_or_nationality():
    assert qualify(job('Requirements:\nContractor must maintain an active corporate entity (PJ / CNPJ).'),FACTS)['reason']=='active_business_entity_not_confirmed'
    assert qualify(job('Requirements:\nContractor must maintain an active corporate entity (\nPessoa Juridica PJ / CNPJ\n).'),FACTS)['reason']=='active_business_entity_not_confirmed'

def test_occasional_remote_benefit_does_not_override_hybrid_office_requirement():
    description='This role is based in Sao Paulo office. We are a hybrid environment. We currently require a minimum of 3 days per week in the office. Perk: four weeks of remote work.\nRequirements:\n3 years of backend experience'
    assert qualify(job(description,location='Sao Paulo, Brazil',workplaceType='remote'),FACTS)['state']=='discarded'

def test_explicit_hybrid_work_type_overrides_generic_remote_perk():
    description='Requirements:\nJava\nWork Type:\nHybrid\nBenefits:\nIn-office, hybrid, or remote flexibility'
    assert qualify(job(description,location='Sao Paulo, Brazil'),FACTS)['state']=='discarded'

def test_unknown_arrangement_and_country_are_pending():
    assert qualify(job('Requirements:\nJava',location='Sao Paulo, Brazil'),FACTS)['reason']=='working_arrangement_needs_evidence'
    assert qualify(job('We have colleagues in Brazil\nRequirements:\nJava',location='Remote'),FACTS)['reason']=='geographic_eligibility_needs_evidence'

def test_explicit_remote_work_type_is_supported():
    assert qualify(job('Requirements:\nJava\nWork Type:\nRemote',location='Brazil, Colombia'),FACTS)['state']=='eligible'


def test_specialist_accounting_and_production_ml_are_not_implied_by_general_engineering():
    assert qualify(job('Requirements:\nPython',title='Software Engineer II with Accounting Experience'),FACTS)['state']=='pending'
    description='Requirements:\n2+ years of software engineering experience, with a strong focus on ML engineering and deploying machine learning models in production.'
    assert qualify(job(description),FACTS)['reason']=='specific_production_ml_requirement'
