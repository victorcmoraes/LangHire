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

## Ashby: Phase 0 checkpoint, 2026-10-28

The [official Job Postings API](https://developers.ashbyhq.com/docs/public-job-posting-api)
documents `GET https://api.ashbyhq.com/posting-api/job-board/{board}`.
Two small live probes returned HTTP 200, API version 1 and job arrays. Fields
include IDs, descriptions, primary/secondary locations, hosted/application URLs,
`isListed` and `workplaceType`. Use `workplaceType`, since `isRemote` includes
hybrid jobs; missing countries require evidence rather than inference. Keep
unlisted postings out of discovery.

The [customer terms](https://www.ashbyhq.com/resources/terms) prohibit security
circumvention and disruptive use. Their customer scope does not establish a
general third-party ingestion license. The documented
[partner-feed route](https://developers.ashbyhq.com/docs/dedicated-partner-job-feeds)
uses employer opt-in; resolve the appropriate source scope before scaling.

Read-only browser inspection allowed GETs and GraphQL operations explicitly
declared as queries. Mutations remained blocked; no fields were filled or files
uploaded. One hosted form loaded name/email, resume upload, custom questions,
location selection and optional demographic controls. Several required questions
ask for specific past experience and would need confirmed evidence. A reCAPTCHA
Enterprise frame was present; no challenge was solved. Email verification and
post-submission confirmation remain unobserved.

The second application's URL, returned by the API, responded HTTP 404. Earlier
local inspections blocked schema queries too, producing a local instrumentation
failure; that is not evidence of a portal restriction. After correcting the
read-only inspection, the second URL still returned 404.

Phase 0 is incomplete: obtain a second working hosted form, document its upload,
custom questions and verification behavior, and resolve source scope. The API
alone does not satisfy the phase gate. No Ashby collector, adapter, registry entry
or submission support was added. Future integration must default to `platform_hold`.

## Workable: coleta pública somente leitura, 2026-11-03

A [referência oficial da API pública](https://workable.readme.io/reference/jobs-1)
documenta `GET https://www.workable.com/api/accounts/{subdomain}?details=true`.
O [guia de integração de carreiras](https://help.workable.com/hc/en-us/articles/115012771647-Using-the-Workable-API-to-create-a-careers-page)
separa essa rota pública da SPI autenticada. Em 2026-11-03, duas chamadas GET sem
autenticação, separadas por pausa de três segundos, confirmaram:

| Conta | Resultado | Vagas públicas |
| --- | --- | --- |
| `adoreal` | HTTP 200 | 19 |
| `zeptolab` | HTTP 200 | 3 |

Foi usado apenas o endpoint documentado. O widget público
`https://apply.workable.com/api/v1/widget/accounts/{subdomain}` e a listagem
`POST https://apply.workable.com/api/v3/accounts/{subdomain}/jobs` não foram
testados, pois a fonte preferida funcionou. Nenhum bloqueio 401/403/429 ocorreu;
esses códigos interrompem a validação e exigem revisão humana, sem contorno.
A sondagem anterior com o nome do fornecedor retornou 404 em 2026-10-28;
ela não foi repetida nesta verificação.

A integração aceita contas e URLs oficiais, coleta apenas vagas públicas com
`published_on`, limpa HTML e normaliza `shortcode`, empresa e localizações.
`workplace_type` prevalece sobre `telecommuting`; na ausência do primeiro,
`telecommuting=true` indica remoto e `false` mantém a modalidade desconhecida.
As respostas reais continham URLs curtas `/j/{shortcode}`; o catálogo produz
URLs com a conta para manter a identidade `workable:{conta}:{shortcode}`.

Workable permanece fora das filas e execuções de candidatura, com a mesma trava
de Ashby/Cornerstone. Os testes usam API simulada e dados fictícios; nenhuma
candidatura foi enviada e nenhum formulário foi aberto. Os
[termos do fornecedor](https://www.workable.com/terms) continuam como referência
da análise anterior. Inspeções de formulários, verificações de fonte por conta e
confirmação de candidatura seguem pendentes; esta coleta não conclui a Fase 0
de candidatura nem adiciona suporte a envio.

## SmartRecruiters: Phase 0 source restriction, 2026-10-28

The [Posting API reference](https://developers.smartrecruiters.com/reference/v1listpostings)
documents `GET https://api.smartrecruiters.com/v1/companies/{companyIdentifier}/postings`.
However, the [candidate terms](https://www.smartrecruiters.com/legal/terms-of-use/)
restrict automated access to other users' content. Under this project's explicit
stop-on-restriction rule, no company feed requests or form inspections were made.
Use an authorized integration agreement before reconsidering this source. The
documented API's existence alone does not settle permission for this use case.
Phase 0 has not passed; no collector, adapter or registry entry was added.
