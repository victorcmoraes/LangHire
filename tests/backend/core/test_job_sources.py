import json

import pytest

from backend.core import job_sources
from backend.core.application_safety import SafetyError, job_key
from backend.core.apply_assist import canonical, platform_of
from backend.core.external_apply import canonical_ats
from backend.core.external_boards import LINK
from backend.core.external_catalog import gupy_job
from backend.core.external_discovery import board_spec


def test_company_names_become_ats_board_slugs():
    assert job_sources.slugs('Acme Labs, Inc.') == ['acmelabs', 'acme-labs', 'acme']
    assert job_sources.slugs('Hand Talk') == ['handtalk', 'hand-talk', 'hand']
    assert job_sources.slugs('São João Tecnologia Ltda') == ['saojoaotecnologia', 'sao-joao-tecnologia', 'saojoao', 'sao-joao']
    assert job_sources.slugs('') == []


def test_candidates_put_direct_links_first_and_skip_known_boards():
    companies = {'Hand Talk':{'source':'himalayas', 'titles':['Backend Developer']}}
    links = {'https://softdesign.gupy.io/':{'source':'gupy_portal', 'titles':['Software Engineer']}}
    rows = job_sources.candidates(companies, links, {('lever', 'handtalk')})
    assert rows[0]['platform'] == 'gupy' and rows[0]['board'] == 'softdesign'
    keys = {(r['platform'], r['board']) for r in rows}
    assert ('greenhouse', 'handtalk') in keys and ('lever', 'handtalk') not in keys and ('workable', 'hand-talk') in keys


def test_gupy_urls_have_one_identity_everywhere():
    url = 'https://softdesign.gupy.io/jobs/12816323'
    assert job_key(url+'?jobBoardSource=gupy_portal') == job_key(url) == 'gupy:softdesign:12816323'
    assert canonical_ats(url+'/') == url
    assert board_spec('https://softdesign.gupy.io/') == {'platform':'gupy', 'board':'softdesign'}
    assert board_spec('gupy:softdesign') == {'platform':'gupy', 'board':'softdesign'}
    with pytest.raises(SafetyError):board_spec('gupy:login')
    assert LINK.findall('Inscreva-se: https://bhs.gupy.io/jobs/1 ou https://login.gupy.io/x') == ['https://bhs.gupy.io']
    assert platform_of('https://softdesign.gupy.io/candidates/jobs/12816323/apply?x=1') == 'gupy'
    assert canonical('https://softdesign.gupy.io/candidates/jobs/12816323/apply?x=1') == url


def test_gupy_posting_maps_to_a_catalog_job():
    job = gupy_job({'id':7, 'name':' Backend Java Pleno ', 'workplaceType':'remote', 'addressCity':'', 'addressCountryShortName':'',
                    'description':'<p>Sobre</p>', 'prerequisites':'<ul><li>Java</li></ul>', 'careerPage':{'name':'SoftDesign'}}, 'softdesign')
    assert job['url'] == 'https://softdesign.gupy.io/jobs/7' and job['title'] == 'Backend Java Pleno'
    assert job['location'] == 'Brasil (remote)' and job['country'] == 'BR' and 'Java' in job['description']
    assert job['company'] == 'SoftDesign'


def test_linkedin_external_destinations_feed_board_discovery(tmp_path):
    (tmp_path/'jobs.json').write_text(json.dumps({
        'a':{'source':'linkedin', 'title':'Dev', 'external_url':'https://job-boards.greenhouse.io/acme/jobs/1?gh_src=li'},
        'b':{'source':'linkedin', 'title':'Dev 2', 'external_url':'https://careers.example.com/apply/9'},
        'c':{'source':'lever', 'title':'Other', 'external_url':'https://jobs.lever.co/x/y'}}), encoding='utf-8')
    assert list(job_sources.linkedin_links(tmp_path)) == ['https://job-boards.greenhouse.io/acme']
