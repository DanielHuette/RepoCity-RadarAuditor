# Architecture

German version: [ARCHITEKTUR.de.md](ARCHITEKTUR.de.md)

## Parts and data flow
**Context:** How app, worker, database and Claude Code interact. | **Input:** Website address and choice of checks. | **Output:** Evaluation, report, quote, documents, classified mails.

```
Phone/tablet/PC ──► App (Cloudflare Pages, installable web app) ──► Supabase (database, files, job queue)
                                                        ▲            │
                                                        │            ▼
                             Claude Code (server, subscription) ◄── jobs pull / push ── Worker container (server)
                                                                                   │
                                     website · PageSpeed · Places · Google search · directories · AI assistants
```

| Part | Folder | Runs where | What is built |
|---|---|---|---|
| App | `app/` | Cloudflare Pages, app.radarauditor.speedofthespirit.dev | sign-in; tab Prüfen with radar: website address only, choice of checks per area, live progress; tab Auswertung: identified business, city and industry with correction, talk text, three biggest levers, every check with measurements and fix (g4 with reviews without an owner reply), report, posts and review replies, rankings, map grid, trend, button to the customer file in the CRM; tab Angebot: positions from the open checks with CRM products and prices, quote in schema `crm`, PDF, decision, optimisation drafts; settings with data sources and install |
| Collectors | `src/radarauditor/collectors/` | Docker container | identification of business, address, phone, industry and search terms from the website (`identify.py`), website crawler, PageSpeed Insights, Google Maps in the browser (profile, newest reviews with owner replies, competitors, position per keyword, 5x5 map grid; Places API (New) as fallback when Maps blocks), Google and Bing search in the browser, directories, AI assistants (ChatGPT, Gemini, Perplexity on their websites in the browser with their paid interfaces as fallback; Claude through Claude Code), paid rankings (DataForSEO, off) |
| Scoring | `src/radarauditor/scoring/` | Docker container | load the catalogue, passed, partial, failed or not measurable per check; area and total scores; rankings from raw data |
| Reporting | `src/radarauditor/reporting/` | Docker container | sales report and monthly report as PDF with charts and forecast range |
| Documents | `src/radarauditor/documents/` | Docker container | audit report with quote, quote, service description, contract, proof of service as PDF; invoices are written by the CRM |
| Jobs | `src/radarauditor/jobs/` | Docker container | queue loop (collect, score, analyse, report, serp_retry, optimize, apply); the analyse step runs Claude Code headless; `jobs pull` / `jobs push` for manual runs; on idle polls `mail_triage.py` reads the mails the CRM queued |
| Events | `src/radarauditor/integrations/` | Docker container | sends events such as "report ready" to a customer-management system when `EVENT_ENDPOINT` is set |
| Database | `supabase/migrations/` | Supabase | tables, access policies, private buckets; `start_field_audit` and `link_crm_customer` (migration 011); audit purpose and `queue_monthly_audit` (013); billing removed (014) |
| Analysis | `.claude/commands/auswerten.md`, `skills/radar-auswerter/` | Claude Code in the worker container on the server (`claude -p`) | findings, fixes, report texts, posts and review replies |
| Screenshots | `scripts/screenshots.py` | PC or container with Playwright | builds the app against demo data and captures the manual's pictures |
| Publishing | `.github/workflows/deploy-app.yml` | GitHub Actions | on a push to the branch `app-veroeffentlichen` only: `npm run check`, `npm test`, `npm run build`, then `wrangler pages deploy` to the Pages project `radarauditor-app`; `ci.yml` checks every push and publishes nothing |

## Audit lifecycle
**Context:** From the button in the app to the report. | **Input:** Website address and choice of checks. | **Output:** Evaluation and report PDF.

