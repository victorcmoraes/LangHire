"""Own one persistent Chrome profile and attach browser-use over local CDP.

browser-use 0.13 copies Chrome profiles to a temporary directory when it launches
them. Owning Chrome here preserves session changes and scopes cleanup correctly.
The caller must hold the profile's exclusive worker lease.
"""
from contextlib import asynccontextmanager
from pathlib import Path


@asynccontextmanager
async def persistent_browser(data_dir: Path, *, headless=False):
    from browser_use import BrowserSession
    from playwright.async_api import async_playwright
    profile = Path(data_dir) / "browser_profile"
    profile.mkdir(parents=True, exist_ok=True)
    playwright = await async_playwright().start()
    context = None
    session = None
    try:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile), channel="chrome", headless=headless,
            args=["--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1"],
        )
        port = int((profile / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()[0])
        session = BrowserSession(cdp_url=f"http://127.0.0.1:{port}", is_local=False,
                                 keep_alive=True, captcha_solver=False, enable_default_extensions=False)
        yield session
    finally:
        try:
            if session:
                await session.kill()
        finally:
            try:
                if context:
                    await context.close()
            finally:
                await playwright.stop()
