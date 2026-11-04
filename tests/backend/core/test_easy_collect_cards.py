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
