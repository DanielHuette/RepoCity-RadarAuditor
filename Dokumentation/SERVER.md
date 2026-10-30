# Server: run the worker on a rented machine

German version: [SERVER.de.md](SERVER.de.md)

Every audit, analysis, PDF and optimisation runs in the worker container on a rented server; the app only shows results. This guide brings the worker up on a fresh Ubuntu 24.04 server. Supabase and the app are set up beforehand as in [SETUP.md](SETUP.md), sections 1 to 3. Expect about an hour, most of it waiting for the first image build.

## 1. Server size and location

| Resource | Minimum | Recommended | Why |
|---|---|---|---|
| CPU | 2 vCPU | 4 vCPU | Chromium renders Google, Maps and the assistant sites; Claude Code and PDF rendering run alongside |
| RAM | 4 GB | 8 GB | Chromium with several pages takes 1–2 GB, Claude Code about 0.5 GB, the worker about 0.5 GB |
| Disk | 30 GB SSD | 40 GB SSD or more | image 3.4 GB, build cache during updates another 3–4 GB, browser profile, logs |
| Location | Germany | Germany (for example Falkenstein, Nuremberg or Frankfurt) | Google search and Maps answer by the server's location; a German address gives the results German customers see |

Any provider offering Ubuntu 24.04 works, for example a Hetzner Cloud CX32 or CPX31. Plain x86-64 (amd64) is tested; Arm servers are not.

## 2. Prepare the server

1. **Rent the server** with image **Ubuntu 24.04** and add your SSH public key during ordering. Note the IPv4 address, below `SERVER_IP`.
2. **Log in and update** from your PC:

   ```
   ssh root@SERVER_IP
   apt-get update && apt-get -y upgrade
   ```
3. **Create a working user** so nothing runs as root, and copy your SSH key to it:

   ```
   adduser --disabled-password --gecos "" radar
   usermod -aG sudo radar
   echo "radar ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/radar
   mkdir -p /home/radar/.ssh && cp ~/.ssh/authorized_keys /home/radar/.ssh/
   chown -R radar:radar /home/radar/.ssh && chmod 700 /home/radar/.ssh
   ```
4. **Switch on the firewall.** Only SSH is reachable from outside; the login view of step 7 is bound to the server's loopback address and needs no open port.

   ```
   ufw allow OpenSSH
   ufw --force enable
   ```
5. Log out (`exit`) and log in again as `ssh radar@SERVER_IP`. All further commands run as `radar`.

## 3. Install Docker

Docker's own package source, as in the official instructions for Ubuntu:

```
sudo apt-get update
sudo apt-get install -y ca-certificates curl git postgresql-client
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker radar
```

Log out and in again so the group applies, then check: `docker run --rm hello-world` prints "Hello from Docker!". `postgresql-client` provides `psql` for step 5.

## 4. Get the code and fill `.env`

```
git clone https://github.com/DanielHuette/RepoCity-RadarAuditor.git ~/radarauditor
cd ~/radarauditor
cp .env.example .env
chmod 600 .env
nano .env
```

Until the field-app branch is merged into `main`, clone with `--branch mobil`. If the repository is private, Git asks for user name and password: enter your GitHub user name and, as password, a fine-grained token (GitHub → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens**, only this repository, permission **Contents: Read-only**).

Every line of `.env`:

