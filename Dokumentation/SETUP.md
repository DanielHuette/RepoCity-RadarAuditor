# Service setup

German version: [SETUP.de.md](SETUP.de.md)

Do this once, in this order; then continue with the [operating manual](BETRIEBSANLEITUNG.de.md). Expect about an hour. Click paths match the service dashboards as of October 2026; if a button is named slightly differently, look for the closest word.

What you have at the end:

| Service | Purpose | What you note down |
|---|---|---|
| Supabase | database, sign-in, file storage, job queue | project URL, public key (anon), secret key (service_role) |
| Cloudflare Pages | serves the app at app.radarauditor.speedofthespirit.dev | API token and account ID, stored as GitHub repository secrets |
| Google Cloud | load-time measurement (PageSpeed Insights) and Google profile data (Places) | one key |
| Your PC | worker in a Docker container, Claude Code | the `.env` file |

## 1. Supabase

1. **Create the project.** Open https://supabase.com/dashboard, sign in, click **New project**. Pick your organisation, name it `radarauditor`, let the dashboard **Generate a password** for the database and store it in your password manager. Under **Region** choose **Central EU (Frankfurt)** so the data stays in Germany. Click **Create new project** and wait about two minutes.
2. **Run the four database scripts, in order.** Left sidebar **SQL Editor**, **New query**. Paste the whole content of `supabase/migrations/001_schema.sql`, click **Run** at the bottom right; the result reads "Success. No rows returned". Repeat with `002_functions.sql`, `003_security.sql` and `004_access.sql`, each in a new query. A script runs only once; a second run reports "already exists".
3. **Turn off self sign-up.** **Authentication** → **Sign In / Providers** (older dashboard: **Providers**, section **User Signups**). Switch off **Allow new users to sign up** and save. The app address and the public key are visible to anyone; without this switch anyone could create an account and read all customer data.
4. **Create your operator account.** **Authentication** → **Users** → **Add user** → **Create new user**. Enter e-mail and a strong password, tick **Auto Confirm User**, click **Create user**. You sign in to the app with these.

   Then run `supabase/migrations/005_roles.sql` in the **SQL Editor**. It sets up the roles: administrators may do everything, every other account reads only. Register your account as administrator (adjust the e-mail) and click **Run**:

   ```sql
   insert into public.app_admins (user_id) select id from auth.users where lower(email) = 'you@example.com';
   ```

   The app then needs migrations 006 to 015 (after the CRM migrations in the same project): [SERVER.md section 5](SERVER.md#5-apply-the-database-migrations-006-to-015).
5. **Copy the URL and both keys.** Click **Connect** at the top or open **Project Settings** → **API Keys**. You need:
   - **Project URL**, like `https://abcdefghijkl.supabase.co` (under **Project Settings** → **Data API** if it is not next to the keys).
   - **anon public** from the tab **Legacy API Keys**. It goes into the app.
   - **service_role secret** from the same tab, shown with **Reveal**. It goes only into `.env` on your PC, never into the app or a message.

   If your project only shows the new keys, use the **Publishable key** for anon and the **Secret key** for service_role.
6. **Check the four storage buckets.** Left **Storage**: `raw`, `reports`, `documents` and `exports`, all marked "Private". If one is missing, `003_security.sql` did not run through.

## 2. Cloudflare Pages

The app is built and published by GitHub Actions with Wrangler (`.github/workflows/deploy-app.yml`), not by a Git connection in Cloudflare. The workflow runs only on a push to the branch `app-veroeffentlichen` or when started by hand under **Actions** → **Deploy app** → **Run workflow**; a push to `main` runs the automatic check (`.github/workflows/ci.yml`) and publishes nothing. Before publishing it runs `npm ci`, `npm run check`, `npm test` and `npm run build` in `app/`. The Pages project `radarauditor` belongs to the public site (`.github/workflows/deploy-site.yml`); the app has its own project `radarauditor-app`.

1. **Create the Pages project once.** In the folder `app` run `npx --yes wrangler@3 pages project create radarauditor-app --production-branch main`; on first use Wrangler asks you to sign in to Cloudflare in the browser. The workflow deploys with `--branch main`, so every publication goes to production.
2. **Create an API token.** Open https://dash.cloudflare.com, top right **My Profile** → **API Tokens** → **Create Token** → **Create Custom Token**. Permission **Account** · **Cloudflare Pages** · **Edit**, under **Account Resources** your account, then **Continue to summary** → **Create Token** and copy the token. The **Account ID** is shown on the **Workers & Pages** overview.
3. **Store both as repository secrets.** On GitHub in the repository **Settings** → **Secrets and variables** → **Actions** → **New repository secret**, once for `CLOUDFLARE_API_TOKEN` (the token) and once for `CLOUDFLARE_ACCOUNT_ID` (the account ID).
4. **Check the environment variables.** `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` and `VITE_CRM_ORIGIN` are set under `env` in `.github/workflows/deploy-app.yml`. All three are public by design: they ship in the browser bundle, and row level security protects the data. For another Supabase project replace them there with the project URL and the public key (anon) from step 1.5. Variables in the Cloudflare dashboard have no effect, because the build runs on GitHub. `VITE_CRM_ORIGIN` is the CRM address behind **Kundenakte im CRM öffnen** (`https://crm.speedofthespirit.dev`); when one of the three is missing, the app names it at start.
5. **Publish.** Push the state to be released to the branch, for example `git push origin main:app-veroeffentlichen`. After a few minutes the run under **Actions** → **Deploy app** is green and the app runs at `radarauditor-app.pages.dev`. If it shows "App nicht eingerichtet", one of the two Supabase values was empty at build time.
6. **Attach the custom domain.** In the Pages project `radarauditor-app` **Custom domains** → **Set up a custom domain**, enter `app.radarauditor.speedofthespirit.dev`, **Continue**, then **Activate domain**. If `speedofthespirit.dev` is already on Cloudflare, the DNS record is created for you. After a few minutes it reads "Active".

## 3. Google Cloud

1. **Create a project.** Open https://console.cloud.google.com, project picker at the top → **New project**, name `radarauditor`, **Create**, then select it.
2. **Link a billing account.** The Places API requires one even within the monthly free usage. Left **Billing** → **Link a billing account**.
3. **Enable the two APIs.** **APIs & Services** → **Library**. Search `PageSpeed Insights API`, open it, **Enable**. Back, search `Places API (New)`, open it, **Enable**. Not the old "Places API" without "(New)".
4. **Create and restrict the key.** **APIs & Services** → **Credentials** → **Create credentials** → **API key**, copy the key. Click the key (**Edit API key**), under **API restrictions** choose **Restrict key**, tick **PageSpeed Insights API** and **Places API (New)**, **Save**. Leave **Application restrictions** at **None**, because the address of a home internet line changes.

## 4. The PC: worker and Claude Code

Prerequisites on the PC: **Docker Desktop** (https://www.docker.com/products/docker-desktop), **uv** (https://docs.astral.sh/uv), **Git** and **Claude Code**. Clone the project with `git clone https://github.com/DanielHuette/RepoCity-RadarAuditor.git`; every command below runs in that project folder.

1. **Create `.env`.** Copy `.env.example` in the project folder and name the copy `.env`. It stays on the PC and never enters the repository (`.gitignore` excludes it).

   | Variable | Value |
   |---|---|
   | `SUPABASE_URL` | Supabase project URL, step 1.5 |
   | `SUPABASE_SERVICE_KEY` | Supabase secret key (service_role), step 1.5 |
   | `GOOGLE_API_KEY` | the Google key from step 3.4 |
   | `RADAR_WORK_DIR` | hand-off folder between worker and Claude Code; keep `./work` |
   | `FORECAST_PATH` | curves for the forecast range; keep `config/prognose.yaml` |
   | `TEMPLATES_DIR` | report and document templates; keep `templates` |
   | `CATALOGUE_PATH` | check catalogue; keep `config/pruefkatalog.yaml` |
   | `QUESTIONS_PATH` | questions for the AI assistants; empty means `config/fragen.yaml` |
   | `INDUSTRIES_PATH` | industry groups with labels and older stored values; empty means `config/branchen.yaml` |
   | `EVENT_ENDPOINT` | address of a customer-management system that receives events; leave empty |
   | `DATAFORSEO_LOGIN` | DataForSEO login; DataForSEO is switched on and capped in the app settings |
   | `DATAFORSEO_PASSWORD` | DataForSEO API password |
   | `DATAFORSEO_EUR_PER_USD` | euros per US dollar for the monthly cost cap; `1` errs on the safe side |
   | `OPENAI_API_KEY` | optional: paid fallback when chatgpt.com blocks the server browser; empty means no fallback |
   | `GEMINI_API_KEY` | optional: paid fallback when gemini.google.com blocks the server browser; empty means no fallback |
   | `PERPLEXITY_API_KEY` | optional: paid fallback when perplexity.ai blocks the server browser or asks for a login; empty means no fallback |
   | `OPENAI_MODEL` | model of the ChatGPT fallback; keep the default |
   | `CLAUDE_MODEL` | model Claude Code answers the assistant questions with; keep the default |
   | `GEMINI_MODEL` | model of the Gemini fallback; keep the default |
   | `PERPLEXITY_MODEL` | preset of the Perplexity fallback: `fast`, `low` or `high`; keep the default `fast` |
   | `ASSISTANT_TIMEOUT_SECONDS` | seconds one assistant answer may take on a website or in Claude Code; keep the default `120` |
   | `GOOGLE_OAUTH_CLIENT_ID` | only for audits with customer access: client ID from section 9 |
   | `GOOGLE_OAUTH_CLIENT_SECRET` | only for audits with customer access: client secret from section 9 |

2. **Check the connection.** Open a terminal in the project folder, run `uv sync` once, then `uv run radarauditor health`. `ok` means URL and key are right; `error: missing environment variables: …` names the missing line.
3. **Start the worker.** Start Docker Desktop, run `docker compose up -d` in the project folder. The first run builds the image (a few minutes). `docker compose ps` then lists the service `worker` as "Up". The container restarts by itself after a PC restart once Docker Desktop runs.
4. **Open Claude Code in the project folder.** The command `/auswerten` is available from `.claude/commands/auswerten.md`.

## 5. Your business data for quotes and contracts

The letterhead of quotes, service descriptions, contracts and proofs of service shows your business data; invoices are written by the CRM with its own company data. The data live in table `settings`. In Supabase **SQL Editor** → **New query**, paste, replace the placeholders, **Run**:

```sql
insert into settings (key, value) values ('supplier', '{
  "name": "Your business name",
  "owner": "First Last",
  "street": "Street 1",
  "postal_code": "48143",
  "city": "Münster",
  "email": "info@your-domain.de",
  "phone": "0251 000000",
  "tax_number": "336/0000/0000",
  "vat_id": null,
  "iban": "DE00 0000 0000 0000 0000 00",
  "bic": "XXXXDEXXXXX",
  "bank_name": "Name of your bank",
  "small_business": false,
  "payment_terms_days": 14
}'::jsonb)
on conflict (key) do update set value = excluded.value;
```

| Field | Required | Meaning |
|---|---|---|
| `name`, `street`, `postal_code`, `city`, `email` | yes | as printed at the top of every document |
| `iban`, `bic`, `bank_name` | yes | your bank account |
| `tax_number` or `vat_id` | one of them | tax number or VAT identification number |
| `owner`, `phone` | no | owner and phone |
| `small_business` | no, default `false` | `true` for a small business under § 19 UStG; quotes then carry no VAT |
| `payment_terms_days` | no, default 14 | payment term the contract names, in days |

## 6. The Archivo font

The app ships the Archivo font itself: `app/public/fonts/Archivo-Variable.woff2` with its licence `OFL.txt` (SIL Open Font License 1.1). It is not loaded from Google, because that sends every visitor's address to Google, which is actionable under German law. The PDF reports in the container use DejaVu Sans as long as Archivo is not installed there.

## 7. Optional: paid services and more AI assistants

| Service | Purpose | Where to get the key | `.env` line |
|---|---|---|---|
| DataForSEO | search volume, link data, ranked keywords (checks c2, c12, i1–i3, i5, j5) and the Google searches the browser is blocked from; on by default with a monthly cost cap in the app settings | https://app.dataforseo.com → **API Access** | `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` |
| OpenAI | asks ChatGPT when chatgpt.com blocks the server browser | https://platform.openai.com/api-keys → **Create new secret key** | `OPENAI_API_KEY` |
| Google Gemini | asks Gemini when gemini.google.com blocks the server browser | https://aistudio.google.com → **Get API key** | `GEMINI_API_KEY` |
| Perplexity | asks Perplexity when perplexity.ai blocks the server browser or asks for a login | https://www.perplexity.ai/settings/api → **Generate** | `PERPLEXITY_API_KEY` |

ChatGPT, Gemini and Perplexity are asked on their websites in the server browser first; Claude is always asked through Claude Code. All four services above charge per request. After changing `.env`, `docker compose up -d` restarts the worker with the new values.

## 8. Mail reading

The CRM's mail worker files incoming mail and queues two kinds for the RadarAuditor: mails of unknown senders (is this a possible customer?) and mails of customers with a sent quote (do they accept or decline?). The worker takes several of them at once with `crm.claim_mail_triage` and asks Claude Code about one mail per idle poll, after the requested documents, on the Claude subscription and without any tool, with no settings, plugins, hooks or MCP servers of the server loaded; the paid API is never used. Without a Claude Code login or with a used-up subscription the held mails are let go without being counted as failed, and reading pauses until Claude Code was checked again. The answers go back to the CRM, which files the sender or records the acceptance.

| `.env` line | Default | Meaning |
|---|---|---|
| `MAIL_TRIAGE_BATCH` | 5 | mails taken from the CRM at once, at most 50; one is read per idle poll; 0 switches mail reading off |
| `MAIL_TRIAGE_TIMEOUT_SECONDS` | 120 | seconds one answer may take |
| `MAIL_TRIAGE_CLAIM_SECONDS` | 900 | seconds the taken mails stay reserved, 30 to 3600; at least batch times answer time, which may not exceed 3600 |
| `MAIL_TRIAGE_MODEL` | `sonnet` | Claude Code model that reads the mails |

Mail reading needs the CRM migration with `crm.mail_triage` and Claude Code signed in on the server. Whether it works shows next to the server status in the app ("Mails lesen steht: …" with the reason) and in the heartbeat (`mail_reading_available`, `mail_reading_reason`).

## 9. Google access for audits with customer access

An audit with access reads calls, direction requests and website clicks from the Business Profile, queries with clicks from Search Console, visits and enquiries from Google Analytics, and the owner's replies to reviews (checks m1 to m4, and g4 and g5 from the real replies). It reads them with **your** Google account, which each customer adds as manager of the profile and as user of Search Console and Analytics (customer instructions at the end of the [operating manual](BETRIEBSANLEITUNG.de.md#anhang-anleitung-für-den-kunden)). You authorise RadarAuditor once; the worker then refreshes the access by itself.

1. **Enable the six interfaces.** In the Google Cloud project from section 3: **APIs & Services** → **Library**, search and **Enable** each: `Google Search Console API`, `Google Analytics Data API`, `Business Profile Performance API`, `My Business Account Management API`, `My Business Business Information API`, `Google My Business API`.
2. **Apply for Business Profile access.** Google releases the Business Profile interfaces only after a short application: open https://developers.google.com/my-business/content/prereqs, follow **Request access to the API** and enter the project number (dashboard of the project, **Project info**). Approval takes a few days. Until then the profile figures (m1) and owner replies (m4) read "not measurable" and the audit carries a warning; Search Console and Analytics work already.
3. **Set up the consent screen.** **APIs & Services** → **OAuth consent screen** (newer console: **Google Auth Platform**). **Get started**: app name `RadarAuditor`, your e-mail as support address, **Audience** **External**, contact e-mail, accept, **Create**. Under **Audience** add your own Google address as **Test user**. Under **Data access** → **Add or remove scopes** tick `.../auth/business.manage`, `.../auth/webmasters.readonly` and `.../auth/analytics.readonly`, **Update**, **Save**.
4. **Create the desktop client.** **APIs & Services** → **Credentials** → **Create credentials** → **OAuth client ID**, **Application type** **Desktop app**, name `RadarAuditor PC`, **Create**. Copy **Client ID** and **Client secret** into `.env` as `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`.
5. **Authorise once.** On the PC in the project folder: `uv run radarauditor google auth`. The command prints a long address; open it in the browser, sign in with the Google account the customers add, confirm the warning "Google hasn't verified this app" with **Continue** and allow all three accesses. The tab then reads "RadarAuditor received the Google authorisation" and the terminal `stored Google authorisation for 3 scopes`. The refresh token is stored in Supabase (table `settings`, key `google_oauth`), so the worker container needs nothing else. If the browser runs on another device, use `uv run radarauditor google auth --manual`: after approval the browser shows an error page; copy its whole address into the terminal.
6. **Keep the access alive.** While the consent screen is in the publishing status **Testing**, Google ends the authorisation after seven days. Under **Audience** click **Publish app** (status **In production**); for your own account no review by Google is needed, the warning from step 5 stays. If the audit ever warns "Google authorisation was revoked or expired", run step 5 again.
