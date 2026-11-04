"""Search-results cards are keyed by job id; anchors from the older layout still count."""
import pytest
from playwright.async_api import async_playwright

from core.easy_service import CARD_LINKS


async def test_card_links_read_job_ids_and_titles_from_both_layouts():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page = await browser.new_page()
            await page.set_content('''
              <a href="https://www.linkedin.com/jobs/view/111111111/">Old layout</a>
              <div role="button" componentkey="job-card-component-ref-222222222">
                <p>Backend Developer</p><button aria-label="Fechar vaga de Backend Developer">x</button></div>
              <div role="button" componentkey="job-card-component-ref-333333333">
                <button aria-label="Dismiss Senior Engineer">x</button></div>
              <div componentkey="job-card-component-ref-not-an-id"></div>''')
            links = dict(await page.evaluate(CARD_LINKS))
        finally:
            await browser.close()
    assert links == {'https://www.linkedin.com/jobs/view/111111111/': '',
                     'https://www.linkedin.com/jobs/view/222222222/': 'Backend Developer',
                     'https://www.linkedin.com/jobs/view/333333333/': 'Senior Engineer'}


@pytest.mark.parametrize('chips,expected',[(['Remoto','Tempo integral'],'remote'),(['Híbrido'],'hybrid'),(['Presencial'],'on-site'),([],''),(['Remoto','Híbrido'],'')])
async def test_job_metadata_reads_the_header_workplace_chip(chips,expected):
    from core.easy_apply import read_job_metadata
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page = await browser.new_page()
            header=''.join(f'<div>{c}</div>' for c in chips)
            await page.set_content(f'''<title>Backend Developer | Fictional Co | LinkedIn</title><main>
              <div>Backend Developer</div><div>Brasil · há 2 dias</div>{header}
              <h2>Sobre a vaga</h2><p>Trabalho remoto ocasional.</p><div>Remoto</div></main>''')
            metadata = await read_job_metadata(page)
        finally:
            await browser.close()
    assert metadata['workplaceType']==expected and metadata['location']=='Brasil'


def test_linkedin_remote_chip_counts_as_remote_arrangement():
    from core.external_qualification import qualify
    facts={'location.country':{'value':'Brazil'},'experience.years':{'value':3}}
    job={'source':'linkedin','title':'Backend Developer','location':'Brasil','description':'Requisitos:\nJava'}
    assert qualify(dict(job,workplaceType='remote'),facts)['state']=='eligible'
    assert qualify(job,facts)['reason']=='working_arrangement_needs_evidence'
    assert qualify(dict(job,workplaceType='hybrid',location='Lisboa'),facts)['state']=='discarded'


def test_pcd_only_titles_wait_for_confirmed_eligibility():
    from core.external_qualification import qualify
    facts={'location.country':{'value':'Brazil'},'experience.years':{'value':3}}
    job={'source':'linkedin','title':'Analista Back End -PCD','location':'Brasil','description':'Requisitos:\nJava','workplaceType':'remote'}
    assert qualify(job,facts)['reason']=='affirmative_eligibility_not_confirmed'
