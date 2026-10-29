# Suggest and register public ATS boards

Run `.venv\Scripts\python.exe -m cli.external boards suggest` to inspect candidates
without changing the registry or collecting jobs. The command reads official
Greenhouse, Lever, Ashby and Cornerstone links stored in LinkedIn/Jobgether records and optional
seeds in the data directory's `external/board-seeds.txt`.

Seeds accept one `platform:slug` or official HTTPS board URL per line. Blank lines
and lines beginning with `#` are ignored. For example:

```text
# Replace with boards you want to inspect
greenhouse:example-company
https://jobs.lever.co/example-company
```

Use `boards suggest --seeds PATH` for another seed file. The default limit is 25
boards per run; `--limit` accepts 1 to 100. Suggestions inspect only unregistered
boards. The command uses at most three concurrent public API requests and caches
both responses and failures for 24 hours. It does not follow aggregator links,
retry blocked requests, or interact with application forms. HTTP 401, 403, 429
and unexpected non-JSON pages require human review.

The local `reports/board-suggestions-*.json` report lists public job counts,
title/location matches and new eligible jobs after full qualification. A board
must return at least one title/location match to qualify for registration; a
match alone does not establish eligibility. Qualification reads current facts
and job-track settings even when the public API response comes from cache.

After reviewing the list, explicitly select approved boards:

```powershell
.venv\Scripts\python.exe -m cli.external boards add --board greenhouse:example-company
```

This command validates the selected boards, backs up the existing registry and
adds them atomically under the catalog lock. It does not collect or apply. Run
`discover` separately to collect registered boards. Once a registry exists, it
is authoritative; boards retained only in an older catalog are not re-added.

Ashby collection accepts `ashby:example-company` or official
`https://jobs.ashbyhq.com/example-company` URLs. Collection is read-only through
the documented public Job Postings API at
`https://api.ashbyhq.com/posting-api/job-board/{board}` and excludes unlisted jobs.
Ashby jobs serve as a manual queue; applications are not supported and these jobs
are excluded from automated runs and batches.

Cornerstone collection accepts `cornerstone:example-company` (site 1),
`cornerstone:example-company/2`, or official
`https://example-company.csod.com/ux/ats/careersite/2/home?c=example-company`
URLs. It reads the public page's anonymous token and regional cloud URL, then
pages only the public `rec-job-search/external/jobs` search endpoint. Collection
is read-only; applications are manual and Cornerstone jobs are excluded from
automated queues and runs. Board validation uses the same cache and human review
handling for HTTP 401, 403 and 429 as other public boards.
