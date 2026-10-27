"""Supervised pilot: prepare, inspect and explicitly release one reviewed attempt.

Control files are local operator input, never page/model instructions. Submission
is permitted only for the exact reviewed form snapshot; the normal queue stays off.
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LANGHIRE_DATA_DIR", str(ROOT / ".local"))
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
from browser_use import BrowserSession
from filelock import FileLock
from backend.core.application_safety import AttemptStore, ACTIVE
from backend.core.config import get_data_dir
from backend.core.submission_monitor import SubmissionMonitor, ERROR
from backend.core.browser_runtime import persistent_browser
from backend.core.pilot_review import brazil_location_pattern, accepted_brazil_location

INSPECT = """() => [...document.querySelectorAll('input,textarea,select')].map((e,i)=>{
 const row=e.closest('li')||e.closest('.application-question');
 const label=row?.querySelector('.application-label')?.innerText||
   [...(e.labels||[])].map(x=>x.innerText).join(' ')||e.getAttribute('aria-label')||
   row?.innerText?.split('Select...')[0]||e.parentElement?.innerText?.slice(0,600)||'';
 return {i,tag:e.tagName,type:e.type,id:e.id,name:e.name,label,
 required:e.required||e.getAttribute('aria-required')==='true'||/[✱*]/.test(label),
 visible:!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length),
 checked:e.checked,value:e.type==='file'?[...e.files].map(f=>f.name):e.value,
 options:e.options?[...e.options].map(o=>({value:o.value,text:o.text})):null};
})"""


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def run(args):
    data = get_data_dir()
    with FileLock(str(data / "application-worker.lock"), timeout=0):
        canonical = args.url.removesuffix("/apply").rstrip("/")
        store = AttemptStore(data)
        attempt = store.begin(canonical)
        directory = Path(store.get(attempt)["evidence_dir"])
        control = directory / "control.json"
        expected = []
        observed_seq = None
        profile = json.loads((data / "confirmed_facts.json").read_text(encoding="utf-8"))
        facts = {fact["id"] for fact in profile["facts"] if not fact.get("conflict")}
        policies = {"policy.application-consent", "policy.discovery-source", "policy.salary-delegation", "policy.not-applicable"}
        save(directory / "candidate-source.json", profile)
        runtime = persistent_browser(data)
        browser = None
        page = None

        async def snapshot():
            return [field for field in await page.evaluate(INSPECT)
                    if field["visible"] and field.get("name") not in ("h-captcha-response", "g-recaptcha-response")]

        async def review():
            fields = await snapshot()
            invalid = []
            for field in fields:
                if field["required"] and field["type"] == "file" and not field["value"]:
                    invalid.append(field["label"] or "Required document missing")
                if field["required"] and field["type"] not in ("checkbox", "radio", "file") and not str(field["value"] or "").strip():
                    invalid.append(field["label"] or field["name"] or field["id"])
                if field["required"] and field["type"] in ("checkbox", "radio"):
                    group = [f for f in fields if f["name"] and f["name"] == field["name"]]
                    if not any(f["checked"] for f in group or [field]):
                        invalid.append(field["label"])
            for action in expected:
                locator = page.locator(action["selector"])
                if action["kind"] == "check": actual = await locator.is_checked()
                elif action["kind"] == "file":
                    actual = await locator.evaluate("e=>[...e.files].map(f=>f.name)")
                    if not actual and await page.locator('input[name="resumeStorageId"]').count():
                        if await page.locator('input[name="resumeStorageId"]').input_value():
                            if action["value"][0] in await page.locator("body").inner_text(): actual = action["value"]
                else: actual = await locator.input_value()
                if actual != action["value"]:
                    invalid.append("Accepted value differs: " + action["selector"])
                if action["kind"] == "location":
                    token = await page.locator("#selected-location").input_value()
                    if not accepted_brazil_location(token, action["city"], action["state"]):
                        invalid.append("Location was not accepted by autocomplete")
            text = await page.locator("body").inner_text()
            if ERROR.search(text): invalid.append("Portal verification/submission error is visible")
            digest = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
            return {"ready": bool(expected) and not invalid, "reason": "; ".join(dict.fromkeys(invalid)),
                    "digest": digest, "fields": fields, "answers": expected}

        monitor = SubmissionMonitor(store, attempt, pre_submit_review=review)
        try:
            browser = await runtime.__aenter__()
            await monitor.attach(browser)
            context = monitor.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3500)
            save(directory / "form-initial.json", await snapshot())
            (directory / "form-initial.html").write_text(await page.content(), encoding="utf-8")
            await page.screenshot(path=str(directory / "form-initial.png"), full_page=True)
            save(data / "pilot-current.json", {"attempt_id": attempt, "url": canonical, "directory": str(directory), "control": str(control)})
            print(f"Pilot prepared. Attempt={attempt}; control={control}", flush=True)
            while True:
                if not control.exists():
                    await asyncio.sleep(0.5)
                    continue
                command = json.loads(control.read_text(encoding="utf-8"))
                if command.get("seq") == observed_seq:
                    await asyncio.sleep(0.5)
                    continue
                observed_seq = command.get("seq")
                if command["action"] == "fill":
                    errors = []
                    for action in command.get("fields", []):
                        if not action.get("facts") or any(ref not in facts | policies for ref in action["facts"]):
                            errors.append("Unsupported provenance: " + action["selector"])
                            continue
                        locator = page.locator(action["selector"])
                        try:
                            if action["kind"] == "select": await locator.select_option(action["value"])
                            elif action["kind"] == "check": await locator.set_checked(action["value"])
                            elif action["kind"] == "file":
                                pdf = Path(action["path"]).resolve()
                                if pdf.parent != (data / "resumes").resolve(): raise ValueError("Only configured local resumes may be uploaded")
                                await locator.set_input_files(str(pdf))
                                action["value"] = [pdf.name]
                                action["sha256"] = hashlib.sha256(pdf.read_bytes()).hexdigest()
                            elif action["kind"] == "location":
                                action["city"] = action["value"]
                                await locator.fill("")
                                await locator.press_sequentially(action["value"], delay=50)
                                candidates = page.locator(".dropdown-results > *").filter(
                                    has_text=brazil_location_pattern(action["value"], action["state"]))
                                await candidates.first.wait_for(timeout=12000)
                                await candidates.first.click()
                                token = await page.locator("#selected-location").input_value()
                                if not token: raise ValueError("Autocomplete did not accept a location")
                                for _ in range(20):
                                    if accepted_brazil_location(token, action["city"], action["state"]):
                                        break
                                    await asyncio.sleep(0.5)
                                    token = await page.locator("#selected-location").input_value()
                                if not accepted_brazil_location(token, action["city"], action["state"]):
                                    raise ValueError("Autocomplete did not confirm the city, state and country")
                                action["value"] = await locator.input_value()
                                action["location_digest"] = hashlib.sha256(token.encode()).hexdigest()
                            else: await locator.fill(action["value"])
                            expected = [item for item in expected if item["selector"] != action["selector"]] + [action]
                        except Exception as error:
                            errors.append(f"{action['selector']}: {str(error)[:300]}")
                    await page.wait_for_timeout(2000)
                    result = await review()
                    result["fill_errors"] = errors
                    if errors: result["ready"] = False
                    save(directory / f"review-{observed_seq}.json", result)
                    await page.screenshot(path=str(directory / f"review-{observed_seq}.png"), full_page=True)
                    print(f"Review {observed_seq}: ready={result['ready']}; missing={result['reason']}; fill_errors={len(errors)}", flush=True)
                elif command["action"] == "submit":
                    result = await review()
                    if not result["ready"] or command.get("digest") != result["digest"]:
                        save(directory / f"submit-blocked-{observed_seq}.json", result)
                        print("Submit refused: review incomplete or snapshot changed", flush=True)
                        continue
                    save(directory / "review-approved.json", result)
                    store.event(attempt, "pilot_operator_review", {"digest": result["digest"], "answers": expected})
                    monitor.arm_submission(canonical + "/apply")
                    await page.get_by_role("button", name="Submit application", exact=True).click()
                    outcome = await monitor.finalize()
                    save(directory / "outcome.json", outcome)
                    print(json.dumps({"attempt": attempt, "outcome": outcome["state"], "message": outcome["message"]}), flush=True)
                    return outcome
                elif command["action"] == "inspect":
                    save(directory / f"inspection-{observed_seq}.json", await snapshot())
                elif command["action"] == "stop":
                    break
        except BaseException as error:
            store.event(attempt, "pilot_error", {"type": type(error).__name__, "message": str(error)[:1000]})
            raise
        finally:
            if store.get(attempt)["state"] in ACTIVE:
                store.transition(attempt, "uncertain" if store.submissions(attempt) else "blocked",
                                 "Pilot stopped; inspect evidence before continuing")
            try: await monitor.close()
            finally:
                if browser: await runtime.__aexit__(None, None, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
