# Server: den Sammler auf einem gemieteten Rechner betreiben

Englische Fassung: [SERVER.md](SERVER.md)

Jede Prüfung, Auswertung, jedes PDF (Portable Document Format) und jede Optimierung läuft im Sammler-Container auf einem gemieteten Server; die App zeigt nur die Ergebnisse. Diese Anleitung bringt den Sammler auf einem frischen Server mit Ubuntu 24.04 zum Laufen. Supabase und die App richtest du vorher nach [SETUP.de.md](SETUP.de.md) ein, Abschnitte 1 bis 3. Rechne mit etwa einer Stunde; die meiste Zeit wartest du darauf, dass das Container-Abbild zum ersten Mal gebaut wird.

## 1. Größe und Standort des Servers

| Ausstattung | Mindestens | Empfohlen | Warum |
|---|---|---|---|
| Prozessorkerne (virtuelle Kerne) | 2 | 4 | Chromium stellt Google, Google Maps und die Seiten der KI-Assistenten dar; Claude Code und die PDF-Erzeugung laufen nebenher |
| Arbeitsspeicher | 4 GB | 8 GB | Chromium mit mehreren Seiten braucht 1–2 GB, Claude Code etwa 0,5 GB, der Sammler etwa 0,5 GB |
| Festplatte (SSD, Solid State Drive) | 30 GB | 40 GB oder mehr | Abbild 3,4 GB, beim Aktualisieren noch einmal 3–4 GB Zwischenspeicher, Browser-Profil, Protokolle |
| Standort | Deutschland | Deutschland (zum Beispiel Falkenstein, Nürnberg oder Frankfurt) | Google-Suche und Google Maps antworten je nach Standort des Servers; mit deutscher Adresse siehst du dieselben Ergebnisse wie deutsche Kunden |

Jeder Anbieter mit Ubuntu 24.04 geht, zum Beispiel Hetzner Cloud CX32 oder CPX31. Getestet ist die übliche Intel/AMD-Bauart (x86-64), nicht die Arm-Server.

## 2. Server vorbereiten

1. **Miete den Server** mit dem Abbild **Ubuntu 24.04** und hinterlege bei der Bestellung deinen öffentlichen SSH-Schlüssel (SSH, Secure Shell: die verschlüsselte Fernverbindung zum Server). Notier dir die IPv4-Adresse (Internet Protocol Version 4), unten `SERVER_IP` genannt.
2. **Melde dich an und bring den Server auf den neuesten Stand.** Auf deinem PC im Terminal:

   ```
   ssh root@SERVER_IP
   apt-get update && apt-get -y upgrade
   ```
3. **Leg einen Arbeitsbenutzer an**, damit nichts als Administrator (root) läuft, und gib ihm deinen SSH-Schlüssel:

   ```
   adduser --disabled-password --gecos "" radar
   usermod -aG sudo radar
   echo "radar ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/radar
   mkdir -p /home/radar/.ssh && cp ~/.ssh/authorized_keys /home/radar/.ssh/
   chown -R radar:radar /home/radar/.ssh && chmod 700 /home/radar/.ssh
   ```
4. **Schalt die Firewall ein.** Von außen ist nur die SSH-Verbindung erreichbar. Die Login-Ansicht aus Schritt 7 hängt nur an der internen Adresse des Servers und braucht keinen offenen Zugang.

   ```
   ufw allow OpenSSH
   ufw --force enable
   ```
5. Melde dich ab (`exit`) und als `ssh radar@SERVER_IP` wieder an. Alle weiteren Befehle laufen als `radar`.

## 3. Docker installieren

Über die Paketquelle von Docker selbst, wie in Dockers offizieller Anleitung für Ubuntu:

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

Melde dich ab und wieder an, damit die neue Gruppe gilt. Dann prüfen: `docker run --rm hello-world` schreibt „Hello from Docker!“. `postgresql-client` bringt das Datenbank-Programm `psql` für Schritt 5 mit.

## 4. Code holen und `.env` ausfüllen

```
git clone https://github.com/DanielHuette/RepoCity-RadarAuditor.git ~/radarauditor
cd ~/radarauditor
cp .env.example .env
chmod 600 .env
nano .env
```

