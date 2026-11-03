"""Observe the portal independently of an LLM's success claim.

Intercept known ATS submit requests before forwarding them. Never log request
bodies, cookies, query strings or CAPTCHA tokens. Unsupported paths cannot be
promoted to confirmed by model output.
"""
import asyncio
import json
import re
import uuid
import zlib
from urllib.parse import urlsplit, urlunsplit, parse_qs

from .application_safety import ACTIVE, AttemptStore, SafetyError, job_key

SUCCESS = re.compile(
    r"application (?:has been |was )?(?:successfully )?submitted|"
    r"we (?:have |have successfully )?received your application|thank you for applying|"
    r"candidatura (?:foi )?(?:enviada|recebida)(?: com sucesso)?", re.I)
ERROR = re.compile(
    r"error verifying your application|unable to submit|application (?:was )?not submitted|"
    r"there was an error|erro ao (?:enviar|verificar)|não foi possível enviar", re.I)


def clean_url(url):
    parsed = urlsplit(url)
    path = parsed.path
    if path.startswith("/cdn-cgi/challenge-platform/"):
        path = "/cdn-cgi/challenge-platform/[session-path-redacted]"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def is_portal(url):
    host = (urlsplit(url).hostname or "").lower()
    return any(host == suffix or host.endswith("." + suffix)
               for suffix in ("lever.co", "greenhouse.io", "linkedin.com"))


def is_submission(url, method, body=None):
    if method.upper() != "POST" or not is_portal(url):
        return False
    path = urlsplit(url).path.lower()
    if (urlsplit(url).hostname or '').endswith('linkedin.com'):
        return linkedin_request_kind(url, method, body) == 'submit'
    return bool(re.search(r"/(?:apply|applications?|jobapplications|easyapply)$|"
                          r"/boards/[^/]+/jobs/[^/]+$|/jobs/[^/]+/apply$|"
                          r"voyagerjobs.*(?:onsiteapply|jobapplications)|jobs(?:onsiteapply|apply)", path))


def request_json(body):
    try:
        raw=body.encode() if isinstance(body,str) else body
        if not raw:return None
        if raw.startswith(b'\x1f\x8b'):
            raw=zlib.decompressobj(16+zlib.MAX_WBITS).decompress(raw,1_000_000)
        return json.loads(raw) if len(raw)<=1_000_000 else None
    except (ValueError,TypeError,zlib.error):return None


def request_metadata(body):
    payload=request_json(body)
    result={'shape':list(payload)[:12] if isinstance(payload,dict) else type(payload).__name__}
    if isinstance(payload,dict):
        for key in ('componentId','requestId'):
            name=payload.get(key)
            if isinstance(name,str) and name.startswith('com.linkedin.') and len(name)<250:result[key]=name
        entries=payload.get('list')
        if isinstance(entries,list) and entries and isinstance(entries[0],dict):result['entry_keys']=list(entries[0])[:12]
        requests=payload.get('requests')
        if isinstance(requests,list) and requests and isinstance(requests[0],dict):
            result['request_keys']=list(requests[0])[:12]
            result['request_shapes']={k:list(v)[:12] for k,v in requests[0].items() if isinstance(v,dict)}
            name=requests[0].get('payload',{}).get('name')
            if isinstance(name,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]{0,200}',name):result['event_name']=name
    return result


