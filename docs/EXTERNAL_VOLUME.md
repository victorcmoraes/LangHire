# External queue (development)

The external workflow runs locally by command, independently of the generic
browser agent. It has not been released in the packaged desktop. A successful
local test or completed form is not counted as an application received.

From the checkout, with the existing imported catalog, confirmed facts and PDF:

```powershell
.venv\Scripts\python.exe -m cli.external collect
.venv\Scripts\python.exe -m cli.external status
.venv\Scripts\python.exe -m cli.external run --platform greenhouse --execution review --limit 30
.venv\Scripts\python.exe -m cli.external run --platform greenhouse --execution submit --limit 10
.venv\Scripts\python.exe -m cli.external stop
```

`collect` refreshes the already known employer boards through public read-only
APIs. It does not assume a global employer search. Filtered aggregator results
are partial and cannot establish closure of missing listings. `triage` can be
run separately. Imported job identities and protected attempts are preserved.

Greenhouse preflight resolves required questions from public form schemas before
opening Chrome or uploading a resume. Unsupported mandatory controls and unknown
facts remain pending. Form review then checks the live controls independently.
Current coverage includes ordinary text, React single selects and the configured
PDF; mandatory multi-selects remain a known limitation. A selected file input
may disappear after the hosted uploader accepts the attachment. Uploader setup
must finish before choosing a file. A resume is never attached as a cover letter.

The submit monitor recognizes the observed Greenhouse JSON final operation,
requires an armed click targeting the same employer/job, and requires both an
accepted response and a same-job confirmation. For the observed HTTP 428
`captcha-failed` response with an email-code recipient, a live form can wait for
human code entry. No code is generated or retrieved from a different mailbox.
After explicit acknowledgement, a supported continuation creates a linked new
attempt and consumes another daily slot before one final click. The original
HTTP 428, evidence and charge remain. A lost session or ordinary uncertain
attempt cannot enter this continuation path. Code values are masked in monitor
screenshots and omitted from event payloads.

The first real Greenhouse attempt reached this email-verification precondition
before support was added, so it remains uncertain and protected. The new
continuation is fixture-tested, not yet validated with a real confirmation.
No external application is confirmed at this checkpoint.

Verification diagnosis holds affect only their platform's sends. A pending
candidate-email clarification blocks additional sends without blocking public
collection and preflight. No job is reconstructed or resent merely to reopen a
lost challenge. The shared daily pilot ceiling is ten; the larger pilot and the
200/day release gate remain incomplete.

Per-run reports in ignored `.local/reports/` distinguish preflight, actual
browser attempts, submit intentions, confirmations, unknown answers, unsupported
controls and paused channels. They record direct rules, cache and model calls
separately and do not attribute another worker's attempts to the current run.
