from playwright.async_api import async_playwright
from core.browser_runtime import persistent_browser


async def test_chrome_profile_keeps_cookies_across_browser_use_sessions(tmp_path):
    async with persistent_browser(tmp_path, headless=True) as session:
        await session.start()
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(session.cdp_url)
            await browser.contexts[0].add_cookies([
                {"name": "fixture_session", "value": "persisted", "domain": "example.invalid", "path": "/",
                 "expires": 2000000000},
            ])
    assert (tmp_path / "browser_profile" / "Default").exists()
    async with persistent_browser(tmp_path, headless=True) as session:
        await session.start()
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(session.cdp_url)
            cookies = await browser.contexts[0].cookies("https://example.invalid/")
            assert any(cookie["name"] == "fixture_session" and cookie["value"] == "persisted" for cookie in cookies)
