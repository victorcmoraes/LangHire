import json
import pytest
from core.arrangement_evidence import extract,verified,description_hash,cache_key,load_cache,PROMPT_VERSION
from core.easy_answers import AnswerBook
from core.external_qualification import qualify,triage


def job(description,**fields):
    return dict({'platform':'greenhouse','source':'greenhouse','board':'fixture','externalId':'1',
                 'title':'Backend Engineer','location':'Unspecified','description':description,
                 'url':'https://job-boards.greenhouse.io/fixture/jobs/1'},**fields)


def entry(posting,mode='unknown',mode_quote=None,brazil='unknown',brazil_quote=None):
    return {'description_hash':description_hash(posting),'model':'qwen3:8b','prompt_version':PROMPT_VERSION,
            'mode':mode,'mode_quote':mode_quote,'brazil':brazil,'brazil_quote':brazil_quote}


def seed(path,posting):
    (path/'external').mkdir()
    (path/'jobs.json').write_text(json.dumps({posting['url']:posting}),encoding='utf-8')
    (path/'external/catalog.json').write_text(json.dumps({'jobs':[posting],'boards':[]}),encoding='utf-8')
    (path/'llm_settings.json').write_text(json.dumps({'provider':'ollama','ollama':{'model':'qwen3:8b'}}),encoding='utf-8')


def decision(posting,proposal):
    return qualify(dict(posting,arrangement_evidence=verified(posting,proposal)),{})


def test_quotes_must_be_verbatim_with_only_case_and_whitespace_ignored():
    posting=job('This role is fully remote within LATAM.')
    valid=entry(posting,'remote','THIS role is fully\n remote within LATAM.')
    assert verified(posting,valid)['mode']=='remote'
    for quote in ('This position is fully remote within LATAM.','fully remote within Latin America.',None,14):
        assert verified(posting,dict(valid,mode_quote=quote))['mode']=='unknown'
    for quote in ('Remote', 'This role is fully remote. '+'x'*400):
        posting=job(quote)
        assert verified(posting,entry(posting,'remote',quote))['mode_quote'] is None


@pytest.mark.parametrize('quote',[
    'Benefits include fully remote working weeks.',
    'This role is remote on occasional Fridays.',
    'This role is remote with eventual office visits.',
    'This role is remote with flexible office days.',
    'This role is remote but requires 3 days per week in-office.',
    'Trabalho remoto como benefício ocasional.',
    'This role is not fully remote.',
])
def test_benefits_occasional_and_negated_remote_are_rejected(quote):
    posting=job(quote)
    assert verified(posting,entry(posting,'remote',quote))['mode']=='unknown'


def test_short_quote_cannot_hide_benefit_heading_or_negation():
    for text in ('Benefits:\nThis role is fully remote.', 'Benefits:\nThis role is fully\nremote.', 'This role is not a fully remote position.'):
        posting=job(text)
        assert verified(posting,entry(posting,'remote','fully remote position' if 'not' in text else 'This role is fully remote.'))['mode']=='unknown'


@pytest.mark.parametrize('quote,mode',[
    ('Atuamos no modelo híbrido, 3x por semana no escritório.','hybrid'),
    ('This role is an on-site position in another city.','onsite'),
])
def test_office_arrangement_discards_outside_home_city(quote,mode):
    posting=job(quote,location='Sao Paulo, Brazil')
    result=decision(posting,entry(posting,mode,quote))
    assert result['reason']=='onsite_or_hybrid_outside_home_region'
    assert quote in result['evidence']


@pytest.mark.parametrize('quote',[
    'This role is remote within LATAM.',
    'Atuamos no modelo remote-first e de qualquer lugar do Brasil.',
    'This is a fully remote role open to candidates in Latin America.',
    'Trabalho remoto para residentes da América Latina.',
    'This role is remote within South America.',
])
def test_verified_remote_and_brazil_make_eligible(quote):
    posting=job(quote)
    result=decision(posting,entry(posting,'remote',quote,'allowed',quote))
    assert result['state']=='eligible'
    assert quote in result['evidence']


def test_company_history_mentioning_brazil_is_not_eligibility():
    quote='Somos a maior empresa independente de meios de pagamentos do Brasil, com milhares de pessoas'
    posting=job(quote+'.',location='Remoto')
    assert verified(posting,entry(posting,brazil='allowed',brazil_quote=quote))['brazil']=='unknown'
    assert decision(posting,entry(posting,brazil='allowed',brazil_quote=quote))['state']=='pending'


