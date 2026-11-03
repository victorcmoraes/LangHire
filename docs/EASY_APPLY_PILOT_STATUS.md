# Easy Apply pilot checkpoint — 2026-10-27

## Current supervised milestone

The first real Easy Apply milestone passed: three complete reviews followed by
three confirmed submissions at three employers. Each final request received HTTP
200 and the LinkedIn job page displayed an explicit submitted status. A separate
read-only session reopened all three pages and verified that the statuses
persisted. Each confirmed attempt has one final-send intent; no uncertain send
was repeated. The historical Binance entry is not counted in these three.

The operator supervised Qwen proposals and checked answers, job identity, PDF
hashes, portal screenshots and durable state. Two local model proposals were
reviewed against confirmed facts. The narrative/POC draft remains unapproved;
none of these submissions depended on it. This is supervised validation, not
proof that the model can approve arbitrary narratives autonomously.

The current LinkedIn final-submit shape is covered by an anonymized fixture.
The monitor requires an armed click and matching job IDs in both copies of the
payload. Confirmation text must belong to the same job. One diagnostic final
click was blocked before forwarding; explicit operator reconciliation released
only that proven never-forwarded probe, retaining its daily charge and history.
Ordinary uncertain submissions remain protected.

Additional fixes cover modern radio groups with empty native labels and duplicate
`on` values, DOM replacement after selection, adjacent checkbox labels, company
follow opt-out, visible required-field errors and damaged Unicode answers.
Superseded reviews remain in the audit trail; the app shows only the current
review and hides jobs that are already confirmed or uncertain.

Detailed private receipts and per-run reports are in `.local/`; the consolidated
report is `.local/reports/supervised-pilot.md`. The original ten-confirmation
cross-platform pilot is still incomplete. The ceiling stays at ten attempts per
day; the 200-attempt ceiling remains locked.

## Earlier checkpoint (retained for historical context)

Before the supervised run, the desktop workflow was implemented but real
submissions were unvalidated. The user had deferred the unanswered application
questions. Pending preparations were not counted as reviewed applications.

## Observed execution

- Native Windows desktop started its frozen backend and authenticated against
  the configured local data directory.
- Two bounded searches collected 13 additional listings with an observed Easy
  Apply button. Collection alone does not assert eligibility.
- Four individually qualified jobs were prepared sequentially by the same
  executor, without employer-specific code.
- The final review-only batch took 46.41 seconds. All four preparations stopped
  at unresolved questions and the queue continued to the next job.
- Answer resolution recorded 14 direct rules, two approved-answer reuses,
  11 unresolved answers and zero model calls.
- Zero final submission intentions, confirmations or new uncertain submissions
  were recorded for that batch. Screenshots and attempt histories remain in
  the ignored local evidence directory.

## Correction validated

LinkedIn used the Portuguese button label `Avaliar` before its final review.
The executor previously recognized other review labels and incorrectly reported
that this transition had not completed. The shared review-button matcher now
recognizes this label. All 11 executor tests passed, including the added
Portuguese regression fixture. The rebuilt frozen desktop backend then reached
the unanswered final questions in all four real forms.

## Deferred information and acceptance gates

Unanswered questions include professional durations by technology, current
income, salary questions lacking explicit units, and a specific professional
research/POC example. Existing prefilled numeric values are not treated as
confirmed facts. Answers remain pending until explicitly supplied or approved.

At that earlier checkpoint there were zero complete real reviews and zero new
confirmed submissions. The next gates were three complete reviews followed by three individually
released confirmations across at least two companies. The current LinkedIn
final-submit protocol still required validation before unknown final operations
could be allowed. The daily pilot ceiling remained ten. CAPTCHA recovery on the
current interface remains unvalidated by the successful no-CAPTCHA sends.

For exact attempt IDs and evidence paths, inspect the local batch report
`.local/reports/664a84852b6e48e3aed8b10e42925da3.json`.