| Variable | Value | Where to get it |
|---|---|---|
| `SUPABASE_URL` | `https://<ref>.supabase.co` | Supabase → **Project Settings** → **Data API** → Project URL |
| `SUPABASE_SERVICE_KEY` | secret key | Supabase → **Project Settings** → **API Keys** → **service_role** (or **Secret key**) |
| `GOOGLE_API_KEY` | key with PageSpeed Insights API and Places API (New) | Google Cloud → **APIs & Services** → **Credentials** ([SETUP.md](SETUP.md) section 3) |
| `RADAR_WORK_DIR` | leave as is | set by `docker-compose.yml` to the volume `work` |
| `WORKER_ALIVE_FILE` | empty | empty means `worker-alive` in the work directory; the health check reads it |
| `BROWSER_PROFILE_DIR` | leave as is | set by `docker-compose.yml` to the volume `browser-profile` |
| `FORECAST_PATH` | `config/prognose.yaml` | part of the image |
| `TEMPLATES_DIR` | `templates` | part of the image |
| `CATALOGUE_PATH` | `config/pruefkatalog.yaml` | part of the image |
| `QUESTIONS_PATH` | empty | empty means `config/fragen.yaml` |
| `PACKAGES_PATH` | empty | empty means `config/pakete.yaml` |
| `INDUSTRIES_PATH` | empty | empty means `config/branchen.yaml` |
| `EVENT_ENDPOINT` | empty, or the webhook address of the customer-management system | your CRM |
| `DATAFORSEO_LOGIN` | login e-mail | https://app.dataforseo.com → **API Access** |
| `DATAFORSEO_PASSWORD` | API password (not the account password) | same page |
| `DATAFORSEO_EUR_PER_USD` | `1` | keep; counts every dollar as a euro against the monthly cost cap |
| `OPENAI_API_KEY` | optional | https://platform.openai.com/api-keys; used only when chatgpt.com blocks the server browser |
| `GEMINI_API_KEY` | optional | https://aistudio.google.com → **Get API key**; used only when gemini.google.com blocks |
| `PERPLEXITY_API_KEY` | optional | https://www.perplexity.ai/settings/api; used only when perplexity.ai blocks |
| `OPENAI_MODEL` | keep the default | model of the ChatGPT fallback |
| `GEMINI_MODEL` | keep the default | model of the Gemini fallback |
| `PERPLEXITY_MODEL` | `fast` | preset of the Perplexity fallback |
| `CLAUDE_MODEL` | keep the default | model Claude Code answers the assistant questions with |
| `ASSISTANT_TIMEOUT_SECONDS` | `120` | seconds per assistant answer |
| `GOOGLE_OAUTH_CLIENT_ID` | only for audits with customer access | [SETUP.md](SETUP.md) section 9 |
| `GOOGLE_OAUTH_CLIENT_SECRET` | only for audits with customer access | [SETUP.md](SETUP.md) section 9 |
| `ANALYSIS_CLAUDE_COMMAND` | `claude` | the Claude Code CLI in the image |
| `ANALYSIS_TIMEOUT_SECONDS` | `900` | seconds per analysis before it is re-queued |
| `ANALYSIS_PROJECT_DIR` | leave as is | set by `docker-compose.yml` to `/opt/radarauditor`, where skills and commands lie |
| `ANALYSIS_PERMISSION_MODE` | `dontAsk` | keep |
| `ANALYSIS_MODEL` | empty | empty uses the default model of the Claude Code login |
| `MAIL_TRIAGE_BATCH` | `5` | mails taken from the CRM at once (at most 50), read one per idle poll; `0` switches mail reading off |
| `MAIL_TRIAGE_TIMEOUT_SECONDS` | `120` | seconds per mail |
| `MAIL_TRIAGE_CLAIM_SECONDS` | `900` | seconds the taken mails stay reserved, 30 to 3600; at least batch times seconds per mail, which may not exceed 3600 |
| `MAIL_TRIAGE_MODEL` | `sonnet` | model Claude Code reads the mails with |

The four paths marked "set by `docker-compose.yml`" are overridden inside the container, so a `.env` copied from the PC works unchanged.

## 5. Apply the database migrations 006 to 015

The field app needs migrations `006_field_app.sql`, `007_serp_retry.sql`, `009_source_cost.sql`, `010_manual_changes.sql`, `011_url_only_audit.sql`, `012_field_audit_parts.sql`, `013_crm_handover.sql` (purpose of an audit, monthly audit queued by the CRM, event `optimization.finished`), `014_remove_billing.sql` (invoices, payments, monthly run and customer phase move to the CRM; it stops with a message while invoices are still stored) and `015_link_crm_leads.sql` (the field audit links a lead the CRM already has from the web form, a mail or a call instead of creating it again) (there is no 008). They require 001–005 and the CRM migrations (schema `crm`) in the same project; 006 stops with a clear message if `crm` is missing. Every statement is guarded, so a second run changes nothing.