def linkedin_request_kind(url, method, body=None):
    """Explicit final-submit action; draft creation is not a submission receipt."""
    parsed = urlsplit(url)
    if parsed.hostname not in ('linkedin.com','www.linkedin.com'): return 'unsupported'
    if method.upper() in ('GET','HEAD','OPTIONS'): return 'read'
    path = parsed.path.lower().rstrip('/')
    action = parse_qs(parsed.query).get('action',[''])[0].lower()
    payload=request_json(body)
    entries=payload.get('list') if isinstance(payload,dict) and set(payload)=={'list'} else payload
    if isinstance(entries,list) and entries and all(isinstance(e,dict) and isinstance(e.get('eventInfo'),dict) and isinstance(e.get('eventBody'),dict) for e in entries):
        return 'telemetry'
    if isinstance(payload,dict) and set(payload)=={'clientContext','requests'} and isinstance(payload['requests'],list) and payload['requests']:
        if all(isinstance(r,dict) and isinstance(r.get('payload'),dict)
               and isinstance(r['payload'].get('name'),str) and r['payload']['name'].endswith('Event')
               and isinstance(r['payload'].get('isPegasusEncoded'),bool) for r in payload['requests']):return 'telemetry'
    if path=='/ambry':return 'upload'
    if path in ('/sensorcollect','/realtime/realtimefrontendclientconnectivitytracking','/voyager/api/messaging/dash/presencestatuses') or path.startswith('/litms/api/events/'):
        return 'telemetry'
    if path=='/flagship-web/rsc-action/actions/app-config' and payload=={}:return 'read'
    if path=='/flagship-web/rsc-action/actions/component' and isinstance(payload,dict):
        allowed={f'com.linkedin.sdui.generated.jobseeker.dsl.impl.{name}' for name in (
            'aboutTheJob','peopleWhoCanHelp','howYouFitGuide','premiumCompanyInsightsForJobDetails',
            'aboutTheCompanyForJobDetails','jobAlertToggle','jobDetailsCourseRecommendations',
            'manageJobBanner','premiumApplicantInsightsForJobDetails','resumeReview','similarJobs','uploadedDocument')}
        if payload.get('componentId') in allowed:return 'read'
    if path=='/flagship-web/rsc-action/actions/server-request' and isinstance(payload,dict):
        if linkedin_submit_job_id(payload) is not None:return 'submit'
        if payload.get('requestId') in ('com.linkedin.sdui.requests.jobseeker.easyapply.handlePickedFile',
                                        'com.linkedin.sdui.requests.jobseeker.easyapply.getAmbryUploadUrl'):return 'upload'
        if payload.get('requestId')=='com.linkedin.sdui.requests.jobseeker.easyapply.setResumeFormBinding':return 'draft'
        if payload.get('requestId') in ('com.linkedin.sdui.impl.homenav.requests.getThirdPartyTrackingPixels',
                                        'com.linkedin.sdui.infra.register.cooloff.activity'):
            return 'telemetry'
        if (payload.get('requestId')=='com.linkedin.sdui.requests.jobSeeker.jobPostingActivityLog'
                and payload.get('requestedArguments',{}).get('payload',{}).get('activityType')=='JobActivityType_VIEW'):
            return 'telemetry'
    if path=='/flagship-web/rsc-action/actions/navigation' and isinstance(payload,dict):
        args=payload.get('clientArguments',{})
        if (isinstance(args,dict) and args.get('screenId')=='com.linkedin.sdui.flagshipnav.jobs.easyapply.EasyApply'
                and payload.get('isModal') is True
                and isinstance(args.get('payload'),dict)
                and set(args['payload'])<= {'applicantTrackingSystemName','companyName','jobId','trackingId'}):
            return 'read'
    if re.fullmatch(r'/flagship-web/(?:feed|in/[^/]+)',path) and isinstance(payload,dict) and set(payload)<= {'requestedArguments','isPrefetch'}:
        return 'read'
    if path in ('/voyager/api/voyagerjobsdashonsiteapplyapplications',
                '/voyager/api/jobs/onsiteapply', '/voyager/api/jobs/onsiteapplyapplications'):
        return 'submit' if action == 'submit' else 'unsupported'
    if path == '/voyager/api/graphql':
        query = parse_qs(parsed.query).get('queryId',[''])[0]
        return 'read' if method.upper() == 'POST' and query.startswith('voyagerJobsDash') and not re.search('submit|create|update|delete|applyapplication',query,re.I) else 'unsupported'
    if path in ('/voyager/api/mediauploadmetadata','/voyager/api/voyagermediadashuploadmetadata'):
        return 'upload'
    if path in ('/voyager/api/jobs/onsiteapplydrafts','/voyager/api/voyagerjobsdashonsiteapplydrafts'):
        return 'draft'
    return 'unsupported'


def linkedin_submit_job_id(payload):
    """Recognize only the final operation and duplicated job identity observed in the pilot."""
    operation='com.linkedin.sdui.requests.jobseeker.easyapply.submit'
    if not isinstance(payload,dict) or payload.get('requestId')!=operation:return None
    server=payload.get('serverRequest');args=payload.get('requestedArguments')
    if not isinstance(server,dict) or server.get('requestId')!=operation or not isinstance(args,dict):return None
    if args.get('screenId')!='com.linkedin.sdui.flagshipnav.jobs.easyapply.EasyApply':return None
    nested=server.get('requestedArguments',{})
    if not isinstance(nested,dict):return None
    outer=args.get('payload');inner=nested.get('payload')
    allowed={'jobId','questionFieldsList','questionListFieldsList','questionDateFieldsList','followCompany',
             'trackingCode','companyName','canonicalMemberId','isSubmittingBinding','topChoiceInfo'}
    if not isinstance(outer,dict) or not isinstance(inner,dict) or not set(outer)<=allowed or not set(inner)<=allowed:return None
    job=str(outer.get('jobId',''))
    if not re.fullmatch(r'\d+',job) or str(inner.get('jobId',''))!=job:return None
    if not isinstance(payload.get('states'),list) or not isinstance(args.get('states'),list):return None
    for entry in (outer,inner):
        if any(not isinstance(entry.get(key),list) for key in ('questionFieldsList','questionListFieldsList','questionDateFieldsList')):return None
    return job


