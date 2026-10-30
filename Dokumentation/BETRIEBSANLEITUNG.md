# Operating manual RepoCity RadarAuditor

German version: [BETRIEBSANLEITUNG.de.md](BETRIEBSANLEITUNG.de.md)

How to work with RadarAuditor, from the website address to the monthly before-after report. Every step: what you do, what you then see, the picture. First the picture from the phone, below it the one from the PC. The pictures show demo data (Dachdeckerei Muster GmbH in Münster); `uv run python scripts/screenshots.py` regenerates them.

Four parts work together:

| Part | Where | What it does |
|---|---|---|
| App | phone, tablet, PC at app.radarauditor.speedofthespirit.dev | check a website, show the evaluation, create the quote, approve the optimisation |
| Worker | Docker container on the RepoCity server | identifies the business from its website, collects data, scores checks, has Claude Code analyse, renders report and quote as PDF, has Claude Code read the mails the CRM queues |
| Claude Code | server, Claude subscription | writes findings, fixes and texts with `/auswerten` and reads incoming mail; the worker calls it by itself |
| CRM | crm.speedofthespirit.dev | customers, sending every mail, orders, subscriptions, invoices, payments, dunning, tax adviser package, dates of the monthly audit |

## 1. One-time setup

1. **Set up the services following [SETUP.md](SETUP.md).**
2. **Sign in.** Open the address, enter the operator e-mail and password, tap **Anmelden**. You land on the tab **Prüfen**.

   There are two roles. The **administrator** may do everything: start audits, correct identified data, create quotes, approve optimisations. Every other account **reads only**: the app shows "Nur Lesen" at the top, forms and action buttons are hidden, and the database refuses any change even when someone bypasses the app. Customer access data (WordPress) is visible to administrators only. Administrators are listed in the table `public.app_admins`.

   ![Phone: sign-in](bilder/anmelden-handy.png)
   ![Desktop: sign-in](bilder/anmelden-desktop.png)

3. **Install the app on phone and tablet.** Tap the **Einstellungen** icon at the top right: on iPhone or iPad tap Share in Safari, then "Zum Home-Bildschirm"; on Android use "App installieren" in the browser menu.

   ![Phone: install hint](bilder/einstellungen-installieren-handy.png)
   ![Desktop: settings](bilder/einstellungen-desktop.png)

4. **Start the worker** on the server as described in [SERVER.md](SERVER.md): `docker compose up -d` in the project folder. It polls for jobs every 15 seconds.
5. **Sign in to Claude Code once** in the project folder. The worker has Claude Code write every analysis; while Claude Code does not answer, it takes no new audit. Run `/auswerten` by hand only when an audit stays at "Wertet aus".

## 2. From website address to quote

1. **Enter the website address** on the tab **Prüfen** and tap **Prüfen**. Name, city, industry and search terms are identified by the server (`src/radarauditor/collectors/identify.py`); per value the first source wins: JSON-LD on the start page, legal notice (Impressum), footer, Google profile.

   ![Phone: tab Prüfen with the website address](bilder/pruefen-handy.png)
   ![Desktop: tab Prüfen](bilder/pruefen-desktop.png)

2. **Choose the checks.** `config/pruefkatalog.yaml` holds 108 checks in 14 areas, a to n. Each area has one box for all its checks and opens on tap, showing one sentence per check. The choice stays on the device and is stored with the audit (`audits.disabled_checks`). Tap **Prüfung starten**. "Prüfungen gerade nicht möglich" means Claude Code on the server cannot analyse right now; the reason follows.

   ![Phone: choice of checks with one area open](bilder/pruefen-auswahl-handy.png)
   ![Desktop: choice of checks](bilder/pruefen-auswahl-desktop.png)

3. **Follow the progress:** Wartet, Sammelt, Bewertet, Wertet aus, Fertig. In "Wertet aus" Claude Code writes findings, fixes and texts. If the server finds no city, the audit stops with "Ort nicht gefunden"; open it under **Auswertung** and enter the city with **Korrigieren**. At **Fertig** tap **Auswertung ansehen**.

   ![Phone: progress of an audit](bilder/pruefen-fortschritt-handy.png)
   ![Desktop: progress of an audit](bilder/pruefen-fortschritt-desktop.png)

4. **Check what was identified.** **Erkannt** shows business, city and industry. **Korrigieren** → **Neu prüfen** starts a new audit of the same website with the corrected values and the same checks.

   ![Phone: evaluation with identified data and talk text](bilder/auswertung-handy.png)
   ![Desktop: evaluation with identified data and talk text](bilder/auswertung-desktop.png)

5. **Talk to the customer.** **Das sagen Sie dem Kunden** holds about five sentences Claude Code wrote for the conversation (`talk_text` in `texte.json`). **Die größten Hebel** lists the three checks with the most open potential; a tap jumps to the check. Below: **Bericht öffnen (PDF)**, open potential, total score with the radar per area, traffic lights (green from 75, yellow from 50). The extra enquiries per month of an open check are an estimate, not a promise.

   ![Phone: biggest levers](bilder/auswertung-hebel-handy.png)
   ![Desktop: biggest levers](bilder/auswertung-hebel-desktop.png)