| Step | Who | Result |
|---|---|---|
| 1 | App: "Prüfung starten" calls `start_field_audit` (migration 011) with the website and the switched-off checks; the correction in the Auswertung adds name, city and industry | customer found by website host or created with the host as provisional name and empty city and industry; `link_crm_customer` links or creates the CRM customer once name and city are known; audit `requested` with `audits.disabled_checks`, job `collect`; audits with access come from `radarauditor audit run --with-access` and from `public.queue_monthly_audit`, which the CRM calls for the monthly audit of a subscription (purpose `monthly`) |
| 2 | Worker | first `identify.py` fills what is missing (name still the host, empty city or industry, no keywords); per value the first source wins: JSON-LD on the start page, legal notice, footer contact data, Google Places text search; then `link_crm_customer`; no city found fails the audit at once with a German message; then `raw.json` in bucket `raw` with the identified values and their sources, rows in `rankings`, audit `collected`, job `score` |
| 3 | Worker | `check_results` per catalogue check with potential points and uplift, checks in `audits.disabled_checks` as `skipped` without weight, total score, job `analyse`, audit `awaiting_analysis` |
| 4 | Worker with headless Claude Code `/auswerten <folder>` | folder as `jobs pull`, `findings.json` and `texte.json`, upload as `jobs push`: rows in `findings`, texts and `auswertung.json` in bucket `raw`, audit `analysed`, job `report` |
| 5 | Worker | before-after report for an audit with purpose `monthly` that has an earlier reported audit, otherwise the sales report, in bucket `reports`; audit `reported`, event `audit.reported` |
| 6 | App | open the Auswertung and the report, create the quote under Angebot |

A failing job is retried up to three times; then job and audit are `failed` with the error text.

## Division of work, documents and mail reading
**Context:** What runs besides audits. The RadarAuditor only audits, evaluates and writes quotes; customer status, invoices, payments, the tax adviser package, the monthly audit dates and every outgoing mail belong to the CRM (decided on 24 October 2026, contract `Dokumentation/INTEGRATION.md` of the RepoCity company repository). | **Input:** Requests from the app, the command line and the CRM. | **Output:** PDFs, sent quotes, classified mails, events.

| Process | Trigger | Who does it | Result |
|---|---|---|---|
| Document | the Angebot page inserts a `documents` row of kind `audit_quote` with `file_path` null; other kinds come from `radarauditor document build` | the worker renders requested documents in its idle step, when no job is queued | PDF in bucket `documents` |
| Sending a quote | **Angebot abschicken** on the Angebot page | the app stores a missing e-mail address in `crm.customers`, requests the audit-plus-quote PDF when there is none, waits for it and calls `crm.send_quote`; the CRM's mail worker attaches the PDF and sends the mail | quote `sent`, mail in the CRM outbox |
| Monthly audit | the CRM's daily routine marks a subscription due; one tap in the Cockpit calls `crm.start_audit_run`, which calls `public.queue_monthly_audit` | worker | audit with purpose `monthly`; its report is the before-after report |
| Order finished | the last change of an order leaves `draft`, `approved` and `manual` and no `optimize` or `apply` job is open | database trigger (migration 013) | event `optimization.finished`; the CRM completes the order and sends the invoice |
| Mail reading | every idle poll after the requested documents and the events, while the heartbeat says mails can be read and `MAIL_TRIAGE_BATCH` is above zero; after a problem only once Claude Code was checked again, at once after a lost login or a used-up subscription | worker: `crm.claim_mail_triage` takes up to `MAIL_TRIAGE_BATCH` mails for `MAIL_TRIAGE_CLAIM_SECONDS`, then one mail per idle poll gets one `claude -p` without any tool (`--tools "" --permission-mode dontAsk --setting-sources "" --strict-mcp-config --disable-slash-commands`, model `MAIL_TRIAGE_MODEL`, in an empty temporary folder), the mail fenced by a fresh marker and declared untrusted data | the answer, validated against the two result shapes of the contract (long texts cut), goes to `crm.classify_mail_triage`; an unreadable answer, a slow or failed answer and an answer the CRM refuses go with a German reason to `crm.fail_mail_triage`; without a Claude Code login or with a used-up subscription the held mails are let go unfailed; a claim where every mail failed is reported in the heartbeat |
| Checks and sources | the app stores the choice of checks with each audit in `audits.disabled_checks` (migration 011; the only selection that counts, empty runs every check) and sets settings key `sources` (per source `enabled`, `daily_cap`, optional `monthly_cost_cap_eur`; shapes and defaults in `src/radarauditor/db/app_settings.py`) | the worker reads the audit's selection when scoring and `sources` at the start of every job | switched-off checks stored as `skipped` without weight; each external request counted in `source_usage` via `consume_source_quota`; an exhausted cap makes the affected checks `not_measurable` with reason "Tageslimit erreicht" |
| Heartbeat | every poll of the worker | worker | settings key `worker_heartbeat`: time, version, capabilities (Chromium, Claude Code CLI and its login, browser profile, `analysis_available` with the German `analysis_reason` when false, `mail_reading_available` with `mail_reading_reason`); `analysis_available` is true when Claude Code is logged in and answered a short question without tools (`claude -p "Antworte nur mit OK." --tools "" --model haiku`), checked every 10 minutes; while it is false the worker claims no new `collect` jobs and reads no mails, the other kinds go on; `mail_reading_available` is false while mail reading is switched off, Claude Code does not answer or the last mail run could not take mails from the CRM, and the app shows the reason next to the server status |