class SubmissionMonitor:
    def __init__(self, store: AttemptStore, attempt_id, *, daily_limit=10, cancel=None, pre_submit_review=None):
        self.store = store
        self.attempt_id = attempt_id
        self.directory = store.directory / "evidence" / attempt_id
        self.daily_limit = daily_limit
        self.cancel = cancel
        self.pre_submit_review = pre_submit_review
        self.playwright = None
        self.browser = None
        self.contexts = []
        self.requests = {}
        self.response_tasks = set()
        self.terminal = False
        self.capture_number = 0
        self.blocked_request = None
        self.rejected_responses = set()
        self.armed_request_key = None
        self.upload_receipts = []

    def arm_submission(self, url):
        """Count and persist the operator's final submit intent BEFORE the click."""
        if self.armed_request_key is not None:
            raise SafetyError("Submit click already armed")
        key = uuid.uuid4().hex
        self.store.intent(self.attempt_id, key, clean_url(url), self.daily_limit)
        self.armed_request_key = key

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
        if is_submission(request.url, request.method, request.post_data_buffer):
            key = self.armed_request_key if self.armed_request_key and not self.requests else uuid.uuid4().hex
            try:
                payload=request_json(request.post_data_buffer)
                linkedin_job=linkedin_submit_job_id(payload)
                if linkedin_job is not None:
                    expected=job_key(self.store.get(self.attempt_id)['job_url'])
                    if not self.armed_request_key or expected!='linkedin:'+linkedin_job:
                        self.blocked_request=clean_url(request.url)
                        raise SafetyError('LinkedIn final request has no armed click or targets a different job')
                if self.pre_submit_review:
                    review = await self.pre_submit_review()
                    self.store.event(self.attempt_id, "pre_submit_review", review)
                    if not review.get("ready"):
                        self.blocked_request = clean_url(request.url)
                        raise SafetyError("Pre-submit review blocked the request: " + review.get("reason", "unresolved fields"))
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
            telemetry = ((host.endswith("linkedin.com") and path.startswith("/li/track"))
                         or (host=='csp.withgoogle.com' and path.startswith('/csp/'))) or any(host == suffix or host.endswith("." + suffix) for suffix in (
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
                r"autocomplete|locations?|countries|validate|parseresume)(?:/|$)", path)
            if linkedin_request_kind(request.url,request.method,request.post_data_buffer) in ('read','upload','draft','telemetry'):
                auxiliary = True
            if host=='collector-pxdojv695v.protechts.net' and path=='/api/v2/msft':
                auxiliary = True  # Native site verification; no token is generated or altered here.
            if host=='dvp.px-cloud.net' and path=='/v':
                # Observed native verification request. Preserve the site's own request unchanged.
                # Domain documented at https://docs.humansecurity.com/applications/sensor
                auxiliary = True
            # Permit the portal's own browser/session verification. No challenge
            # is solved or token fabricated by this code; native site scripts run.
            if is_portal(request.url) and path.startswith("/cdn-cgi/challenge-platform/"):
                auxiliary = True
            if host.endswith("linkedin.com") and path == "/talentwidgets/apply-with-linkedin":
                auxiliary = True  # Embedded profile-widget initialization, not final application submit.
            if host.endswith(".s3.amazonaws.com") and "multipart/form-data" in request.headers.get("content-type", ""):
                auxiliary = True  # A hosted ATS may use a signed object-storage form for its PDF upload.
            if human_challenge:
                await route.fallback()
                return
            if not auxiliary:
                self.blocked_request = clean_url(request.url)
                self.store.event(self.attempt_id, "unsupported_post_blocked", {"url": self.blocked_request})
                if host in ('www.linkedin.com','linkedin.com'):
                    self.store.event(self.attempt_id,'unsupported_operation_metadata',request_metadata(request.post_data_buffer))
                await route.abort("blockedbyclient")
                return
        await route.fallback()

    def on_response(self, response):
        path=urlsplit(response.url).path.rstrip('/').lower()
        host=urlsplit(response.url).hostname
        if host in ('linkedin.com','www.linkedin.com') and response.request.method=='POST':
            payload=request_json(response.request.post_data_buffer) if path.endswith('/server-request') else None
            kind='binding' if isinstance(payload,dict) and payload.get('requestId')=='com.linkedin.sdui.requests.jobseeker.easyapply.setResumeFormBinding' else 'binary' if path=='/ambry' else None
            if kind:
                receipt={'kind':kind,'status':response.status,'url':clean_url(response.url)}
                self.upload_receipts.append(receipt)
                self.store.event(self.attempt_id,'resume_upload_response',receipt)
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
                            if not await element.is_visible():
                                continue
                            opaque = await element.evaluate("e=>{for(let n=e;n;n=n.parentElement){if(getComputedStyle(n).opacity==='0')return false;}return true;}")
                            if not opaque:
                                continue
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
            await self.finish("uncertain" if self.store.submissions(self.attempt_id) else "pending", "Unrecognized portal POST blocked before network; endpoint requires review")
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
                if any(row['http_status']==422 and row['request_key'] in self.rejected_responses for row in sends):
                    await self.finish('rejected','Portal explicitly rejected the payload with HTTP 422 and validation errors; evidence retained')
                    break
                accepted = any(row["http_status"] and 200 <= row["http_status"] < 300 and
                               row["request_key"] not in self.rejected_responses for row in sends)
                expected=job_key(self.store.get(self.attempt_id)['job_url'])
                confirmations=[view for view in views if SUCCESS.search(view['text'])
                               and (not expected.startswith('linkedin:') or job_key(view['url'])==expected)]
                if accepted and confirmations:
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