6. **Go through the checks.** **Alle Prüfungen** sorts by potential, area or urgency. Each check opens on tap: why it matters, what was measured, what it costs, the fix with **Kopieren**. Check g4 lists the newest reviews without an owner reply. Further down: rankings against competitors, map grid, trend.

   ![Phone: check g4 opened with reviews without a reply](bilder/auswertung-pruefung-handy.png)
   ![Desktop: check g4 opened](bilder/auswertung-pruefung-desktop.png)
   ![Phone: rankings](bilder/auswertung-wettbewerber-handy.png)
   ![Desktop: rankings and map grid](bilder/auswertung-wettbewerber-desktop.png)

7. **Create the quote.** Tab **Angebot**, pick the audit under "Letzte Prüfungen". **Positionen** proposes the CRM products whose areas have failed or partial checks, at CRM prices. Deselect or change quantities, tap **Angebot erstellen**. A business not yet linked to a CRM customer gets a note; check it again under **Prüfen**.

   ![Phone: tab Angebot with the latest audits](bilder/angebot-handy.png)
   ![Desktop: tab Angebot](bilder/angebot-desktop.png)

8. **Send the quote.** **Angebot abschicken** shows the customer's e-mail address from the CRM; when the CRM has none, type it in and it is stored there. **Jetzt abschicken**: when the audit report with the quote does not exist as PDF yet, the worker renders it first (keep the page open until then), then the CRM marks the quote as sent and mails it with the PDF attached. **PDF herunterladen** saves the PDF to look at it beforehand.
9. **Enter the decision.** **Angenommen** creates the order in the CRM, **Abgelehnt** closes the quote. A customer who accepts by mail needs no tap: Claude Code reads the reply on the server and the CRM records the acceptance. Quotes and orders are kept in the customer's file in the CRM, opened with **Kundenakte im CRM öffnen**.

## 3. From quote to customer

The customer's status lives in the CRM: an accepted quote becomes an order there, and every monthly item a subscription.


