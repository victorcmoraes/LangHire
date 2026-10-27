"""Small browser navigation diagnostic on intercepted fixtures, never real jobs.

Passing this diagnostic does NOT approve a model for unsupervised applications.
"""
import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
from browser_use import Agent, BrowserSession, Tools
from backend.core.application_safety import AttemptStore
from backend.core.llm_factory import create_llm
from backend.core.submission_monitor import SubmissionMonitor


async def evaluate(model, output):
    started = time.monotonic()
    # Browser/LLM see only fictional data, with every navigation intercepted.
    with tempfile.TemporaryDirectory(prefix="langhire-navigation-") as temporary:
        store = AttemptStore(Path(temporary) / "attempts")
        attempt = store.begin("https://jobs.lever.co/example/fixture")
        browser = BrowserSession(channel="chrome", headless=True, keep_alive=True,
                                 user_data_dir=str(Path(temporary) / "browser"),
                                 captcha_solver=False, enable_default_extensions=False)
        monitor = SubmissionMonitor(store, attempt)
        received = []
        await monitor.attach(browser)
        context = monitor.contexts[0]
        html = """<h1>Application form</h1><form>
        <label>Full name<input name='name' required></label>
        <label>Email<input name='email' type='email' required></label>
        <button type='submit'>Submit application</button></form><script>
        document.querySelector('form').onsubmit=async e=>{
          e.preventDefault();await fetch('/example/fixture/apply',
          {method:'POST',body:new URLSearchParams(new FormData(e.target))});
          document.body.textContent='Application submitted';
        };</script>"""
        async def fixture(route):
            if route.request.url.startswith("https://jobs.lever.co/example/fixture"):
                if route.request.method == "POST":
                    received.append(parse_qs(route.request.post_data or ""))
                    await route.fulfill(status=200, body="ok")
                else:
                    await route.fulfill(content_type="text/html", body=html)
            else:
                await route.abort("blockedbyclient")
        await context.route("**/*", fixture)
        await context.unroute("**/*", monitor.route)
        await context.route("**/*", monitor.route)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://jobs.lever.co/example/fixture")
        await page.bring_to_front()
        agent = Agent(
            task="Use ONLY the already-open application form. Fill Full name with Test Candidate, "
                 "Email with test@example.invalid, then click Submit application ONCE. Do not search or open other sites. /no_think",
            llm=create_llm({"provider": "ollama", "ollama": {"model": model, "base_url": "http://127.0.0.1:11434"}}),
            browser_session=browser, use_vision=False, use_thinking=False,
            tools=Tools(exclude_actions=["evaluate", "search", "write_file", "replace_file", "read_file"]),
            max_actions_per_step=1, max_failures=1, llm_timeout=90,
            use_judge=False, enable_planning=False, final_response_after_failure=False,
        )
        result = {"model": model, "diagnostic_only": True, "passed": False}
        try:
            history = await agent.run(max_steps=8, on_step_start=monitor.guard, on_step_end=monitor.guard)
            outcome = await monitor.finalize()
            result.update(outcome=outcome["state"], steps=len(history.history), received=received,
                          passed=outcome["state"] == "confirmed" and received == [
                              {"name": ["Test Candidate"], "email": ["test@example.invalid"]}])
        except Exception as error:
            result["error"] = str(error)[:1000]
        finally:
            await monitor.close()
            await browser.kill()
        result["seconds"] = round(time.monotonic() - started, 2)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/navigation-diagnostic.json")
    args = parser.parse_args()
    asyncio.run(evaluate(args.model, args.output))


if __name__ == "__main__":
    main()
