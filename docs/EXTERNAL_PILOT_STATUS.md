# Hosted ATS checkpoint

The external pilot remains incomplete. A Chrome `ERR_BLOCKED_BY_CLIENT` page
coincided with the local monitor blocking a second submission request. The old
attempt has no recorded HTTP receipt and stays uncertain and protected. That
evidence does not establish an account restriction or prove that no application
was received. Do not reconstruct or resend that attempt.

The transport monitor now dispatches an intercepted document POST before
capturing navigation-dependent screenshots. Final evidence is still awaited
before committing a confirmed result. A same-job Lever POST redirect with status
307/308 and unchanged body belongs to the original intent; an independent second
send remains blocked. Redirect receipts must stay on the same HTTPS origin and
job path. Success text on another Lever job cannot confirm the current attempt.
Transport diagnostics retain cleaned URLs, status, resource type, redirect
relationship and body hashes, never request bodies or CAPTCHA tokens.

Regression coverage uses a real local HTTP server for native form POSTs through
307/308, 302 and the final confirmation page. A capture deliberately awaits that
navigation to detect request/capture deadlocks. Other tests retain the duplicate
send guard and reject confirmations from another job or origin. The backend
suite passed with 696 tests, two platform-specific skips and one dependency
deprecation warning.

The local deterministic Lever executor also prepared and reviewed a fresh real
form. An initial preparation exceeded its resume-parser wait without making a
send intent. A later preparation reached a human CAPTCHA. After intervention,
the native document POST reached the portal, which returned HTTP 400 and an
explicit application-verification error. Unlike the earlier client-side block,
this attempt has a recorded portal response. It remains uncertain and protected
against repetition. No external submission is confirmed. Further Lever sends
are paused for diagnosis; this is not evidence of an account restriction.

The monitor now continues observing while waiting for human intervention. A
portal verification error or a valid same-job confirmation ends that hold
without requiring a chat/terminal acknowledgement or causing another click.
Text and CAPTCHA visibility are read together, avoiding a stale iframe locator
waiting for a challenge that has already disappeared. Fixtures cover both
automatic outcomes while retaining one send intent.

After this follow-up, all 46 targeted monitor, durable-attempt and hosted-form
tests passed, including platform-pause isolation and the new human-hold cases.

Detailed job identities, attempts, screenshots and the live checkpoint are kept
in ignored `.local/`. The external executor and catalog are still development
work; external operation has not been released in the packaged desktop. The
shared daily pilot ceiling remains ten attempts. The larger pilot and 200/day
release gate remain unchanged.

## Lever: read-only diagnosis, 2026-10-28

Phase 1 used existing attempt events, reviews, portal captures and a saved error
screenshot. No forms were opened and no submissions were executed. The
verification hold remains active. Detailed evidence references and transport
hashes stay in ignored local reports.

The uncertain history contains eight legacy records for four job identities,
with no current evidence directories, and four newer attempts. The newer cases
separate into two local failures without an HTTP receipt and two portal HTTP 400
responses. One 400 came from the aggregator board and one from a direct company
board. The saved screenshot and DOM capture show the same application-verification
error. The direct CI&T and Swile legacy records do not establish a portal result.
Do not treat the aggregator as the sole cause.

Hypotheses, in the plan's investigation order:

1. Hidden or altered required field: unresolved. Both rejected forms passed the
   visible-control review. Historical form HTML identifies hidden location,
   attachment, account, origin, timezone and consent fields, but no safe per-send
   comparison of those fields was recorded. Visible review cannot establish the
   server's hidden requirements.
2. CAPTCHA expiry from delay: weakened. Human-hold entry to POST took about 155
   seconds on the aggregator and seven seconds on the direct board; both failed.
   Hold entry is not token issuance or resolution. Exact token age is unavailable,
   so this does not rule out other verification failures.
3. Browser/session or anti-automation verification: plausible, unconfirmed. The
   same error across board origins and short/long waits supports investigating a
   shared verification problem. No manual same-profile control exists. These
   results do not establish an account restriction or justify bypassing protection.

The Greenhouse comparison has confirmed receipts and uses a JSON/fetch transport;
Lever uses a native document POST and reviews attachment/location state before
forwarding. That difference is not an A/B control. Existing instrumentation stores
cleaned URLs, status and body hashes, but not request headers, hidden-field
snapshots or CAPTCHA timestamps. A header comparison cannot be reconstructed.

Next experiment requires separate approval: a manual application to a fresh Lever
job in the same profile. Only after that control should an explicitly approved
single automated experiment be considered. Preserve uncertain identities and the
platform hold; do not retry them. Phase 2 and Phase 3 have not started.