Solange der Zweig mit der Außendienst-App noch nicht in `main` übernommen ist, klonst du mit `--branch mobil`. Ist das Repository privat, fragt Git nach Benutzername und Passwort: Benutzername ist dein GitHub-Name, als Passwort nimmst du einen Zugangsschlüssel (GitHub → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens**, nur dieses Repository, Recht **Contents: Read-only**).

Jede Zeile der `.env`:

| Variable | Was hineingehört | Woher |
|---|---|---|
| `SUPABASE_URL` | `https://<ref>.supabase.co` | Supabase → **Project Settings** → **Data API** → Project URL |
| `SUPABASE_SERVICE_KEY` | geheimer Schlüssel | Supabase → **Project Settings** → **API Keys** → **service_role** (oder **Secret key**) |
| `GOOGLE_API_KEY` | Schlüssel für PageSpeed Insights und Places (New) | Google Cloud → **APIs & Services** → **Credentials** ([SETUP.de.md](SETUP.de.md) Abschnitt 3) |
| `RADAR_WORK_DIR` | so lassen | `docker-compose.yml` setzt ihn auf den Speicherbereich `work` |
| `WORKER_ALIVE_FILE` | leer | leer heißt `worker-alive` im Arbeitsordner; die Gesundheitsprüfung des Containers liest sie |
| `BROWSER_PROFILE_DIR` | so lassen | `docker-compose.yml` setzt ihn auf den Speicherbereich `browser-profile` |
| `FORECAST_PATH` | `config/prognose.yaml` | steckt im Abbild |
| `TEMPLATES_DIR` | `templates` | steckt im Abbild |
| `CATALOGUE_PATH` | `config/pruefkatalog.yaml` | steckt im Abbild |
| `QUESTIONS_PATH` | leer | leer heißt `config/fragen.yaml` |
| `PACKAGES_PATH` | leer | leer heißt `config/pakete.yaml` |
| `INDUSTRIES_PATH` | leer | leer heißt `config/branchen.yaml` |
| `EVENT_ENDPOINT` | leer, oder die Empfangsadresse deiner Kundenverwaltung (CRM, Customer Relationship Management) | deine Kundenverwaltung |
| `DATAFORSEO_LOGIN` | Anmelde-E-Mail | https://app.dataforseo.com → **API Access** |
| `DATAFORSEO_PASSWORD` | Passwort für die Schnittstelle (nicht das Konto-Passwort) | dieselbe Seite |
| `DATAFORSEO_EUR_PER_USD` | `1` | so lassen; zählt jeden Dollar als Euro gegen die Monatsgrenze |
| `OPENAI_API_KEY` | freiwillig | https://platform.openai.com/api-keys; nur genutzt, wenn chatgpt.com den Server-Browser sperrt |
| `GEMINI_API_KEY` | freiwillig | https://aistudio.google.com → **Get API key**; nur genutzt, wenn gemini.google.com sperrt |
| `PERPLEXITY_API_KEY` | freiwillig | https://www.perplexity.ai/settings/api; nur genutzt, wenn perplexity.ai sperrt |
| `OPENAI_MODEL` | Vorgabe lassen | Modell für den ChatGPT-Ersatzweg |
| `GEMINI_MODEL` | Vorgabe lassen | Modell für den Gemini-Ersatzweg |
| `PERPLEXITY_MODEL` | `fast` | Voreinstellung für den Perplexity-Ersatzweg |
| `CLAUDE_MODEL` | Vorgabe lassen | Modell, mit dem Claude Code die Assistenten-Fragen beantwortet |
| `ASSISTANT_TIMEOUT_SECONDS` | `120` | Sekunden pro Assistenten-Antwort |
| `GOOGLE_OAUTH_CLIENT_ID` | nur für Prüfungen mit Kundenzugang | [SETUP.de.md](SETUP.de.md) Abschnitt 9 |
| `GOOGLE_OAUTH_CLIENT_SECRET` | nur für Prüfungen mit Kundenzugang | [SETUP.de.md](SETUP.de.md) Abschnitt 9 |
| `ANALYSIS_CLAUDE_COMMAND` | `claude` | das Claude-Code-Programm im Abbild |
| `ANALYSIS_TIMEOUT_SECONDS` | `900` | Sekunden pro Auswertung, danach wird sie neu eingereiht |
| `ANALYSIS_PROJECT_DIR` | so lassen | `docker-compose.yml` setzt ihn auf `/opt/radarauditor`, wo Skills und Befehle liegen |
| `ANALYSIS_PERMISSION_MODE` | `dontAsk` | so lassen |
| `ANALYSIS_MODEL` | leer | leer nimmt das Standardmodell der Claude-Code-Anmeldung |
| `MAIL_TRIAGE_BATCH` | `5` | Mails, die auf einmal aus dem CRM geholt werden (höchstens 50), je Leerlauf wird eine gelesen; `0` schaltet das Mail-Lesen ab |
| `MAIL_TRIAGE_TIMEOUT_SECONDS` | `120` | Sekunden je Mail |
| `MAIL_TRIAGE_CLAIM_SECONDS` | `900` | Sekunden, die genommene Mails reserviert bleiben, 30 bis 3600; mindestens Anzahl mal Sekunden je Mail, und das darf 3600 nicht übersteigen |
| `MAIL_TRIAGE_MODEL` | `sonnet` | Modell, mit dem Claude Code die Mails liest |

