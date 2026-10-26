"""Observe the portal independently of an LLM's success claim.

Intercept known ATS submit requests before forwarding them. Never log request
bodies, cookies, query strings or CAPTCHA tokens. Unsupported paths cannot be
promoted to confirmed by model output.
"""
import asyncio
import json
import re
import uuid
from urllib.parse import urlsplit, urlunsplit

from .application_safety import ACTIVE, AttemptStore, SafetyError

SUCCESS = re.compile(
    r"application (?:has been |was )?(?:successfully )?submitted|"
    r"we (?:have |have successfully )?received your application|thank you for applying|"
    r"candidatura (?:foi )?(?:enviada|recebida)(?: com sucesso)?", re.I)
ERROR = re.compile(
    r"error verifying your application|unable to submit|application (?:was )?not submitted|"
    r"there was an error|erro ao (?:enviar|verificar)|não foi possível enviar", re.I)


def clean_url(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def is_portal(url):
    host = (urlsplit(url).hostname or "").lower()
    return any(host == suffix or host.endswith("." + suffix)
               for suffix in ("lever.co", "greenhouse.io", "linkedin.com"))


def is_submission(url, method):
    if method.upper() != "POST" or not is_portal(url):
        return False
    path = urlsplit(url).path.lower()
    return bool(re.search(r"/(?:apply|applications?|jobapplications|easyapply)$|"
                          r"/boards/[^/]+/jobs/[^/]+$|/jobs/[^/]+/apply$|"
                          r"voyagerjobs.*(?:onsiteapply|jobapplications)|jobs(?:onsiteapply|apply)", path))


class SubmissionMonitor:
    def __init__(self, store: AttemptStore, attempt_id, *, daily_limit=10, cancel=None):
        self.store = store
        self.attempt_id = attempt_id
        self.directory = store.directory / "evidence" / attempt_id
        self.daily_limit = daily_limit
        self.cancel = cancel
        self.playwright = None
        self.browser = None
        self.contexts = []
        self.requests = {}
        self.response_tasks = set()
        self.terminal = False
        self.capture_number = 0
        self.blocked_request = None
        self.rejected_responses = set()

    async def attach(self, session):
        from playwright.async_api import async_playwright
        await session.start()
        if not session.cdp_url:
            raise SafetyError("No local browser connection; cannot observe submission")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.connect_over_cdp(session.cdp_url)
        for context in self.browser.contexts:
            await self.attach_context(context)

    async def attach_context(self, context):
        self.contexts.append(context)
        await context.route("**/*", self.route)
        context.on("response", self.on_response)

    async def route(self, route):
        request = route.request
        if is_submission(request.url, request.method):
            key = uuid.uuid4().hex
            try:
                self.store.intent(self.attempt_id, key, clean_url(request.url), self.daily_limit)
                self.requests[request] = key
                await self.capture("before-submit")
            except Exception as error:
                self.store.event(self.attempt_id, "request_blocked", {"reason": str(error)})
                await route.abort("blockedbyclient")
                return
        elif request.method.upper() == "POST":
            # Unknown mutations are not allowed to slip past the durable send gate.
            # Expand this list only with endpoint-specific fixtures/evidence.
            path = urlsplit(request.url).path.lower()
            host = (urlsplit(request.url).hostname or "").lower()
            telemetry = any(host == suffix or host.endswith("." + suffix) for suffix in (
                "google-analytics.com", "googletagmanager.com", "segment.io", "segment.com", "sentry.io",
                "datadoghq.com", "browser-intake-datadoghq.com", "fullstory.com", "hotjar.com", "mixpanel.com", "amplitude.com",
            ))
            if telemetry:
                await route.abort("blockedbyclient")
                return  # Tracking is not a failed application submission.
            human_challenge = (host == "hcaptcha.com" or host.endswith(".hcaptcha.com") or
                               (host in ("www.google.com", "www.recaptcha.net") and path.startswith("/recaptcha/")))
            auxiliary = is_portal(request.url) and re.search(
                r"/(?:upload|attachments?|analytics|tracking|telemetry|events?|metrics|"
                r"autocomplete|locations?|countries|validate)(?:/|$)", path)
            if host.endswith(".s3.amazonaws.com") and "multipart/form-data" in request.headers.get("content-type", ""):
                auxiliary = True  # A hosted ATS may use a signed object-storage form for its PDF upload.
            if human_challenge:
                await route.fallback()
                return
            if not auxiliary:
                self.blocked_request = clean_url(request.url)
                self.store.event(self.attempt_id, "unsupported_post_blocked", {"url": self.blocked_request})
                await route.abort("blockedbyclient")
                return
        await route.fallback()

    def on_response(self, response):
        if response.request in self.requests:
            task = asyncio.create_task(self.record_response(response))
            self.response_tasks.add(task)
            task.add_done_callback(self.response_tasks.discard)

    async def record_response(self, response):
        try:
            key = self.requests[response.request]
            if "json" in response.headers.get("content-type", ""):
                try:
                    body = await asyncio.wait_for(response.json(), timeout=3)
                    if isinstance(body, dict) and (
                        body.get("success") is False or body.get("ok") is False or
                        body.get("error") or body.get("errors")
                    ):
                        self.rejected_responses.add(key)
                except Exception:
                    self.rejected_responses.add(key)
            self.store.response(key, response.status)
            self.store.event(self.attempt_id, "portal_response",
                             {"url": clean_url(response.url), "status": response.status,
                              "semantic_rejection_or_invalid_json": key in self.rejected_responses})
        except Exception as error:
            self.store.event(self.attempt_id, "capture_error", {"stage": "response", "error": str(error)})

    async def observe(self):
        views = []
        for context in self.contexts:
            for page in context.pages:
                # Read hosted ATS frames as well as the top page, not CAPTCHA scripts.
                for frame in page.frames:
                    if not is_portal(frame.url):
                        continue
                    try:
                        text = await frame.locator("body").inner_text(timeout=2000)
                        challenge = False
                        for element in await frame.locator(
                            'iframe[src*="hcaptcha"], iframe[src*="recaptcha"], [data-captcha-challenge]'
                        ).all():
                            box = await element.bounding_box()
                            if box and box["height"] > 150 and box["width"] > 150:
                                challenge = True
                        views.append({"url": clean_url(frame.url), "text": text[:50000], "challenge": challenge})
                    except Exception as error:
                        self.store.event(self.attempt_id, "capture_error",
                                         {"stage": "observe", "error": str(error)[:300]})
        return views

    async def capture(self, stage):
        self.capture_number += 1
        prefix = f"{self.capture_number:03d}-{stage}"
        views = await self.observe()
        (self.directory / f"{prefix}.json").write_text(
            json.dumps(views, ensure_ascii=False, indent=2), encoding="utf-8")
        for i, page in enumerate(page for ctx in self.contexts for page in ctx.pages):
            try:
                if is_portal(page.url) or any(is_portal(frame.url) for frame in page.frames):
                    await page.screenshot(path=str(self.directory / f"{prefix}-{i}.png"), timeout=5000)
            except Exception as error:
                self.store.event(self.attempt_id, "capture_error",
                                 {"stage": "screenshot", "error": str(error)[:300]})
        return views

    async def finish(self, state, message):
        # Persist durable confirmation BEFORE updating the jobs.json projection.
        await self.capture(state)
        self.store.transition(self.attempt_id, state, message)
        self.terminal = True

    async def guard(self, agent=None):
        if self.terminal:
            if agent:
                agent.stop()
            return
        if self.blocked_request:
            await self.finish("uncertain", "Unrecognized portal POST blocked before network; endpoint requires review")
            if agent:
                agent.stop()
            return
        views = await self.observe()
        if any(view["challenge"] for view in views):
            await self.capture("human-hold")
            await self.store.wait_for_human(
                self.attempt_id, "Resolva o CAPTCHA da candidatura aberta", self.cancel)
            # Re-read the portal; acknowledgement never proves success or causes a click.
            views = await self.observe()
            while any(view["challenge"] for view in views):
                await self.store.wait_for_human(
                    self.attempt_id, "O desafio ainda está visível. Resolva e confirme novamente", self.cancel)
                views = await self.observe()
        sends = self.store.submissions(self.attempt_id)
        if sends:
            # Once any send was attempted, the model never receives another action.
            for _ in range(20):
                if self.cancel and self.cancel.get("cancel_requested"):
                    raise asyncio.CancelledError()
                views = await self.observe()
                if any(view["challenge"] for view in views):
                    return await self.guard(agent)
                errors = [ERROR.search(view["text"]) for view in views]
                if any(errors):
                    await self.finish("uncertain", "Portal reported a submission/verification error; do not resend")
                    break
                sends = self.store.submissions(self.attempt_id)
                accepted = any(row["http_status"] and 200 <= row["http_status"] < 300 and
                               row["request_key"] not in self.rejected_responses for row in sends)
                if accepted and any(SUCCESS.search(view["text"]) for view in views):
                    await self.finish("confirmed", "Successful submit response AND portal confirmation")
                    break
                await asyncio.sleep(0.5)
            if not self.terminal:
                await self.finish("uncertain", "Send attempted without independent portal confirmation")
        if self.terminal and agent:
            agent.stop()

    async def finalize(self):
        await self.guard()
        if not self.terminal:
            await self.finish("uncertain", "No independently observed submission; model success is insufficient")
        return self.store.get(self.attempt_id)

    async def close(self):
        if self.response_tasks:
            await asyncio.gather(*self.response_tasks, return_exceptions=True)
        for context in self.contexts:
            try:
                context.remove_listener("response", self.on_response)
                await context.unroute("**/*", self.route)
            except Exception:
                pass
        if self.playwright:
            await self.playwright.stop()