@pytest.mark.parametrize('quote',['Auxílio home office * (apenas para contratos Híbridos ou Remotos)','Contratos Híbridos ou Remotos'])
def test_hybrid_or_remote_contract_policy_does_not_discard_remote_location(quote):
    posting=job('Nosso pacote de Remuneração e Benefícios:\n'+quote+'\nOutro item',location='Remoto')
    assert verified(posting,entry(posting,'hybrid',quote))['mode']=='unknown'
    assert decision(posting,entry(posting,'hybrid',quote))['reason']=='geographic_eligibility_needs_evidence'


def test_remote_first_flexibility_policy_is_remote_evidence():
    quote='Atuamos no modelo remote-first, oferecendo total flexibilidade para você trabalhar de qualquer lugar do Brasil'
    posting=job('Localização e Modelo de Trabalho\n'+quote+'.',location='São Paulo, Brazil')
    assert decision(posting,entry(posting,'remote',quote,'allowed',quote))['state']=='eligible'


def test_remote_quote_does_not_override_metadata_or_explicit_nonremote_mode():
    quote='This role is remote within LATAM.'
    for fields in ({'location':'Sao Paulo, Brazil (Hybrid)'},{'title':'Backend Engineer - On-site'}):
        posting=job(quote,**fields)
        assert decision(posting,entry(posting,'remote',quote,'allowed',quote))['state']=='discarded'
    posting=job(quote+'\nModalidade: Presencial',location='Sao Paulo, Brazil')
    assert decision(posting,entry(posting,'remote',quote,'allowed',quote))['state']=='discarded'


def test_hash_change_invalidates_both_parts():
    quote='This role is remote within LATAM.';posting=job(quote)
    proposal=entry(posting,'remote',quote,'allowed',quote)
    result=verified(dict(posting,description=quote+' Updated.'),proposal)
    assert result=={'mode':'unknown','mode_quote':None,'brazil':'unknown','brazil_quote':None}


def test_excluded_brazil_discards_even_with_brazil_metadata():
    quote='This remote role is not available in Brazil.';posting=job(quote,location='Brazil (Remote)')
    result=decision(posting,entry(posting,brazil='excluded',brazil_quote=quote))
    assert result['reason']=='brazil_excluded_by_posting' and quote in result['evidence']
    assert verified(posting,entry(posting,brazil='allowed',brazil_quote=quote))['brazil']=='unknown'
    quote='This role is remote within LATAM except Mexico.';posting=job(quote)
    assert verified(posting,entry(posting,brazil='excluded',brazil_quote=quote))['brazil']=='unknown'


@pytest.mark.asyncio
async def test_model_failure_leaves_pending(tmp_path,monkeypatch):
    posting=job('We build backend services.');seed(tmp_path,posting)
    async def fail(*args,**kwargs):raise RuntimeError('fake failure')
    monkeypatch.setattr(AnswerBook,'generate',fail)
    report=await extract(tmp_path)
    assert report['checked']==1 and report['failed']==1
    assert triage(tmp_path)['states']=={'pending':1}
    assert not load_cache(tmp_path)


@pytest.mark.asyncio
async def test_cache_reuse_no_second_call_and_description_model_version_invalidation(tmp_path,monkeypatch):
    quote='This role is remote within LATAM.';posting=job(quote);seed(tmp_path,posting);calls=[]
    async def fake(self,answer_id,settings,payload,purpose=None):
        calls.append(payload)
        assert payload['think'] is False and payload['format']['type']=='object'
        return {'mode':'remote','mode_quote':quote,'brazil':'allowed','brazil_quote':quote}
    monkeypatch.setattr(AnswerBook,'generate',fake)
    report=await extract(tmp_path)
    assert report['remote']==report['brazil_allowed']==1
    assert triage(tmp_path)['states']=={'eligible':1}
    assert (await extract(tmp_path))['cached']==1 and len(calls)==1
    path=tmp_path/'external/catalog.json';catalog=json.loads(path.read_text(encoding='utf-8'))
    catalog['jobs'][0]['description']+=' Updated.';path.write_text(json.dumps(catalog),encoding='utf-8')
    assert triage(tmp_path)['states']=={'pending':1}
    assert (await extract(tmp_path))['cached']==0 and len(calls)==2
    cache=load_cache(tmp_path);cache[cache_key(posting)]['prompt_version']=0
    (tmp_path/'external/arrangement-evidence.json').write_text(json.dumps(cache),encoding='utf-8')
    assert (await extract(tmp_path))['cached']==0 and len(calls)==3
    (tmp_path/'llm_settings.json').write_text(json.dumps({'provider':'ollama','ollama':{'model':'fixture:8b'}}),encoding='utf-8')
    (tmp_path/'settings.json').write_text(json.dumps({'automation_answers':{'model':'fixture:8b'}}),encoding='utf-8')
    assert (await extract(tmp_path))['cached']==0 and len(calls)==4
    assert load_cache(tmp_path)[cache_key(posting)]['model']=='fixture:8b'