## Hand-off to Claude Code
**Context:** The worker writes no plain-language text; Claude Code does, through the subscription. | **Input:** Folder `work/analyse/<job-id>/`. | **Output:** Findings and texts in Supabase.

| File | Direction | Content |
|---|---|---|
| `audit.json` | worker → Claude Code | audit, customer, locations, competitors, keywords |
| `raw.json` | worker → Claude Code | all raw data of the audit |
| `check_results.json` | worker → Claude Code | outcome and measurements per check |
| `katalog.json` | worker → Claude Code | catalogue with customer question and benefit |
| `rankings.json` | worker → Claude Code | positions of this and the previous audit |
| `vorheriges_audit.json` | worker → Claude Code | follow-up audits only: the last audit |
| `findings.json` | Claude Code → worker | one finding per failed or partial check with severity 1 to 3, explanation, customer impact, fix and copyable result (`fix_payload`) |
| `texte.json` | Claude Code → worker | introduction, summary, forecast note, subscription reason, offer text, talk text (`talk_text`: about five sentences the operator says to the customer word for word, shown only in the app at the top of the Auswertung), before-after text for follow-ups, post suggestions and review replies |

`jobs push` validates `findings.json` against the model and the audit's checks (no unknown or duplicate checks, severity 1 to 3) and `texte.json` against the text model; a new evaluation without `talk_text` is rejected. Invalid folders stay in `work/analyse/`; valid ones move to `work/erledigt/`.

The worker runs these steps itself for every `analyse` job:

| Step | What happens |
|---|---|
| Prepare | same folder as `jobs pull` |
| Claude Code | `claude -p "/auswerten <folder>" --output-format json --permission-mode dontAsk --tools Read,Glob,Grep,Write --allowedTools Read Glob Grep "Edit(//<folder>/findings.json)" "Edit(//<folder>/texte.json)" --add-dir <folder> --no-session-persistence` in `ANALYSIS_PROJECT_DIR`, stopped after `ANALYSIS_TIMEOUT_SECONDS`; everything except reading and writing the two files is denied |
| No answer | Claude Code is the only author of the analysis, there is no substitute. When it is missing, not logged in, too slow or writes files `jobs push` would reject, the job is re-queued via `jobs.run_after` after 10 min, 30 min, 2 h and then every 6 h; the audit stays `awaiting_analysis`; the job error holds the German reason ("Claude Code ist nicht installiert", "Claude Code ist nicht angemeldet", "Claude Code hat nicht rechtzeitig geantwortet", "Claude Code hat mit einem Fehler geantwortet", "Claude Code hat ungültige Dateien geschrieben") |
| Push | same validation and upload as `jobs push`; `auswertung.json` (`path`: `claude_code` from the worker or `manual` from `jobs push`) goes to bucket `raw` at `<audit_id>/auswertung.json` |

