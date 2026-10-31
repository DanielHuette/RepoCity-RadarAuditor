# Build report

German version: [BUILD_REPORT.de.md](BUILD_REPORT.de.md)

## Skeleton created
**Context:** First state before the build. | **Input:** Conversation of 2026-10-15/16, check catalogue. | **Output:** Folder structure, signatures, schema, catalogue, documentation outline.

| Finding | Change | Evidence |
|---|---|---|
| No project existed | Skeleton following senior-engineering-standard | commit "Skeleton" |

## Python package built
**Context:** `src/radarauditor/`, state of 2026-10-16 after four build waves (core, collectors, scoring; reconciliation, reporting, app; integration, docs; access, paid data, operations). | **Input:** Skeleton signatures and docstrings. | **Output:** Working modules with tests.

| Module | Built | Test files | Tests |
|---|---|---|---|
| `collectors/` | website crawler and parser, PageSpeed Insights, Places API (New), Google, Bing and image search in a browser, directories, AI assistants (OpenAI, Claude, Gemini, Perplexity), DataForSEO (positions, grid, volumes, backlinks, gaps), Google access (OAuth, profile performance, Search Console, Analytics, owner replies), full run | `test_website_parser`, `test_pagespeed`, `test_places`, `test_serp_parser`, `test_directories`, `test_assistants`, `test_paid_rankings`, `test_paid_data`, `test_access`, `test_google_auth`, `test_collect_run` | 180 |
| `scoring/` | catalogue loading and validation, all 108 checks in the 14 areas a to n, area and total scores, rankings and grid points from raw data | `test_catalog`, `test_evaluator`, `test_rankings`, `test_checks_a` to `test_checks_n` | 455 |
| `reporting/` | charts, forecast range, sales and monthly report with profile figures and keyword gaps, German number and date formats | `test_charts`, `test_forecast`, `test_report`, `test_report_repositories`, `test_rendering` | 92 |
| `documents/` | document kinds, requests from the app | `test_documents`, `test_document_requests` | 72 |
| `jobs/` | queue, retries, idle step for documents, hand-off to Claude Code with schema validation | `test_handoff`, `test_worker` | 76 |
| `db/` | table and storage access | `test_repositories` | 48 |
| `integrations/` | events for a customer-management system | `test_events` | 7 |
| `config.py` | settings from `.env` with readable errors | `test_config` | 10 |
| `cli.py` | `health`, `worker run`, `audit run`, `jobs pull`, `jobs push`, `report sales`, `report monthly`, `document build`, `google auth` | `test_cli` | 24 |
| Total | | 41 files | 983 |

## App built
**Context:** Installable web app in `app/` (Preact, Supabase, Chart.js, Progressive Web App). | **Input:** Schema and hand-off conventions. | **Output:** Pages, components, tests.

| Part | Built | Test files | Tests |
|---|---|---|---|
| Pages | sign-in, dashboard, customers, customer (locations, competitors, keywords, access, audits, documents), audit (score, traffic lights, findings with copy, competitors, trend, posts and review replies), documents, settings | | |
| Scoring | area names, scores as in `evaluator.py` | `scoring.test.ts` | 10 |
| Documents | form fields, validation, error line | `documents.test.ts` | 12 |
| Rankings | comparison per keyword, axis labels | `rankings.test.ts` | 9 |
| Grid | grid points | `grid.test.ts` | 6 |
| Texts | reading and validating `texte.json` | `texts.test.ts` | 8 |
| Formats | euro, dates, amounts, addresses, ids | `format.test.ts` | 11 |
| Dashboard | next steps, search | `pipeline.test.ts` | 4 |
| Copy | copy-button content | `clipboard.test.ts` | 6 |
| Errors | readable error messages | `errors.test.ts` | 5 |
| Total | | 10 files | 81 |

## Documentation
**Context:** Material for the operator. | **Input:** Code, screenshots. | **Output:** Manuals with pictures.

