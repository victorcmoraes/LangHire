"""Real Chromium + local intercepted fixtures. No real ATS requests or LLMs."""
import asyncio
import json

import pytest
from playwright.async_api import async_playwright
from core.application_safety import AttemptStore
from core.submission_monitor import SubmissionMonitor, is_submission, clean_url


@pytest.fixture
async def fixture_portal(tmp_path):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        context = await browser.new_context()
        data = {"status": 200, "received": 0, "challenge": False, "json_rejection": False, "body": b""}
        html = """<form id='application'><input name='email' required value='test@example.invalid'>
          <input type='file' name='resume'><button>Submit application</button></form>
          <script>
          document.querySelector('form').onsubmit=async e=>{
            e.preventDefault(); const response=await fetch('/acme/job/apply',{method:'POST',body:new FormData(e.target)});
            const result=await response.json();
            document.body.innerHTML=result.challenge
              ? '<div data-captcha-challenge style="width:300px;height:200px">Human challenge</div>'
              : result.message;
          };
          </script>"""
        async def serve(route):
            if route.request.method == "POST":
                data["received"] += 1
                data["body"] = route.request.post_data_buffer
                await route.fulfill(status=data["status"], content_type="application/json",
                                    body=json.dumps({"challenge": data["challenge"], "message": "Application submitted",
                                                     "success": not data["json_rejection"]}))
            else:
                await route.fulfill(content_type="text/html", body=html)
        await context.route("**/*", serve)
        store = AttemptStore(tmp_path)
        attempt = store.begin("https://jobs.lever.co/acme/job")
        monitor = SubmissionMonitor(store, attempt)
        await monitor.attach_context(context)
        page = await context.new_page()
        await page.goto("https://jobs.lever.co/acme/job")
        yield page, data, store, attempt, monitor
        await monitor.close()
        await browser.close()


async def click_submit(page, monitor):
    await page.get_by_role("button", name="Submit application").click()
    await page.wait_for_function("!document.querySelector('form')")
    if monitor.response_tasks:
        await asyncio.gather(*monitor.response_tasks)


async def test_submit_confirmed_by_http_and_dom_with_evidence(fixture_portal):
    page, data, store, attempt, monitor = fixture_portal
    await click_submit(page, monitor)
    outcome = await monitor.finalize()
    assert outcome["state"] == "confirmed"
    assert data["received"] == 1
    assert list(monitor.directory.glob("*.png"))
    assert list(monitor.directory.glob("*.json"))


async def test_http_500_with_success_toast_is_not_confirmation(fixture_portal, monkeypatch):
    page, data, store, attempt, monitor = fixture_portal
    data["status"] = 500
    await click_submit(page, monitor)
    # Avoid waiting ten seconds for a deliberately impossible confirmation.
    async def no_wait(_): pass
    monkeypatch.setattr("core.submission_monitor.asyncio.sleep", no_wait)
    assert (await monitor.finalize())["state"] == "uncertain"


async def test_model_success_without_a_send_is_not_confirmation(fixture_portal):
    page, _, store, attempt, monitor = fixture_portal
    await page.set_content("Application submitted")
    assert (await monitor.finalize())["state"] == "uncertain"


@pytest.mark.parametrize("message,expected", [
    ("Application submitted", "confirmed"),
    ("There was an error verifying your application. Please try again.", "uncertain"),
])
async def test_captcha_wait_then_same_attempt_result(fixture_portal, message, expected):
    page, data, store, attempt, monitor = fixture_portal
    data["challenge"] = True
    await click_submit(page, monitor)
    checking = asyncio.create_task(monitor.finalize())
    for _ in range(100):
        if store.get(attempt)["state"] == "awaiting_human": break
        await asyncio.sleep(0.01)
    assert not checking.done()
    assert store.get(attempt)["state"] == "awaiting_human"
    await page.set_content(message)  # Human resolved challenge; portal responds.
    store.acknowledge(attempt)
    assert (await asyncio.wait_for(checking, 5))["state"] == expected
    assert data["received"] == 1
    assert len(store.submissions(attempt)) == 1


async def test_verification_error_overrides_success_text(fixture_portal):
    page, data, store, attempt, monitor = fixture_portal
    await click_submit(page, monitor)
    await page.set_content("Application submitted. There was an error verifying your application.")
    assert (await monitor.finalize())["state"] == "uncertain"


def test_submit_recognition_ignores_analytics_and_sanitizes_tokens():
    assert is_submission("https://jobs.lever.co/acme/job/apply", "POST")
    assert is_submission("https://boards-api.greenhouse.io/v1/boards/acme/jobs/123", "POST")
    assert not is_submission("https://analytics.example.com/apply", "POST")
    assert not is_submission("https://jobs.lever.co/acme/job/apply", "GET")
    assert not is_submission("https://job-boards.greenhouse.io/application/upload", "POST")
    assert clean_url("https://jobs.lever.co/acme/job?token=secret#captcha") == "https://jobs.lever.co/acme/job"