@pytest.mark.asyncio
async def test_rejected_quotes_counted_and_remote_url_never_called(tmp_path,monkeypatch):
    posting=job('Benefits include fully remote weeks in Brazil.');seed(tmp_path,posting);calls=[]
    async def fake(*args,**kwargs):
        calls.append(1)
        return {'mode':'remote','mode_quote':posting['description'],'brazil':'unknown','brazil_quote':None}
    monkeypatch.setattr(AnswerBook,'generate',fake)
    report=await extract(tmp_path)
    assert report['rejected_quotes']==1 and triage(tmp_path)['states']=={'pending':1}
    assert (await extract(tmp_path))['cached']==1 and len(calls)==1
    settings={'provider':'ollama','ollama':{'base_url':'https://example.com','model':'qwen3:8b'}}
    (tmp_path/'llm_settings.json').write_text(json.dumps(settings),encoding='utf-8')
    (tmp_path/'external/arrangement-evidence.json').unlink()
    assert (await extract(tmp_path))['failed']==1 and len(calls)==1


def test_remote_evidence_keeps_explicit_residence_restriction():
    quote='This role is remote within LATAM.'
    posting=job(quote+'\nEligible Locations: Sao Paulo, Brazil')
    result=decision(posting,entry(posting,'remote',quote,'allowed',quote))
    assert result['reason']=='explicit_residence_restriction_not_supported'
    assert quote in result['evidence']


@pytest.mark.asyncio
async def test_limit_and_unrelated_pending_reasons_are_preserved(tmp_path,monkeypatch):
    quote='This role is remote within LATAM.';posting=job(quote);seed(tmp_path,posting);calls=[]
    catalog={'jobs':[posting,dict(posting,externalId='2',url=posting['url']+'2'),
                     dict(posting,externalId='3',url=posting['url']+'3',description='Requirements:\n6 years of backend experience')], 'boards':[]}
    (tmp_path/'external/catalog.json').write_text(json.dumps(catalog),encoding='utf-8')
    (tmp_path/'jobs.json').write_text(json.dumps({j['url']:j for j in catalog['jobs']}),encoding='utf-8')
    async def fake(*args,**kwargs):
        calls.append(1)
        return {'mode':'remote','mode_quote':quote,'brazil':'allowed','brazil_quote':quote}
    monkeypatch.setattr(AnswerBook,'generate',fake)
    assert (await extract(tmp_path,limit=1))['checked']==1 and len(calls)==1
    assert triage(tmp_path)['states']=={'eligible':1,'pending':2}


def test_evidence_cli_runs_extraction_then_triage(tmp_path,monkeypatch,capsys):
    monkeypatch.setenv('LANGHIRE_DATA_DIR',str(tmp_path))
    import cli.external as external
    import backend.core.arrangement_evidence as evidence
    posting=job('Requirements:\nJava');seed(tmp_path,posting);calls=[]
    async def fake(data,limit=None,progress=None):
        calls.append((data,limit))
        return {'checked':1,'cached':0,'rejected_quotes':0}
    monkeypatch.setattr(evidence,'extract',fake)
    monkeypatch.setattr(external,'get_data_dir',lambda:tmp_path)
    monkeypatch.setattr('sys.argv',['external','evidence','--limit','1'])
    external.main()
    report=json.loads(capsys.readouterr().out)
    assert calls==[(tmp_path,1)] and report['triage']=={'pending':1}
