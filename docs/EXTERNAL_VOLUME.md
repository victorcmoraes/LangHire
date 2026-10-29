# External queue (development)

The external workflow runs locally by command, independently of the generic
browser agent. It has not been released in the packaged desktop. A successful
local test or completed form is not counted as an application received.

From the checkout, with the existing imported catalog, confirmed facts and PDF:

```powershell
.venv\Scripts\python.exe -m cli.external collect
.venv\Scripts\python.exe -m cli.external status
.venv\Scripts\python.exe -m cli.external preflight --limit 50
.venv\Scripts\python.exe -m cli.external run --platform greenhouse --execution review --limit 30
.venv\Scripts\python.exe -m cli.external run --platform greenhouse --execution submit --limit 10
.venv\Scripts\python.exe -m cli.external stop
.venv\Scripts\python.exe -m cli.external export --tracker path\to\candidaturas.csv
```

`collect` refreshes the already known employer boards through public read-only
APIs. It does not assume a global employer search. Filtered aggregator results
are partial and cannot establish closure of missing listings. `triage` can be
run separately. Imported job identities and protected attempts are preserved.

Greenhouse preflight resolves required questions from public form schemas before
opening Chrome or uploading a resume. Unsupported mandatory controls and unknown
facts remain pending. Form review then checks the live controls independently.
Remote work does not establish eligibility for every city: an explicit eligible
locations list must cover the candidate's confirmed residence. Otherwise the job
is held even if its form can be filled completely.
Current coverage includes ordinary text, React single selects and the configured
PDF, native checkbox groups, React multiple selects, phone calling codes and
city/state/country autocomplete. Location and demographic schema sections are
included in preflight. Demographic questions use an explicit decline option,
without inferring a personal attribute. Their required processing checkbox is
accepted only when all survey responses decline disclosure.
The native, schema-required checkbox for processing the current application is
handled separately; retention for future vacancies and marketing are not opted in.
A selected file input
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

For current hosted Greenhouse pages, confirmation must be on that job's
`/confirmation` route. Posting text such as “Application submitted (you are
here)” in an employer's interview-process description is not a receipt.

The first real Greenhouse attempt reached this email-verification precondition
before support was added, so it remains uncertain and protected. The new
continuation has also completed real confirmations using the authorized local
Gmail reader. Lost sessions remain protected and are not reconstructed.
The initial two external applications were independently confirmed on Greenhouse,
in two companies, with accepted HTTP responses, saved own-job confirmation routes
and received application emails. Both initially produced false negatives in the
monitor: an unavailable response body was conflated with rejection, and one
receipt used an unrecognized "Thank you for your application" template. The
classifier now distinguishes unavailable bodies from explicit parsed rejection;
it still requires the matching portal receipt. An HTTP 200 alone is insufficient.
Hosted forms have been filled and reviewed in Portuguese and English. The local
run reports record the current confirmation counts and pilot progress.

For already-final uncertain records, `reconcile-receipt` requires retained portal
JSON, its screenshot, a mailbox receipt and a successful submit response. It
retains previous events and charges, creates an audit report and updates the job
projection; it never repeats submission. Example (filenames are relative to that
attempt's evidence directory):

```powershell
.venv\Scripts\python.exe -m cli.external reconcile-receipt --attempt-id ATTEMPT --capture CAPTURE.json --screenshot CAPTURE.png --mail-receipt receipt-email.json
```

Preflight inspects up to three public schemas concurrently without opening Chrome.
Browser applications remain sequential. A fresh ready result prioritizes a job in
the queue; execution always checks its current schema and live form again. The
preflight report distinguishes readiness from actual sends. A changed job identity
requires fresh qualification. A required working schedule needs its own confirmed
fact; technical experience does not establish availability.

Automatic local Qwen3 8b proposals and local Gmail OAuth are now available; setup,
first-answer approval and live-session email continuation are described in
[GMAIL_LOCAL.md](GMAIL_LOCAL.md). The existing chat plugin connection is not a
local OAuth credential. Without local authorization, email verification continues
to require human intervention. CAPTCHA handling remains manual.

Equivalent questions with the same displayed option set and context share a
pending answer even when their ATS option IDs differ. An approval is translated
back to each form's own IDs; review checks both the accepted value and original
approval. Country, company where relevant, language and salary units remain
part of identity. Changed facts, changed options and conflicting approvals block
reuse. Older pending rows are preserved; this is not a destructive history migration.

CSV export appends only confirmed external receipts, preserves existing file
bytes and manual columns, backs up before appending and is idempotent for already
exported job identities. It does not turn an uncertain attempt into a CSV success.

Verification diagnosis holds affect only their platform's sends. A pending
candidate-email clarification blocks additional sends without blocking public
collection and preflight. No job is reconstructed or resent merely to reopen a
lost challenge. New installations default to a shared daily ceiling of ten.

The daily ceiling can be explicitly disabled in the private `settings.json`:

```json
{"submission_policy":{"daily_limit":null}}
```

For unattended runs, `submission_policy.unattended_hold_seconds` (a positive
integer) bounds each human hold. When it expires, the challenge stays unsolved:
an attempt without a send returns to pending, one with a send stays uncertain
and protected, and the queue continues. Without it, holds wait for a human.

Without this setting, the default remains ten. A positive integer sets a shared
daily ceiling; `null` removes it across browser workers, restarts and email-code
continuations. Attempts remain counted and all duplicate/uncertain-send guards
stay active. `status` exposes `daily_limit_enabled` and uses null remaining budget
for unlimited operation. The CLI processes the complete eligible queue unless
`run --limit N` explicitly bounds that invocation. Removing a ceiling does not
claim completion of the qualification benchmark or platform pilot.

The observed hosted confirmation templates include English and Portuguese.
Receipt reconciliation accepts evidence obtained through the connected Gmail
plugin or the locally authorized read-only Gmail API; both still require the
same-job portal receipt, screenshot and accepted response before confirmation.

Per-run reports in ignored `.local/reports/` distinguish preflight, actual
browser attempts, submit intentions, confirmations, unknown answers, unsupported
controls and paused channels. They record direct rules, cache and model calls
separately and do not attribute another worker's attempts to the current run.

## Arrangement and geography evidence

Run `uv run python -m cli.external evidence --limit 50` to locate arrangement and
eligible-location sentences with the configured local Ollama model, then rerun
triage. It opens no browser and sends no applications. The report counts checked
and cached jobs, verified remote/hybrid/onsite and Brazil allowed/excluded evidence,
rejected quotes, failures and resulting triage states.

The model only proposes verbatim quotes. Code checks the current description hash,
quote text and explicit arrangement/geography patterns before using evidence;
benefits and occasional remote work cannot establish a remote arrangement.
Unknown or failed proposals leave unresolved jobs pending. The local cache at
`external/arrangement-evidence.json` is reused only for the same description,
model and prompt version. Explicit location and non-remote mode restrictions
continue to take precedence.