1. **When the worker already runs, stop it first:** `docker compose stop worker` in `~/radarauditor`. An old worker would otherwise still ask for the removed job kind `monthly` and read the removed columns while 013 and 014 change them.
2. In Supabase click **Connect** at the top, tab **Connection string**, method **Session pooler** (works over IPv4). Copy the address and replace `[YOUR-PASSWORD]` with the database password from project creation (reset under **Project Settings** → **Database** if lost).
3. On the server, in `~/radarauditor`, enter the address without it landing in the shell history, then run the files in order:

   ```
   read -rs -p "Connection string: " DB_URL; echo
   for f in 006_field_app 007_serp_retry 009_source_cost 010_manual_changes 011_url_only_audit 012_field_audit_parts 013_crm_handover 014_remove_billing 015_link_crm_leads; do
     psql "$DB_URL" -v ON_ERROR_STOP=1 -f "supabase/migrations/$f.sql" || break
   done
   unset DB_URL
   ```

   Each file prints lines such as `ALTER TABLE`, `CREATE FUNCTION`, `DO`. An `ERROR:` stops the loop; the message names the cause. 014 also removes the storage bucket `exports` of the old tax export when it is empty; otherwise it says so and the bucket stays for the dashboard.
4. **Rebuild and start the worker** with the code that matches the new database: `docker compose build && docker compose up -d`.

`supabase db push` is not used: it expects every earlier migration in its own history table, and 001–005 were run in the SQL editor.

## 6. Start the worker

```
docker compose up -d --build
```

The first build takes 5–15 minutes (Chromium, Claude Code, Python packages). Then:

```
docker compose ps
docker compose logs -f worker
```

`ps` lists `radarauditor-worker-1` as `Up … (health: starting)`, after the first stored heartbeat `(healthy)`. The log shows `Worker started, polling every 15 seconds` and, until step 7, `No new audits while Claude Code is unavailable: Claude Code ist nicht angemeldet`. Leave the log with Ctrl+C; the worker keeps running and starts again by itself after a reboot (`restart: always`).

The image's health check fails when the worker stored no heartbeat for three minutes (`radarauditor worker alive` reads the file the worker touches after each heartbeat). Docker then shows `(unhealthy)`; the log names the cause, usually an unreachable Supabase or a wrong key.

## 7. One-time logins

Both logins are stored in Docker volumes and survive restarts and updates.

### 7.1 Claude Code (analysis, optimisation, Claude as assistant)

```
docker compose exec worker claude auth login
```

The command prints a sign-in address. Open it on your PC, sign in with the Claude account that has the subscription, confirm, copy the code shown and paste it into the server terminal. Check:

```
docker compose exec worker claude auth status
```

It reports `"loggedIn": true`. Within ten minutes the worker notices the login (or immediately after `docker compose restart worker`) and starts claiming audits.

Alternative without an interactive login on the server: run `claude setup-token` on your PC (Claude Code installed there), add the token it prints as a line `CLAUDE_CODE_OAUTH_TOKEN=<token>` to `.env` and run `docker compose up -d`. The token is valid for a year.

### 7.2 Browser logins (ChatGPT, Gemini with your Google account, Perplexity)

The worker reads these sites in a persistent Chromium profile. You log in once in a visible copy of that browser, shown in your PC's browser through an SSH tunnel.

1. **On your PC**, open a second terminal and start the tunnel; leave it open:

   ```
   ssh -L 6080:127.0.0.1:6080 radar@SERVER_IP
   ```
2. **On the server** (in that tunnel terminal or the first one), stop the worker, because both use the same profile, and start the login browser:

   ```
   cd ~/radarauditor
   docker compose stop worker
   docker compose --profile login run --rm --service-ports login
   ```

   It prints `Browser ready: open http://localhost:6080/vnc.html through the SSH tunnel.`