1. **Get access from the customer** with the customer instructions at the end of the German manual.
2. **Enter the access** in `public.customer_access` (Supabase **Table Editor**): `google_business_profile_granted`, `search_console_property`, `google_location_name` (`locations/` plus the Business Profile ID), `analytics_property_id` (digits only), `wordpress_url`, `wordpress_user`, `wordpress_app_password_encrypted`, `granted_at`.
3. **Start the audit with access:** `uv run radarauditor audit run <customer-id> --with-access`; the monthly audit does this by itself once profile access is recorded. With your one-time Google authorisation ([SETUP section 9](SETUP.md#9-google-access-for-audits-with-customer-access)) the worker also reads the impact figures: views, calls, direction requests and website clicks of the last 28 days against the 28 days before, queries with clicks from Search Console, visits and enquiries from Analytics and the replies to reviews. The report shows them under "Was Ihr Google-Profil heute bringt"; the impact area does not count towards the total.

## 4. Initial optimisation

1. **Start the optimisation.** After **Angenommen** the tab **Angebot** shows **Optimierung** with the order number. **Optimierung starten** has Claude Code prepare the changes as drafts (`/optimieren`).
2. **Approve the drafts** under **Zur Freigabe** with **Freigeben** or **Ablehnen**; the worker applies approved ones.
3. **Do the rest by hand:** **Von Hand umsetzen** lists changes with **Kopieren** to paste into WordPress, the Google profile or the site builder. **Protokoll** shows what was applied.
4. **Let the next monthly audit measure the effect,** or check the website again under **Prüfen**. When the last change of the order is done, the CRM completes the order and sends the invoice.

## 5. Monthly audit

1. **Start the monthly audit from the notification.** For every subscription the CRM marks the audit due once a month; the Cockpit shows "Monatsprüfung fällig" with the button **Prüfung auslösen**, which also works on the lock screen. The worker then audits the business again with purpose `monthly`; the audit appears under **Auswertung** → **Letzte Prüfungen**.
2. **Read the evaluation.** Claude Code analyses without you. A monthly audit with an earlier report gets the before-after report; post suggestions and review replies appear under **Beiträge für das Google-Profil** and **Antworten auf Bewertungen**, each with **Kopieren**.

   ![Phone: posts and review replies with copy button](bilder/auswertung-texte-handy.png)
   ![Desktop: posts and review replies](bilder/auswertung-texte-desktop.png)

3. **Check the trend.**

   ![Phone: score trend](bilder/auswertung-verlauf-handy.png)
   ![Desktop: score trend](bilder/auswertung-verlauf-desktop.png)

4. **Send report and proof of service.** Report via **Bericht öffnen (PDF)**; proof of service with `uv run radarauditor document build proof_of_service <customer-id> <data.json>` (keys per document kind at the top of `src/radarauditor/documents/builder.py`).

## 6. Invoices, payments and tax adviser

The RadarAuditor writes no invoices. Invoices with one number range, payments, dunning and the package for the tax adviser are in the CRM.

## 7. Troubleshooting

| Symptom | Cause | Remedy |
|---|---|---|
| Audit stays at „Wartet“ or „Sammelt“ | the worker is not running | on the server, in the project folder run `docker compose ps`; if `worker` is not „Up“: `docker compose up -d` |
| Audit stays at „Wertet aus“ with a note giving the reason | Claude Code did not answer (not installed, not signed in, too slow or invalid files); the worker retries after 10 min, 30 min, 2 h and then every 6 h | sign in to Claude Code; to skip the wait, open Claude Code in the project folder and enter `/auswerten` |
| Audit shows „Fehler“ | a service was unreachable three times, or the server found no city | read the error text in the progress view; check again in the tab **Prüfen**, or use **Korrigieren** in the evaluation when the city is missing |
| „Google-Suche wird nachgeholt“ shows at the audit | Google answered the search with a block page (captcha, "I am not a robot") | nothing to do: the server searches again after 15 min, 1 h, 4 h and 12 h and then rescores the audit. If Google stays blocked, only these rankings are missing; the paid service DataForSEO helps permanently ([SETUP.md](SETUP.md), section 7) |
| App reports „Keine Verbindung zum Server“, `uv run radarauditor health` reports an error | Supabase paused the project on the free plan after a week without access | open https://supabase.com/dashboard, choose the project, **Restore project**; after a few minutes everything works again. While the worker runs it accesses the project regularly and it does not pause |
| App shows „App nicht eingerichtet“ | one of the three variables was missing at build time; the page names it | check `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` and `VITE_CRM_ORIGIN` in `.github/workflows/deploy-app.yml`, [SETUP.md](SETUP.md), section 2 |
| Sign-in: „E-Mail oder Passwort stimmt nicht“ | typo or another account | check in Supabase under **Authentication** → **Users**; a new password can be set there |
| Quote stays at „PDF wird auf dem Server erstellt …“ | the worker is not running, or company data is missing | check the worker as above; create the row `supplier` as in [SETUP.md](SETUP.md), section 5; `docker compose logs worker` shows the reason |
| PDF looks plain, font differs from the app | Archivo is not installed in the container; the report then uses DejaVu Sans | does not affect the content ([SETUP.md](SETUP.md), section 6) |
| PDF shows boxes instead of letters | the worker runs outside Docker on a machine without a suitable font | run the worker in the Docker container; DejaVu Sans is installed there |
| **Angebot abschicken** waits for the PDF | the worker is not running or the PDF failed | check the worker as above; after a failed PDF tap **Jetzt abschicken** again, it requests a new one |
| The server status says „Mails lesen steht“ with a reason | mail reading is switched off (`MAIL_TRIAGE_BATCH=0`), Claude Code is not signed in, the subscription is used up, no mail of the last batch could be read, or the CRM hands out no mails; the server tries again after its next check of Claude Code, at the latest after 10 minutes | sign in to Claude Code on the server; check `MAIL_TRIAGE_BATCH` in `.env`; `docker compose logs worker` shows the CRM's answer |
| `/auswerten` reports an error at `jobs push` | `findings.json` does not match the audit | show Claude Code the message and let it correct the file; the folder stays under `work/analyse/` until it is valid |

## Appendix: instructions for the customer

The customer receives the German text from the appendix of [BETRIEBSANLEITUNG.de.md](BETRIEBSANLEITUNG.de.md#anhang-anleitung-für-den-kunden); replace `<deine Google-Adresse>` with the address of your Google account. In English it reads:

---

**Access for your ongoing care**

So that we can maintain your listings and measure the effect, we need access to your Google Business Profile, to Google Search Console and, if you use it, to Google Analytics. You keep full control and can remove the access at any time. We do not need your password.

**1. Google Business Profile (about 2 minutes)**

1. Sign in with the Google account you use to manage your business profile.
2. Search Google for the name of your business. Your profile appears above the results with edit buttons.
3. Tap the three-dot menu and then **Business Profile settings**.
4. Choose **People and access** and then **Add** (icon with person and plus).
5. Enter `<your Google address>`, choose the role **Manager** and tap **Invite**.

**2. Google Search Console (about 2 minutes, only if you use it)**

1. Open https://search.google.com/search-console and choose your website at the top left.
2. At the bottom left **Settings**, then **Users and permissions**.
3. **Add user**, enter `<your Google address>`, permission **Restricted**, **Add**.

**3. Google Analytics (about 1 minute, only if you use it)**

1. Open https://analytics.google.com and choose your website at the top.
2. At the bottom left **Admin** (gear), then under **Property** **Property access management**.
3. At the top right **+**, **Add users**, enter `<your Google address>`, role **Viewer**, **Add**.
4. Send us the **Property ID** (under **Admin** → **Property details**, digits only).

**4. WordPress (only if your website runs on WordPress)**

1. Sign in to WordPress, on the left **Users** → **Add New User**.
2. Username `radarauditor`, email `<your Google address>`, role **Editor**, **Add New User**.

You will not receive any further questions from us; we will get in touch once everything is set up.

---