| File | Content |
|---|---|
| `BETRIEBSANLEITUNG.de.md` / `.md` | eight sections, 24 pictures (phone and desktop), appendix with customer instructions |
| `SETUP.de.md` / `.md` | Supabase, Cloudflare Pages, Google Cloud, `.env`, supplier data, font, paid services, mail reading, Google access |
| `ARCHITEKTUR.de.md` / `.md` | parts, lifecycle, hand-off files, decisions, limits |
| `scripts/screenshots.py` | produces every picture against mocked data |

## Final checks
**Context:** State of 2026-10-16. | **Input:** Repository. | **Output:** Check results.

| Check | Command | Result |
|---|---|---|
| Python formatting | `uv run ruff format --check .` | passed |
| Python lint | `uv run ruff check .` | All checks passed! |
| Types | `uv run mypy src` | Success: no issues found in 57 source files |
| Python tests | `uv run pytest -q` | 983 passed, 1 warning (WeasyPrint: HarfBuzz-Subset missing on the build machine) |
| App check | `npm run check` | no errors (TypeScript and ESLint) |
| App tests | `npm test` | 81 passed (10 files) |
| App build | `npm run build` | succeeded |
| Screenshots | `uv run python scripts/screenshots.py --font …` | 48 pictures, verified |
| Reports and documents | PDF pages from the tests inspected | sales report 9 pages, quote with all fields |

## Billing moved to the CRM
**Context:** Decided by Daniel on 24 October 2026: the RadarAuditor only audits, evaluates and writes quotes; customer status, invoices, payments, tax adviser package, monthly audit dates and every outgoing mail belong to the CRM. The tables above show the state of 2026-10-16 without the parts removed here. | **Input:** Contract `Dokumentation/INTEGRATION.md` of the RepoCity company repository. | **Output:** Removed billing, new mail reading, new send flow.

| Finding | Change | Evidence |
|---|---|---|
| Invoices, credit notes, own invoice numbers and payments in the RadarAuditor | removed from code, templates, models and database (migration 014; stored invoices stop it instead of being deleted) | `supabase/migrations/014_remove_billing.sql`, `test_migration_database.py` |
| Yearly tax export | `exports/`, `config/export.yaml` and `radarauditor export tax` removed | `test_cli.py` |
| Monthly run and customer phase | `run_monthly`, `radarauditor monthly run`, job kind `monthly`, `customers.stage` removed; the CRM queues the monthly audit, the report step picks the before-after report from `audits.purpose` | `test_worker.py` |
| Mail of customers and unknown senders | `jobs/mail_triage.py`: one Claude Code call per mail without any tool, mail fenced as untrusted data, answer validated against the two result shapes; heartbeat field `mail_reading_available` | `test_mail_triage.py`, `test_heartbeat.py` |
| Review of 25 October 2026 | one mail per idle poll after the documents; reading pauses on a problem until Claude Code was checked again; a used-up subscription is recognised and lets the mails go unfailed; no settings, plugins, hooks or MCP servers load in the no-tools call; long answers are cut; CRM refusals fail the mail; **Angebot abschicken** can be cancelled and gives up after five minutes; 014 removes the empty bucket `exports` | `test_mail_triage.py`, `test_worker.py`, `test_claude_code.py`, `SendQuote.test.tsx`, `test_migration_database.py` |
| Quote sent by hand | **Angebot abschicken**: asks for a missing e-mail address and stores it in the CRM, requests the PDF when missing, then `crm.send_quote` | `SendQuote.test.tsx`, `quoteSending.test.ts` |

| Check | Command | Result |
|---|---|---|
| Python lint and format | `uv run ruff check .`, `uv run ruff format --check .` | passed |
| Types | `uv run mypy src` | no issues in 83 source files |
| Python tests with PostgreSQL | `uv run pytest` | 1804 passed (run of 30 October 2026) |
| App | `npm run check`, `npm test`, `npm run build` | no errors, 365 passed in 43 test files, built |

## Customer search, reply drafts and monthly service
**Context:** Roadmap item 6, built on 31 October 2026. | **Input:** State after migration 015. | **Output:** Migration 016, Cockpit migration 008, modules, app page, check results.