Die vier Pfade mit „setzt `docker-compose.yml`“ werden im Container überschrieben; eine vom PC kopierte `.env` passt also unverändert.

## 5. Datenbank-Änderungen 006 bis 015 einspielen

Die Außendienst-App braucht die Dateien `006_field_app.sql`, `007_serp_retry.sql`, `009_source_cost.sql`, `010_manual_changes.sql`, `011_url_only_audit.sql`, `012_field_audit_parts.sql`, `013_crm_handover.sql` (Zweck eines Audits, Monatsprüfung vom CRM angestoßen, Ereignis `optimization.finished`), `014_remove_billing.sql` (Rechnungen, Zahlungen, Monatslauf und Kundenphase wandern ins CRM; sie bricht mit einer Meldung ab, solange noch Rechnungen gespeichert sind) und `015_link_crm_leads.sql` (die Prüfung vor Ort verknüpft einen Interessenten, den das CRM schon aus Kontaktformular, Mail oder Anruf kennt, statt ihn ein zweites Mal anzulegen) (eine 008 gibt es nicht). Sie setzen 001–005 und die Datenbank-Dateien der Kundenverwaltung (Bereich `crm`) im selben Projekt voraus; fehlt `crm`, bricht 006 mit einer verständlichen Meldung ab. Jede Anweisung ist abgesichert, ein zweiter Lauf ändert nichts.

1. **Läuft der Sammler schon, halt ihn zuerst an:** `docker compose stop worker` in `~/radarauditor`. Ein alter Sammler würde sonst noch nach der entfernten Auftragsart `monthly` fragen und entfernte Spalten lesen, während 013 und 014 sie ändern.
2. In Supabase oben auf **Connect**, Reiter **Connection string**, Verfahren **Session pooler** (funktioniert mit IPv4). Die Adresse kopieren und `[YOUR-PASSWORD]` durch das Datenbank-Passwort aus der Projektanlage ersetzen (vergessen: neu setzen unter **Project Settings** → **Database**).
3. Auf dem Server in `~/radarauditor` die Adresse eingeben, ohne dass sie im Befehlsverlauf landet, dann die Dateien der Reihe nach ausführen:

   ```
   read -rs -p "Connection string: " DB_URL; echo
   for f in 006_field_app 007_serp_retry 009_source_cost 010_manual_changes 011_url_only_audit 012_field_audit_parts 013_crm_handover 014_remove_billing 015_link_crm_leads; do
     psql "$DB_URL" -v ON_ERROR_STOP=1 -f "supabase/migrations/$f.sql" || break
   done
   unset DB_URL
   ```

   Jede Datei schreibt Zeilen wie `ALTER TABLE`, `CREATE FUNCTION`, `DO`. Ein `ERROR:` hält die Schleife an; die Meldung nennt den Grund. 014 löscht außerdem die Ablage `exports` des alten Steuerexports, wenn sie leer ist; sonst sagt sie das, und die Ablage bleibt fürs Dashboard.
4. **Bau den Sammler neu und start ihn** mit dem Code, der zur neuen Datenbank passt: `docker compose build && docker compose up -d`.