Potential and uplift per check (stored on `check_results` at scoring time):

| Column | Calculation |
|---|---|
| `potential_points` | total score with this check passed minus today's total score, using the evaluator's area weighting; 0 for passed, skipped and not measurable checks and for area m |
| `uplift_low`, `uplift_high` | forecast gain (enquiries after optimisation minus today, from rankings, `config/prognose.yaml` and the industry, as in the sales report) times this check's share of the summed potential of all open checks |

## Limits
**Context:** What the system cannot do today. | **Input:** Build state of 2026-10-16. | **Output:** Honest list for sales and operation.

| Limit | Consequence | Way out |
|---|---|---|
| Google searches from the browser can hit a captcha page | rankings arrive later; the audit is scored without them first | the remaining searches are retried automatically after 15 min, 1 h, 4 h and 12 h (job `serp_retry`), then the audit is re-scored; the app shows "Google-Suche wird nachgeholt"; with DataForSEO on, the blocked searches run through the DataForSEO SERP API at once and the retry is only needed when DataForSEO is off or capped |
| Owner replies to reviews are not in the Places API | when Maps blocks the browser and the Places API answers instead, reply rate and time (g4) read "partial"; the tone of replies to criticism (g5) always needs the operator's eyes | Google Maps in the browser reads the replies; an audit with access reads them from the profile |
| Review dates on Google Maps are relative ("vor 2 Wochen"); the Places API returns at most 5 reviews | reviews per month (g3) and reply time (g4) are accurate to the unit Google shows; from the Places API g3 is an estimate | `review_source` in the result names the source |
| Checks f4, f5, f6, f9, f10, f11 and f13 (profile description, services, products, posts, questions and answers, service areas, attributes) are not available through any API | they read "not measurable" and do not count | the operator checks them in the profile |
| DataForSEO checks (c2, c12, i1, i2, i3, i5, j5) when the monthly cost cap is reached | "not measurable" with "Monatslimit erreicht"; switched off in the app settings they are left out and their weight is spread within the area | raise the cap in the app settings; DataForSEO credentials in the environment |
| The Business Profile interfaces need Google's approval of the Cloud project; in publishing status Testing the authorisation ends after seven days | until approved, profile figures (m1) and owner replies (m4) are "not measurable" with a warning | apply once and publish the consent screen, [SETUP section 9](SETUP.md#9-google-access-for-audits-with-customer-access) |
| Supabase pauses free-tier projects after a week without access | app and worker cannot reach the database | "Restore project" in the dashboard; a running worker keeps the project active |

## Decisions
**Context:** Fixed on 2026-10-16. | **Input:** Conversation. | **Output:** Requirements.

| Decision | Reason |
|---|---|
| No Anthropic API; analysis through Claude Code and the subscription | no usage-based cost |
| Rankings from real browser searches first; DataForSEO where no free route gives the full result | complete audits; spend limited by the monthly cost cap |
| Supabase free tier, Cloudflare Pages | accounts exist, no extra subscriptions |
| The app is published only from the branch `app-veroeffentlichen` | a release happens on purpose; a push to `main` never changes the live app |
| Worker as a container | runs unchanged on the RepoCity server |
| Billing, customer status, monthly audit dates and outgoing mail only in the CRM | one number range and one place for money; the RadarAuditor stays a tool for audits and quotes |
| Mails read by Claude Code without tools | the subscription covers it, and a mail cannot make Claude Code do anything but answer |
| Forecasts only as a range | no legally binding promises |