async def test_unrecognized_mutation_cannot_bypass_send_tracking(fixture_portal):
    page, data, store, attempt, monitor = fixture_portal
    result = await page.evaluate("fetch('/new-unrecognized-endpoint', {method:'POST'}).catch(()=>'blocked')")
    assert result == "blocked"
    assert data["received"] == 0
    assert (await monitor.finalize())["state"] == "uncertain"


async def test_second_submit_is_blocked_before_network(fixture_portal):
    page, data, store, attempt, monitor = fixture_portal
    await click_submit(page, monitor)
    await page.evaluate("fetch('/acme/job/apply', {method:'POST'}).catch(()=>'blocked')")
    assert data["received"] == 1
    assert len(store.submissions(attempt)) == 1


async def test_http_200_json_rejection_cannot_be_hidden_by_success_toast(fixture_portal, monkeypatch):
    page, data, store, attempt, monitor = fixture_portal
    data["json_rejection"] = True
    await click_submit(page, monitor)
    async def no_wait(_): pass
    monkeypatch.setattr("core.submission_monitor.asyncio.sleep", no_wait)
    assert (await monitor.finalize())["state"] == "uncertain"


async def test_resume_upload_and_new_confirmation_tab(fixture_portal):
    page, data, store, attempt, monitor = fixture_portal
    await page.locator('input[type="file"]').set_input_files({
        "name": "test-resume.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-test-resume",
    })
    await click_submit(page, monitor)
    assert b"test-resume.pdf" in data["body"]
    assert b"%PDF-test-resume" in data["body"]
    await page.set_content("Processing")
    receipt = await page.context.new_page()
    await receipt.goto("https://jobs.lever.co/acme/job/receipt")
    await receipt.set_content("We have received your application")
    assert (await monitor.finalize())["state"] == "confirmed"
    assert data["received"] == 1


async def test_embedded_ats_form_is_observed_across_frames(fixture_portal):
    page, data, store, attempt, monitor = fixture_portal
    await page.set_content('<iframe src="https://job-boards.greenhouse.io/acme/jobs/123"></iframe>')
    frame = page.frame_locator("iframe")
    await frame.get_by_role("button", name="Submit application").click()
    await frame.get_by_text("Application submitted", exact=True).wait_for()
    assert (await monitor.finalize())["state"] == "confirmed"
    assert data["received"] == 1


async def test_analytics_post_does_not_poison_the_application(fixture_portal):
    page, data, store, attempt, monitor = fixture_portal
    await page.evaluate("fetch('https://www.google-analytics.com/g/collect', {method:'POST'}).catch(()=>null)")
    assert monitor.blocked_request is None
    await click_submit(page, monitor)
    assert (await monitor.finalize())["state"] == "confirmed"


async def test_unresolved_answers_block_before_intent_and_network(fixture_portal):
    page, data, store, attempt, monitor = fixture_portal
    async def review():
        return {"ready": False, "reason": "Required answer has no supporting fact"}
    monitor.pre_submit_review = review
    await page.get_by_role("button", name="Submit application").click()
    await page.wait_for_timeout(100)
    assert data["received"] == 0
    assert store.submissions(attempt) == []
    assert (await monitor.finalize())["state"] == "uncertain"


@pytest.mark.parametrize("path", ["/parseResume", "/cdn-cgi/challenge-platform/h/b/jsd/oneshot/session-token"])
async def test_native_verification_and_resume_parsing_are_not_application_sends(fixture_portal, path):
    page, data, store, attempt, monitor = fixture_portal
    await page.evaluate("path=>fetch(path,{method:'POST'}).then(r=>r.status)", path)
    assert monitor.blocked_request is None
    assert store.submissions(attempt) == []


def test_browser_verification_session_paths_are_not_written_to_logs():
    assert "session-token" not in clean_url("https://jobs.lever.co/cdn-cgi/challenge-platform/h/b/jsd/oneshot/session-token")


async def test_hidden_fullscreen_captcha_iframe_does_not_create_a_human_hold(fixture_portal):
    page, data, store, attempt, monitor = fixture_portal
    await page.set_content('<iframe src="https://newassets.hcaptcha.com/fixture" style="visibility:hidden;position:fixed;width:100%;height:100%"></iframe>')
    assert not any(view["challenge"] for view in await monitor.observe())


async def test_final_click_is_counted_before_transport_and_not_counted_twice(fixture_portal):
    page, data, store, attempt, monitor = fixture_portal
    monitor.arm_submission("https://jobs.lever.co/acme/job/apply")
    assert len(store.submissions(attempt)) == 1
    assert data["received"] == 0
    await click_submit(page, monitor)
    assert (await monitor.finalize())["state"] == "confirmed"
    assert len(store.submissions(attempt)) == 1


async def test_linkedin_widget_tracking_is_not_a_submission_or_fatal_error(fixture_portal):
    page, data, store, attempt, monitor = fixture_portal
    await page.evaluate("fetch('https://www.linkedin.com/li/track', {method:'POST'}).catch(()=>null)")
    assert monitor.blocked_request is None
    assert store.submissions(attempt) == []