`supabase db push` nehmen wir nicht: Es erwartet jede frühere Änderung in seiner eigenen Liste, und 001–005 hast du im SQL Editor ausgeführt.

## 6. Sammler starten

```
docker compose up -d --build
```

Der erste Bau dauert 5–15 Minuten (Chromium, Claude Code, Python-Pakete). Danach:

```
docker compose ps
docker compose logs -f worker
```

`ps` zeigt `radarauditor-worker-1` als `Up … (health: starting)`, nach dem ersten gespeicherten Lebenszeichen `(healthy)`. Im Protokoll steht `Worker started, polling every 15 seconds` und, bis Schritt 7 erledigt ist, `No new audits while Claude Code is unavailable: Claude Code ist nicht angemeldet`. Mit Strg+C verlässt du das Protokoll; der Sammler läuft weiter und startet nach einem Neustart des Servers von selbst (`restart: always`).

Die Gesundheitsprüfung des Containers schlägt fehl, wenn der Sammler drei Minuten lang kein Lebenszeichen gespeichert hat (`radarauditor worker alive` liest die Datei, die der Sammler nach jedem Lebenszeichen auffrischt). Docker zeigt dann `(unhealthy)`; das Protokoll nennt den Grund, meist ein nicht erreichbares Supabase oder ein falscher Schlüssel.

## 7. Einmalige Anmeldungen

Beide Anmeldungen liegen in Docker-Speicherbereichen und überstehen Neustarts und Aktualisierungen.

### 7.1 Claude Code (Auswertung, Optimierung, Claude als Assistent)

```
docker compose exec worker claude auth login
```

Der Befehl zeigt eine Anmeldeadresse. Öffne sie auf deinem PC, melde dich mit dem Claude-Konto mit Abo an, bestätige, kopiere den angezeigten Code und füge ihn im Server-Terminal ein. Prüfen:

```
docker compose exec worker claude auth status
```

Dort steht `"loggedIn": true`. Spätestens nach zehn Minuten bemerkt der Sammler die Anmeldung (sofort nach `docker compose restart worker`) und holt Prüfungen ab.

Ohne Anmeldung im Server-Terminal geht es so: Auf deinem PC `claude setup-token` ausführen (dort ist Claude Code installiert), den ausgegebenen Schlüssel als Zeile `CLAUDE_CODE_OAUTH_TOKEN=<Schlüssel>` in die `.env` schreiben und `docker compose up -d` ausführen. Der Schlüssel gilt ein Jahr.

### 7.2 Anmeldungen im Browser (ChatGPT, Gemini mit deinem Google-Konto, Perplexity)

Der Sammler liest diese Seiten in einem dauerhaften Chromium-Profil. Du meldest dich einmal in einer sichtbaren Ausgabe genau dieses Browsers an. Das Bild kommt über noVNC (Bildschirmübertragung im Browser) durch einen SSH-Tunnel auf deinen PC; von außen ist es nicht erreichbar.

1. **Auf deinem PC** ein zweites Terminal öffnen, den Tunnel starten und offen lassen:

   ```
   ssh -L 6080:127.0.0.1:6080 radar@SERVER_IP
   ```
2. **Auf dem Server** (in diesem Tunnel-Terminal oder im ersten) den Sammler anhalten, weil beide dasselbe Profil nutzen, und den Anmelde-Browser starten:

   ```
   cd ~/radarauditor
   docker compose stop worker
   docker compose --profile login run --rm --service-ports login
   ```

   Es erscheint `Browser ready: open http://localhost:6080/vnc.html through the SSH tunnel.`
3. **Auf deinem PC** http://localhost:6080/vnc.html öffnen und **Connect** klicken. Ein Chromium-Fenster mit drei Reitern erscheint:
   - **chatgpt.com**: mit deinem OpenAI-Konto anmelden.
   - **gemini.google.com**: mit deinem Google-Konto anmelden. Google fragt vielleicht auf deinem Handy nach, weil der Server ein neues Gerät ist; bestätige das.
   - **perplexity.ai**: mit deinem Perplexity-Konto anmelden.

   Cookie-Hinweise beantwortest du, wie du willst; die Wahl bleibt im Profil gespeichert.
