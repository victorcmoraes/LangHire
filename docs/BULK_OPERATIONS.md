# External batches

Run from the checkout with the local virtual environment, configured facts,
resume and browser profile. This workflow is not in the packaged desktop yet.

```powershell
.venv\Scripts\python.exe -m cli.external batch --collect --target 100 --execution review
.venv\Scripts\python.exe -m cli.external batch-status BATCH_ID
.venv\Scripts\python.exe -m cli.external pending --batch-id BATCH_ID
.venv\Scripts\python.exe -m cli.external approve-answer --id ANSWER_ID --value-file .local/answer.json
.venv\Scripts\python.exe -m cli.external batch --resume BATCH_ID
.venv\Scripts\python.exe -m cli.external batch --collect --target 100 --execution submit --tracker path\to\candidaturas.csv
.venv\Scripts\python.exe -m cli.external stop
```

Review is the default. A saved batch cannot change execution mode: create a new
submit batch to submit previously reviewed jobs. Every submission gets a fresh
qualification and live review. Confirmed or uncertain identities stay protected
across batches. The target is a maximum: the manifest records inventory, held
channels and any shortfall instead of adding duplicates or unsuitable jobs.

`discover` adds jobs from `.local/external/board-registry.json`. `collect` is the
legacy refresh of known imported identities. Add official hosted boards with:

```powershell
.venv\Scripts\python.exe -m cli.external discover --board greenhouse:company --board lever:company --aggregator-pages 5
```

`ashby:<board>`, `workable:<account>` and `smartrecruiters:<company>` boards are
collected and triaged but have no executor: their eligible jobs are written to
`.local/external/manual-queue.json` for applying by hand, and sends stay refused.

Filtered aggregator pages cannot prove that missing global postings closed.
See the [Lever postings API](https://github.com/lever/postings-api) for its
company-scoped location filters and pagination.

Batches persist in the existing SQLite database alongside attempts. A single
profile lease protects all browser workers. One persistent browser processes
one application at a time. Public schema preparation can run concurrently;
ready forms do not wait for unrelated model proposals. Inference remains serial.
Unknown questions are grouped by meaning, displayed options and context.
Proposals require approval; missing facts do not become default answers.
Approvals cannot silently supply missing salary currency or period.

Resume reconciles the manifest against actual attempts first. Confirmed and
uncertain identities never become new applications. Per-run reports retain new
and cumulative counts separately, native intentions, dispositions, timing and
answer metrics. Reports and evidence stay under `.local/`; review is never
reported as a received application. CSV export appends confirmed identities only.

Platform verification holds continue to block bulk sends. `diagnose-lever
--job-url URL` permits one explicitly selected new eligible job only when the
hold records a technical verification problem and explicitly excludes a
confirmed account restriction. The hold and evidence are preserved. After a
native intention that job cannot be used for another diagnostic. CAPTCHA remains
human-operated. Old uncertain applications cannot be retried through this command.

The real 2026-10-28 review batch selected 100 jobs, reviewed 46 and left 54
pending in 1,195 seconds, with zero native send intentions. A subsequent single
Lever diagnostic received the portal verification error and remained uncertain.
These results establish review throughput, not submission throughput or pilot
completion. Qualification sample auditing and a successful Lever submission
remain outstanding.
