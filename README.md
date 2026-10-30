# RepoCity RadarAuditor

German version: [README.de.md](README.de.md)

Measures how visible a local business is on Google, in directories and in AI assistants and turns the result into a sales report and a quote.

Project page: [radarauditor.speedofthespirit.dev/#projekt](https://radarauditor.speedofthespirit.dev/#projekt)

Status: 108 checks in 14 areas; test stage without customer operation.

RadarAuditor measures how visible a local business is on Google Search, Google Maps, directories and AI assistants, scores it against a fixed check catalogue, writes the findings in plain language, and produces the sales report, the quote and the monthly before-after report. One installable web app for phone, tablet and desktop; one Python worker that collects data; Claude Code does the writing through the Claude subscription.

## Division of work

Decided on 24 October 2026; the full contract is `Dokumentation/INTEGRATION.md` of the RepoCity company repository.

| Part | Owns |
|---|---|
| RadarAuditor | audits, evaluation, quotes in the field (`crm.create_quote`), optimisation of website and Google profile, audit reports, reading incoming mail with Claude Code, answering the Cockpit assistant |
| CRM | customers and their status, sending every mail (also the quote via `crm.send_quote`), orders, subscriptions, invoices, payments, dunning, tax adviser package, monthly audit dates |

The CRM queues the monthly audit of a subscription (`public.queue_monthly_audit`, purpose `monthly`); the RadarAuditor then writes the before-after report. When the last change of an order is done, the database writes the event `optimization.finished` and the CRM completes the order.

## Status

The app is published at app.radarauditor.speedofthespirit.dev. The automatic check runs on every push (`.github/workflows/ci.yml`: `ruff`, `mypy`, `pytest`, app check, app tests and build) and again before every publication. Only a push to the branch `app-veroeffentlichen` publishes the app (`.github/workflows/deploy-app.yml`); a push to `main` publishes nothing. Open points are listed in the [build report](Dokumentation/BUILD_REPORT.md#open-points).

Test stage without customer operation.

## Parts

| Part | Where it runs | Purpose |
|---|---|---|
| `app/` | Cloudflare Pages at app.radarauditor.speedofthespirit.dev, installable web app | tabs Prüfen (website address, choice of checks, progress), Auswertung (identified data, talk text, levers, every check, report), Angebot (positions from the open points, prices from the CRM, PDF, decision, optimisation), settings |
| `site/` | Cloudflare Pages at https://radarauditor.speedofthespirit.dev (published) | public project page, German and English |
| `src/radarauditor/` | Docker container on the RepoCity server | collects data, scores checks, renders reports and documents, reads the mails the CRM queues, answers the Cockpit assistant |
| `.claude/` and `skills/` | Claude Code on the server, Claude subscription login | turns raw audit data into findings, fixes and report texts (`/auswerten`) |
| `supabase/` | Supabase (free tier, Frankfurt) | database, sign-in, file storage, job queue |

## Cockpit assistant

Daniel types or speaks a request in the Cockpit. Beside the jobs, the worker (`src/radarauditor/jobs/assistant.py`) takes one request at a time with `crm.claim_assistant_request` and:

1. for a recording, downloads `crm-assistant/anfragen/<id>.wav` with the service key, sends it to the speech service of the phone assistant (`POST /transcribe?language=de`, mono 16 kHz WAV of at most 60 seconds, answer `{"text": ...}`), deletes the recording whatever happens and saves the text with `crm.save_assistant_transcript`;
2. asks Claude Code on the subscription login without any tool (as the mail reading does) to read the request as exactly one JSON intent: `create_contact`, `create_appointment` (`starts_at` with offset to UTC), `draft_mail`, `revenue` or `unclear`. The prompt names today's date and time in Europe/Berlin, puts the request between two lines with a random marker and says that it is data, not instructions;
3. checks the answer with pydantic (one shape per `action`, no further keys, a time only with offset) and hands it to `crm.apply_assistant_intent`, which checks again, carries it out and reports in the Cockpit. A drafted mail waits for "Senden".

Every other failure ends the request with a German sentence through `crm.fail_assistant_request`, for example "Die Spracherkennung ist gerade nicht erreichbar." or "Claude Code ist nicht angemeldet."; the details go to the worker log, the request text never does. While no request waits, the worker removes at most every ten minutes the recordings that `crm.orphan_recordings` names: older than ten minutes and named by no request, for example when the upload worked but the request was never stored.

| Setting in `.env` | Default | Meaning |
|---|---|---|
| `SPEECH_URL` | empty | address of the speech service; empty answers spoken requests with "Die Spracherkennung ist auf dem Server nicht eingerichtet." |
| `SPEECH_TIMEOUT_SECONDS` | 60 | seconds per transcription, tried up to 3 times |
| `COCKPIT_ASSISTANT_POLL_SECONDS` | 10 | pause while no request waits; 0 switches the assistant off |
| `COCKPIT_ASSISTANT_TIMEOUT_SECONDS` | 120 | seconds Claude Code may take; 3 × `SPEECH_TIMEOUT_SECONDS` plus this must stay below 600, because the CRM hands a request claimed for 10 minutes out again |
| `COCKPIT_ASSISTANT_MODEL` | `sonnet` | Claude Code model |

Reaching the speech service on the same server: the speech service of `telefon/` uses the host network and listens on 127.0.0.1:8090 only, which the worker container cannot reach. Three steps open it to the containers of the server and nothing else:

1. `telefon/.env`: `SPEECH_HOST=0.0.0.0`, then `docker compose up -d speech` in `telefon/`.
2. Firewall: `sudo ufw allow from 172.16.0.0/12 to any port 8090 proto tcp` (the Docker networks; the port stays closed to the outside).
3. `radarauditor/.env`: `SPEECH_URL=http://host.docker.internal:8090`, then `docker compose up -d` here; `docker-compose.yml` maps `host.docker.internal` to the server (`host-gateway`).

Check from the worker: `docker compose exec worker python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:8090/health', timeout=3).read())"`.

## Documentation

| Document | Content |
|---|---|
| [Operating manual](Dokumentation/BETRIEBSANLEITUNG.md) ([Deutsch](Dokumentation/BETRIEBSANLEITUNG.de.md)) | every step from the website address to sending the quote, optimisation and the monthly before-after report, with screenshots |
| [Setup](Dokumentation/SETUP.md) ([Deutsch](Dokumentation/SETUP.de.md)) | Supabase, Cloudflare Pages, Google Cloud, `.env`, business data, mail reading |
| [Server](Dokumentation/SERVER.md) ([Deutsch](Dokumentation/SERVER.de.md)) | worker on a rented Ubuntu server: Docker, `.env`, migrations, one-time logins, updates, backups |
| [Architecture](Dokumentation/ARCHITEKTUR.md) ([Deutsch](Dokumentation/ARCHITEKTUR.de.md)) | parts, data flow, hand-off to Claude Code, limits, decisions |
| [Build report](Dokumentation/BUILD_REPORT.md) ([Deutsch](Dokumentation/BUILD_REPORT.de.md)) | modules, test counts, check results, open points |

## Commands

```
uv sync --all-extras
uv run radarauditor --help
```

| Command | Does |
|---|---|
| `uv run radarauditor health` | checks `.env` and the Supabase connection, prints `ok` |
| `uv run radarauditor worker run` | runs the job loop (the container does this) |
| `uv run radarauditor audit run <customer-id> [--with-access]` | queues an audit without the app |
| `uv run radarauditor jobs pull` | writes waiting analysis jobs to `work/analyse/` for Claude Code |
| `uv run radarauditor jobs push` | uploads `findings.json` and `texte.json` from `work/analyse/` |
| `uv run radarauditor report sales <audit-id>` | renders the sales report PDF |
| `uv run radarauditor report monthly <audit-id>` | renders the before-after report PDF |
| `uv run radarauditor document build <kind> <customer-id> <data.json>` | renders a quote, service description, contract or proof of service |
| `uv run radarauditor google auth [--manual]` | authorises your Google account once for audits with customer access |
| `docker compose up -d` | starts the worker container |
| `uv run python scripts/screenshots.py` | regenerates the manual's screenshots from demo data |

App commands are in [app/README.md](app/README.md).

## First audit in 10 steps

Details and pictures: [operating manual, section 2](Dokumentation/BETRIEBSANLEITUNG.md#2-from-website-address-to-quote).

1. Set up the services once following [SETUP.md](Dokumentation/SETUP.md) and start the worker container on the server ([SERVER.md](Dokumentation/SERVER.md)).
2. Sign in at app.radarauditor.speedofthespirit.dev. The app opens on the tab **Prüfen** with the radar.
3. Enter only the website address and tap **Prüfen**. The server identifies business, city, industry and search terms by itself (`src/radarauditor/collectors/identify.py`; first source wins: JSON-LD on the start page, legal notice (Impressum), footer, Google profile).
4. Choose the checks: 108 checks in 14 areas a to n from `config/pruefkatalog.yaml`. Each area opens on tap and shows one sentence per check; the choice stays on the device and travels with the audit (`audits.disabled_checks`).
5. Tap **Prüfung starten**. The progress shows Wartet, Sammelt, Bewertet, Wertet aus, Fertig; in step "Wertet aus" the worker has Claude Code run `/auswerten` headless.
6. Tap **Auswertung ansehen**. At the top **Erkannt** with business, city and industry; **Korrigieren** starts a new audit with the corrected values and the same checks.
7. Read **Das sagen Sie dem Kunden** to the customer (`talk_text` from `texte.json`) and go through **Die größten Hebel**, the three checks with the most open potential.
8. Open single checks under **Alle Prüfungen**: why it matters, what was measured, the fix; check g4 shows examples of reviews without an owner reply.
9. Tab **Angebot**, choose the audit: the positions come from the audit's open points, the prices from the CRM. **Angebot erstellen**, then **Angebot abschicken** (the CRM mails the quote with the audit report as PDF; a missing e-mail address is asked for and stored in the CRM), then the customer's decision (**Angenommen** creates the order in the CRM); after acceptance **Optimierung starten**.
10. **Kundenakte im CRM öffnen** opens the customer's file in the CRM, where quotes and orders are kept.