4. **Schließ das Chromium-Fenster** (das X oben rechts). Im Server-Terminal steht `Browser closed, profile saved in /data/browser-profile.`, der Anmelde-Container beendet sich.
5. **Starte den Sammler wieder** und schließ den Tunnel:

   ```
   docker compose start worker
   ```

Schritt 7.2 wiederholst du nur, wenn eine Prüfung später meldet, dass ein Assistent eine Anmeldung verlangt.

## 8. Lebenszeichen in der App prüfen

Öffne die App auf dem Handy, Reiter **Prüfen**. Oben steht **Server online**. Steht dort **Server offline** mit der Zeit des letzten Lebenszeichens, schreibt der Sammler keins: schau in `docker compose logs worker`. Steht dort „Die Auswertung auf dem Server ist nicht bereit.“, ist Claude Code nicht angemeldet oder antwortet nicht: Schritt 7.1 wiederholen.

Die Rohwerte siehst du in Supabase im **SQL Editor**:

```sql
select value from settings where key = 'worker_heartbeat';
```

`at` ist höchstens eine Minute alt; unter `capabilities` stehen `chromium`, `claude_cli`, `claude_logged_in`, `browser_profile` und `analysis_available` auf `true`, sobald Schritt 6 und 7 erledigt sind.

## 9. Aktualisieren

```
cd ~/radarauditor
git pull
docker compose build --pull --no-cache
docker compose up -d
docker image prune -f
```

`--no-cache` holt auch das neueste Claude Code; Anmeldungen und Browser-Profil bleiben in ihren Speicherbereichen. Bringt die Aktualisierung eine neue Datei in `supabase/migrations/`, führ sie vor `docker compose up -d` wie in Schritt 5 aus. Für eine feste Claude-Code-Version baust du mit `--build-arg CLAUDE_CODE_VERSION=<Version>`.

## 10. Sicherungen

| Speicherbereich | Inhalt | Sichern |
|---|---|---|
| `radarauditor_browser-profile` | Cookies und Anmeldungen von ChatGPT, Google, Perplexity | ja, erspart Schritt 7.2 |
| `radarauditor_claude-login` | Anmeldung von Claude Code | ja, erspart Schritt 7.1 |
| `radarauditor_work` | Übergabeordner laufender Aufträge, nur vorübergehend | nein |

Alle Geschäftsdaten liegen in Supabase (dort mit eigener täglicher Sicherung), nicht auf dem Server. Sichere bei angehaltenem Sammler, damit das Browser-Profil vollständig ist:

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

Die Archive enthalten deine Anmeldungen: hol sie mit `scp radar@SERVER_IP:backups/*.tar.gz .` auf deinen PC und bewahre sie so sicher auf wie ein Passwort. Zurückspielen auf einem neuen Server nach Schritt 4 und vor Schritt 6:

```
docker compose up --no-start
for v in browser-profile claude-login; do
  docker run --rm -v radarauditor_$v:/volume -v ~/backups:/backup debian:bookworm-slim \
    sh -c "tar xzf /backup/$v-<Datum>.tar.gz -C /volume && chown -R 1000:1000 /volume"
done
docker compose up -d
```

## 11. Wenn etwas nicht geht

| Was du siehst | Grund und Abhilfe |
|---|---|
| `docker compose ps` zeigt `(unhealthy)` | drei Minuten kein Lebenszeichen; `docker compose logs worker` nennt den Grund (Supabase-Adresse oder -Schlüssel, Netz) |
| Protokoll: `missing environment variables: …` | die genannte Zeile in der `.env` ist leer; ausfüllen und `docker compose up -d` |
| App: „Die Auswertung auf dem Server ist nicht bereit.“ | Claude Code nicht angemeldet oder das Abo-Kontingent aufgebraucht; `docker compose exec worker claude auth status`, dann Schritt 7.1 |
| Prüfung zeigt „Google-Suche wird nachgeholt“ | Google hat den Server-Browser gesperrt; die Suchen laufen mit wachsenden Abständen erneut, oder kommen von DataForSEO, wenn das eingeschaltet ist |
| Anmelde-Container: `error: no Playwright Chromium` | der Bau des Abbilds wurde unterbrochen; `docker compose build --no-cache` |
| http://localhost:6080 lädt nicht | der SSH-Tunnel aus Schritt 7.2 ist nicht offen, oder der Anmelde-Container läuft nicht |