| Finding | Change | Evidence |
|---|---|---|
| No search for businesses without a website address | Tab **Kunden**, `prospects/`, `search_prospects` in `collectors/places.py`, tables `prospect_searches` and `prospect_results`, list kept 24 hours, own source `prospect_search` with daily and euro monthly cap, price per request from Google's price list | `test_prospect_assessment.py`, `test_prospect_search.py`, `test_places.py`, `test_migration_016_database.py`, `Kundensuche.test.tsx` |
| Contact ways without legal check, objection could be bypassed | `config/kundensuche.yaml`, no mail and no dial button, the CRM deal names the lawful way; objection list `advertising_objections` (name and city, phone, `place_id`; a name also as the start of the other, additions in brackets removed, an entry without city holds everywhere), mark in the list, CRM only with Daniel's entries | `KONTAKTWEGE.md`, `test_migration_016_database.py`, integration tests P1 to P17 |
| Places fields only deleted while the server runs | pg_cron schedule `radar-prospect-purge` hourly at minute 53 in migration 016 | `test_migration_016_database.py`, integration test P17 |
| A stored `null` lifted the euro cap of paid sources | the default cap stays for a missing or empty value (server and app) | `test_app_settings.py`, `sources.test.ts` |
| Review replies only as text in the app | PDF of up to five reply drafts to real unanswered reviews, `/antworten` | `test_review_replies.py`, `test_reviews.py`, `ReviewReplies.test.tsx` |
| Monthly audit without prepared work | `queue_monthly_service` only after the initial optimisation is finished, only drafts of the same audit replaced, `monatsservice.json`, Cockpit notice, approval per draft | `test_optimization.py`, `test_crm_repositories.py`, `test_worker.py`, integration tests S11 to S15 |
| Promises of positions not ruled out | Rule in both skills, sentence in the monthly report and the reply PDF | `test_report.py`, `test_review_replies.py` |

| Check | Result |
|---|---|
| `ruff check`, `ruff format --check`, `mypy --strict src` | clean |
| `pytest` (with database tests) | 1913 passed (before 1804) |
| App `npm run check`, `npm test`, `npm run build` | clean, 399 tests passed (before 365) |
| Cockpit app `npm run check`, `npm test`, `npm run build` | clean, 108 tests passed |
| `integration/` `pytest` | 446 passed; T12 depended on the time of day (day change Berlin and UTC) and is now independent of it |

## Open points
**Context:** Not possible on the build machine. | **Input:** Code. | **Output:** List for the first real run.

| Finding | Consequence | Evidence |
|---|---|---|
| Customer search and reply drafts only tested against doubles of Google and Claude Code | watch the first real search and the first reply PDF after migration 016 is applied | `test_prospect_search.py`, `test_review_replies.py` |
| That 24 hours of cache of the Places fields are covered by Google's EEA terms is not settled (no caching permission except identifier and coordinates) | ask Google or lower the period; it is set in one place (`public.prospect_cache_hours()`) | `KONTAKTWEGE.md`, section storage |
| Pictures of the manual do not show tab **Kunden** and the reply PDF yet | `uv run python scripts/screenshots.py` on a machine with Playwright | `scripts/screenshots.py` |
| No run against a real business, Google, DataForSEO or Supabase; every external service is mocked in tests | first real run on the operator's PC after setup | `SETUP.md` |
| Docker image not built (no Docker on the build machine); dependencies resolve in a fresh environment | watch the first `docker compose up -d` | `Dockerfile`, `requirements.txt` |
| Business Profile interfaces usable only after Google's approval | checks m1 and m4 read "not measurable" until then | `SETUP.md` section 9 |
| Wording of contract and service description not reviewed by a lawyer | review once, then only fill in | `templates/documents/` |
| Mail reading and **Angebot abschicken** tested against stand-ins only; they need the CRM migration with `crm.claim_mail_triage`, `crm.classify_mail_triage`, `crm.fail_mail_triage` and `crm.send_quote` | first real mail and first sent quote after the CRM migration | `test_mail_triage.py`, `SendQuote.test.tsx` |
| Supabase may refuse deleting the bucket `exports` by SQL, and 014 keeps it while it holds files | 014 then says so; delete it in the Supabase dashboard | `014_remove_billing.sql` |
