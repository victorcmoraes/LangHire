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


def test_remote_does_not_override_explicit_eligible_residence_locations():
    facts=FACTS|{'location.country':{'value':'Brazil'},'location.city':{'value':'Olinda'},'location.state':{'value':'Pernambuco'}}
    description='Requirements:\nJava\nWork Type:\nRemote\nEligible Locations:\nSão Paulo or Rio de Janeiro, Brazil\nLegal & Hiring Information'
    result=qualify(job(description,location='São Paulo or Rio de Janeiro, Brazil'),facts)
    assert result['state']=='pending' and result['reason']=='explicit_residence_restriction_not_supported'
    assert 'Eligible Locations:' in result['evidence'][0]
    assert 'location.state' in result['fact_refs']
    assert qualify(job(description),facts|{'location.city':{'value':'São Paulo'}})['state']=='eligible'
    assert qualify(job(description.replace('São Paulo or Rio de Janeiro, Brazil','Pernambuco, Brazil')),facts)['state']=='eligible'
    assert qualify(job(description.replace('São Paulo or Rio de Janeiro, Brazil','Brazil')),facts)['state']=='eligible'
    assert qualify(job(description),FACTS)['state']=='pending'


def test_country_mention_and_matching_city_substring_do_not_prove_allowed_residence():
    facts=FACTS|{'location.country':{'value':'Brazil'},'location.city':{'value':'Rio'},'location.state':{'value':'Pernambuco'}}
    result=qualify(job('Requirements:\nJava\nEligible Locations: Rio Grande, Brazil'),facts)
    # A city name prefix is not an exact place identity; keep this ambiguous.
    assert result['state']=='pending'


def test_required_specific_schedule_is_not_inferred_from_technology_experience():
    line='Availability to work until 3:00 PM CST.'
    assert qualify(job('Requirements:\nJava\n'+line),FACTS)['reason']=='mandatory_schedule_not_confirmed'
    confirmed=dict(FACTS,**{'availability.schedule':{'value':[line]}})
    assert qualify(job('Requirements:\nJava\n'+line),confirmed)['state']=='eligible'


def test_explicit_home_location_policy_supports_brazil_but_not_other_countries_or_hybrid():
    text='Our team operates under a remote-first model; we can live anywhere in Brazil.\nRequirements:\nJava'
    result=qualify(job(text,location='Brasil'),FACTS)
    assert result['state']=='eligible' and 'can live anywhere in Brazil' in result['evidence']
    assert qualify(job(text.replace('Brazil','Argentina'),location='Argentina'),FACTS)['state']=='pending'
    assert qualify(job(text,location='Curitiba | On-site'),FACTS)['state']=='discarded'
    assert qualify(job(text+'\nWork Type: Hybrid',location='Sao Paulo, Brazil'),FACTS)['state']=='discarded'


def test_specialist_accounting_and_production_ml_are_not_implied_by_general_engineering():
    assert qualify(job('Requirements:\nPython',title='Software Engineer II with Accounting Experience'),FACTS)['state']=='pending'
    description='Requirements:\n2+ years of software engineering experience, with a strong focus on ML engineering and deploying machine learning models in production.'
    assert qualify(job(description),FACTS)['reason']=='specific_production_ml_requirement'