3. **On your PC**, open http://localhost:6080/vnc.html and click **Connect**. A Chromium window with three tabs appears:
   - **chatgpt.com**: sign in with your OpenAI account.
   - **gemini.google.com**: sign in with your Google account. Google may ask for a confirmation on your phone because the server is a new device; confirm it.
   - **perplexity.ai**: sign in with your Perplexity account.

   Answer cookie banners as you like; the choice is stored in the profile.
4. **Close the Chromium window** (the X at the top right). The server terminal prints `Browser closed, profile saved in /data/browser-profile.` and the login container ends.
5. **Start the worker again** and close the tunnel:

   ```
   docker compose start worker
   ```

Repeat step 7.2 only when an assistant later reports a login wall in an audit.

## 8. Check the heartbeat in the app

Open the app on your phone, tab **Prüfen**. At the top it reads **Server online**. If it reads **Server offline** with the time of the last sign of life, the worker is not writing its heartbeat: see `docker compose logs worker`. If it reads "Die Auswertung auf dem Server ist nicht bereit.", Claude Code is not logged in or not answering: repeat step 7.1.

The raw values are in Supabase **SQL Editor**:

```sql
select value from settings where key = 'worker_heartbeat';
```

`at` is at most a minute old; under `capabilities`, `chromium`, `claude_cli`, `claude_logged_in`, `browser_profile` and `analysis_available` all read `true` once steps 6 and 7 are done.

## 9. Update

```
cd ~/radarauditor
git pull
docker compose build --pull --no-cache
docker compose up -d
docker image prune -f
```

`--no-cache` also fetches the newest Claude Code; logins and the browser profile stay in their volumes. If the update brings a new file in `supabase/migrations/`, run it as in step 5 before `docker compose up -d`. To pin a Claude Code version, build with `--build-arg CLAUDE_CODE_VERSION=<version>`.

## 10. Backups

| Volume | Content | Back up |
|---|---|---|
| `radarauditor_browser-profile` | cookies and logins of ChatGPT, Google, Perplexity | yes, saves repeating step 7.2 |
| `radarauditor_claude-login` | Claude Code login | yes, saves repeating step 7.1 |
| `radarauditor_work` | temporary hand-off folders of running jobs | no |

All business data lives in Supabase (its own daily backups) and is not on the server. Back up with the worker stopped, so the browser profile is consistent:

```
mkdir -p ~/backups
docker compose stop worker
for v in browser-profile claude-login; do
  docker run --rm -v radarauditor_$v:/volume:ro -v ~/backups:/backup debian:bookworm-slim \
    tar czf /backup/$v-$(date +%F).tar.gz -C /volume .
done
docker compose start worker
chmod 600 ~/backups/*.tar.gz
```

The archives contain your logins: copy them to your PC with `scp radar@SERVER_IP:backups/*.tar.gz .` and keep them as safe as a password. Restore on a new server after step 4 and before step 6:

```
docker compose up --no-start
for v in browser-profile claude-login; do
  docker run --rm -v radarauditor_$v:/volume -v ~/backups:/backup debian:bookworm-slim \
    sh -c "tar xzf /backup/$v-<date>.tar.gz -C /volume && chown -R 1000:1000 /volume"
done
docker compose up -d
```

## 11. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `docker compose ps` shows `(unhealthy)` | no heartbeat for three minutes; `docker compose logs worker` names the cause (Supabase address or key, network) |
| Log: `missing environment variables: …` | the named line in `.env` is empty; fill it and run `docker compose up -d` |
| App: "Die Auswertung auf dem Server ist nicht bereit." | Claude Code not logged in or the subscription limit reached; `docker compose exec worker claude auth status`, then step 7.1 |
| Audit shows "Google-Suche wird nachgeholt" | Google blocked the server browser; the searches are repeated with backoff, or come from DataForSEO when it is switched on |
| Login container: `error: no Playwright Chromium` | the image build was interrupted; `docker compose build --no-cache` |
| http://localhost:6080 does not load | the SSH tunnel of step 7.2 is not open, or the login container is not running |
