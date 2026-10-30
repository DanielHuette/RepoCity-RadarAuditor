# Einrichtung der Dienste

Englische Fassung: [SETUP.md](SETUP.md)

Diese Anleitung richtest du einmal ein, in dieser Reihenfolge. Danach geht es in der [Betriebsanleitung](BETRIEBSANLEITUNG.de.md) weiter. Rechne mit etwa einer Stunde. Die Klickwege entsprechen den Oberflächen der Dienste im Oktober 2026; heißt ein Knopf inzwischen leicht anders, such nach dem nächstliegenden Wort.

Was du am Ende hast:

| Dienst | Wofür | Was du dir notierst |
|---|---|---|
| Supabase | Datenbank, Anmeldung, Dateiablage, Auftragswarteschlange | Projekt-Adresse, öffentlicher Schlüssel (anon), geheimer Schlüssel (service_role) |
| Cloudflare Pages | stellt die App unter app.radarauditor.speedofthespirit.dev bereit | API-Schlüssel und Account ID, hinterlegt als Repository-Geheimnisse auf GitHub |
| Google Cloud | Ladezeit-Messung (PageSpeed Insights) und Google-Profil-Daten (Places) | ein Schlüssel |
| Dein PC | Sammler im Docker-Container, Claude Code | die Datei `.env` |

## 1. Supabase

1. **Leg das Projekt an.** Öffne https://supabase.com/dashboard, melde dich an, klicke **New project**. Wähle deine Organisation, gib als Namen `radarauditor` ein, lass dir ein Datenbank-Passwort erzeugen (**Generate a password**) und speichere es in deinem Passwort-Manager. Bei **Region** wählst du **Central EU (Frankfurt)**, damit die Daten in Deutschland liegen. Klicke **Create new project** und warte, bis das Projekt fertig ist (etwa zwei Minuten).
2. **Führ die vier Datenbank-Skripte aus, in dieser Reihenfolge.** Links in der Seitenleiste **SQL Editor** öffnen, **New query** klicken. Den gesamten Inhalt von `supabase/migrations/001_schema.sql` aus dem Projektordner hineinkopieren, unten rechts **Run** klicken. Unten erscheint „Success. No rows returned“. Dasselbe mit `002_functions.sql`, `003_security.sql` und `004_access.sql`, jeweils in einer neuen Abfrage. Ein Skript läuft nur einmal; ein zweiter Lauf meldet „already exists“.
3. **Schalt die Selbst-Registrierung ab.** Links **Authentication**, dann **Sign In / Providers** (ältere Oberfläche: **Providers**, Abschnitt **User Signups**). Den Schalter **Allow new users to sign up** ausschalten und speichern. Wichtig: Die App-Adresse und der öffentliche Schlüssel sind für jeden sichtbar; ohne diesen Schalter könnte sich jeder ein Konto anlegen und alle Kundendaten lesen.
4. **Leg dein Betreiber-Konto an.** **Authentication** → **Users** → **Add user** → **Create new user**. E-Mail-Adresse und ein starkes Passwort eingeben, das Häkchen **Auto Confirm User** setzen, **Create user** klicken. Mit diesen Daten meldest du dich später in der App an.

   Danach im **SQL Editor** `supabase/migrations/005_roles.sql` ausführen. Das Skript legt die Rollen an: Administratoren dürfen alles, jedes andere Konto darf nur lesen. Trag dein Konto als Administrator ein (E-Mail anpassen) und klick **Run**:

   ```sql
   insert into public.app_admins (user_id) select id from auth.users where lower(email) = 'du@example.de';
   ```

   Danach braucht die App die Datenbank-Änderungen 006 bis 015 (nach denen der Kundenverwaltung im selben Projekt): [SERVER.de.md Abschnitt 5](SERVER.de.md#5-datenbank-änderungen-006-bis-015-einspielen).
5. **Kopier die Adresse und die zwei Schlüssel.** Oben auf **Connect** klicken oder links **Project Settings** → **API Keys**. Du brauchst:
   - **Project URL**, sieht aus wie `https://abcdefghijkl.supabase.co` (unter **Project Settings** → **Data API**, falls nicht bei den Schlüsseln).
   - **anon public** (öffentlicher Schlüssel) aus dem Reiter **Legacy API Keys**. Der kommt in die App.
   - **service_role secret** (geheimer Schlüssel) aus demselben Reiter, über **Reveal** sichtbar. Der kommt nur in die `.env` auf deinem PC, nie in die App, nie in eine Nachricht.

   Zeigt dein Projekt nur die neuen Schlüssel, nimm **Publishable key** statt anon und **Secret key** statt service_role.
6. **Prüf die vier Ablagen.** Links **Storage**. Dort stehen `raw`, `reports`, `documents` und `exports`, alle mit dem Hinweis „Private“. Fehlt eine, ist Schritt 2 mit `003_security.sql` nicht durchgelaufen.

## 2. Cloudflare Pages

Die App baut und veröffentlicht GitHub Actions mit Wrangler (`.github/workflows/deploy-app.yml`), nicht eine Git-Verbindung in Cloudflare. Der Ablauf startet nur bei einem Hochladen auf den Branch `app-veroeffentlichen` oder von Hand unter **Actions** → **Deploy app** → **Run workflow**; ein Hochladen auf `main` startet die automatische Prüfung (`.github/workflows/ci.yml`) und veröffentlicht nichts. Vor dem Veröffentlichen laufen in `app/` `npm ci`, `npm run check`, `npm test` und `npm run build`. Das Pages-Projekt `radarauditor` gehört zur öffentlichen Projektseite (`.github/workflows/deploy-site.yml`); die App hat ihr eigenes Projekt `radarauditor-app`.

1. **Leg das Pages-Projekt einmal an.** Im Ordner `app` `npx --yes wrangler@3 pages project create radarauditor-app --production-branch main` ausführen; beim ersten Mal bittet Wrangler, dich im Browser bei Cloudflare anzumelden. Der Ablauf veröffentlicht mit `--branch main`, jede Veröffentlichung geht also in den Produktivbetrieb.
2. **Leg einen API-Schlüssel an.** Öffne https://dash.cloudflare.com, oben rechts **My Profile** → **API Tokens** → **Create Token** → **Create Custom Token**. Berechtigung **Account** · **Cloudflare Pages** · **Edit**, unter **Account Resources** dein Konto, dann **Continue to summary** → **Create Token** und den Schlüssel kopieren. Die **Account ID** steht auf der Übersicht **Workers & Pages**.
3. **Hinterleg beides als Repository-Geheimnis.** Auf GitHub im Repository **Settings** → **Secrets and variables** → **Actions** → **New repository secret**, einmal für `CLOUDFLARE_API_TOKEN` (der Schlüssel) und einmal für `CLOUDFLARE_ACCOUNT_ID` (die Account ID).
4. **Prüf die Umgebungsvariablen.** `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` und `VITE_CRM_ORIGIN` stehen unter `env` in `.github/workflows/deploy-app.yml`. Alle drei sind absichtlich öffentlich: Sie stecken im Code, den der Browser lädt, und die Zeilen-Berechtigungen der Datenbank schützen die Daten. Für ein anderes Supabase-Projekt dort die Project URL und den öffentlichen Schlüssel (anon) aus Schritt 1.5 eintragen. Variablen in der Cloudflare-Oberfläche wirken nicht, weil der Bau auf GitHub läuft. `VITE_CRM_ORIGIN` ist die Adresse des CRM hinter **Kundenakte im CRM öffnen** (`https://crm.speedofthespirit.dev`); fehlt eine der drei Variablen, zeigt die App beim Start, welche.
5. **Veröffentliche.** Den freigegebenen Stand auf den Branch hochladen, zum Beispiel `git push origin main:app-veroeffentlichen`. Nach wenigen Minuten ist der Lauf unter **Actions** → **Deploy app** grün und die App läuft unter `radarauditor-app.pages.dev`. Zeigt sie „App nicht eingerichtet“, war beim Bau einer der zwei Supabase-Werte leer.
6. **Häng die eigene Adresse an.** Im Pages-Projekt `radarauditor-app` **Custom domains** → **Set up a custom domain**, `app.radarauditor.speedofthespirit.dev` eingeben, **Continue**, dann **Activate domain**. Liegt `speedofthespirit.dev` schon bei Cloudflare, legt Cloudflare den Eintrag selbst an. Nach einigen Minuten steht dort „Active“.

## 3. Google Cloud

1. **Leg ein Projekt an.** Öffne https://console.cloud.google.com, oben die Projektauswahl → **New project**, Name `radarauditor`, **Create**. Danach oben dieses Projekt auswählen.
2. **Hinterleg ein Rechnungskonto.** Die Places-Schnittstelle (Schnittstelle zu den Google-Profil-Daten) verlangt eines, auch wenn du im monatlichen Freikontingent bleibst. Links **Billing** → **Link a billing account** und den Schritten folgen.
3. **Schalt die zwei Schnittstellen ein.** **APIs & Services** → **Library**. Nach `PageSpeed Insights API` suchen, öffnen, **Enable**. Zurück, nach `Places API (New)` suchen, öffnen, **Enable**. Achtung: nicht die alte „Places API“ ohne „(New)“.
4. **Leg den Schlüssel an und beschränk ihn.** **APIs & Services** → **Credentials** → **Create credentials** → **API key**. Den angezeigten Schlüssel kopieren. Dann auf den Schlüssel klicken (**Edit API key**), unter **API restrictions** **Restrict key** wählen, im Auswahlfeld **PageSpeed Insights API** und **Places API (New)** anhaken, **Save**. Bei **Application restrictions** bleibt **None**, weil sich die Adresse deines Internetanschlusses ändern kann.

## 4. Der PC: Sammler und Claude Code

Voraussetzungen auf dem PC: **Docker Desktop** (https://www.docker.com/products/docker-desktop), **uv** (Python-Werkzeug, https://docs.astral.sh/uv), **Git** und **Claude Code**. Den Projektordner holst du mit `git clone https://github.com/DanielHuette/RepoCity-RadarAuditor.git`; alle Befehle unten laufen in diesem Projektordner.

1. **Leg die Datei `.env` an.** Im Projektordner `.env.example` kopieren und die Kopie `.env` nennen. Sie bleibt auf dem PC und kommt nie ins Repository (`.gitignore` schließt sie aus). Die Zeilen:

   | Variable | Was hineingehört |
   |---|---|
   | `SUPABASE_URL` | Project URL aus Supabase, Schritt 1.5 |
   | `SUPABASE_SERVICE_KEY` | geheimer Schlüssel (service_role) aus Supabase, Schritt 1.5 |
   | `GOOGLE_API_KEY` | der Google-Schlüssel aus Schritt 3.4 |
   | `RADAR_WORK_DIR` | Übergabeordner zwischen Sammler und Claude Code; `./work` lassen |
   | `FORECAST_PATH` | Kurven für die Prognose-Spanne; `config/prognose.yaml` lassen |
   | `TEMPLATES_DIR` | Vorlagen für Bericht und Dokumente; `templates` lassen |
   | `CATALOGUE_PATH` | Prüfkatalog; `config/pruefkatalog.yaml` lassen |
   | `QUESTIONS_PATH` | Fragen an die KI-Assistenten; leer lassen, dann gilt `config/fragen.yaml` |
   | `INDUSTRIES_PATH` | Branchengruppen mit Namen und früher gespeicherten Werten; leer lassen, dann gilt `config/branchen.yaml` |
   | `EVENT_ENDPOINT` | Adresse eines Kundenverwaltungs-Programms, das Ereignisse empfängt; leer lassen |
   | `DATAFORSEO_LOGIN` | Anmeldename bei DataForSEO; eingeschaltet und begrenzt wird DataForSEO in den App-Einstellungen |
   | `DATAFORSEO_PASSWORD` | Passwort für die DataForSEO-Schnittstelle |
   | `DATAFORSEO_EUR_PER_USD` | Euro je US-Dollar für das Monatslimit; `1` rechnet vorsichtig |
   | `OPENAI_API_KEY` | freiwillig: bezahlter Ersatzweg, wenn chatgpt.com den Server-Browser sperrt; leer heißt, kein Ersatzweg |
   | `GEMINI_API_KEY` | freiwillig: bezahlter Ersatzweg, wenn gemini.google.com den Server-Browser sperrt; leer heißt, kein Ersatzweg |
   | `PERPLEXITY_API_KEY` | freiwillig: bezahlter Ersatzweg, wenn perplexity.ai den Server-Browser sperrt oder eine Anmeldung verlangt; leer heißt, kein Ersatzweg |
   | `OPENAI_MODEL` | Modell des ChatGPT-Ersatzwegs; Vorgabe lassen |
   | `CLAUDE_MODEL` | Modell, mit dem Claude Code die Assistenten-Fragen beantwortet; Vorgabe lassen |
   | `GEMINI_MODEL` | Modell des Gemini-Ersatzwegs; Vorgabe lassen |
   | `PERPLEXITY_MODEL` | Voreinstellung (preset) des Perplexity-Ersatzwegs: `fast`, `low` oder `high`; Vorgabe `fast` lassen |
   | `ASSISTANT_TIMEOUT_SECONDS` | Sekunden, die eine Assistenten-Antwort auf der Website oder in Claude Code dauern darf; Vorgabe `120` lassen |
   | `GOOGLE_OAUTH_CLIENT_ID` | nur für Audits mit Kundenzugang: Client-ID aus Abschnitt 9 |
   | `GOOGLE_OAUTH_CLIENT_SECRET` | nur für Audits mit Kundenzugang: Client-Secret aus Abschnitt 9 |

2. **Prüf die Verbindung.** Im Projektordner ein Terminal öffnen und einmal `uv sync` ausführen, dann `uv run radarauditor health`. Erscheint `ok`, stimmen Adresse und Schlüssel. Erscheint `error: missing environment variables: …`, fehlt die genannte Zeile in `.env`.
3. **Start den Sammler.** Docker Desktop starten, im Projektordner `docker compose up -d` ausführen. Beim ersten Mal baut Docker das Abbild (einige Minuten). `docker compose ps` zeigt danach den Dienst `worker` mit „Up“. Der Container startet nach einem Neustart des PCs von selbst, sobald Docker Desktop läuft.
4. **Öffne Claude Code im Projektordner.** Den Befehl `/auswerten` gibt es dann von selbst (Datei `.claude/commands/auswerten.md`).

## 5. Deine Firmendaten für Angebote und Verträge

Im Briefkopf von Angeboten, Leistungsbeschreibungen, Verträgen und Leistungsnachweisen stehen deine Firmendaten; Rechnungen schreibt das CRM mit seinen eigenen Firmendaten. Die Angaben liegen in der Tabelle `settings`. In Supabase **SQL Editor** → **New query**, Folgendes einfügen, die Platzhalter ersetzen und **Run** klicken:

```sql
insert into settings (key, value) values ('supplier', '{
  "name": "Dein Firmenname",
  "owner": "Vorname Nachname",
  "street": "Straße 1",
  "postal_code": "48143",
  "city": "Münster",
  "email": "info@deine-domain.de",
  "phone": "0251 000000",
  "tax_number": "336/0000/0000",
  "vat_id": null,
  "iban": "DE00 0000 0000 0000 0000 00",
  "bic": "XXXXDEXXXXX",
  "bank_name": "Name deiner Bank",
  "small_business": false,
  "payment_terms_days": 14
}'::jsonb)
on conflict (key) do update set value = excluded.value;
```

| Feld | Pflicht | Bedeutung |
|---|---|---|
| `name`, `street`, `postal_code`, `city`, `email` | ja | so, wie es oben auf jedem Dokument steht |
| `iban`, `bic`, `bank_name` | ja | deine Bankverbindung |
| `tax_number` oder `vat_id` | eins von beiden | Steuernummer vom Finanzamt oder Umsatzsteuer-Identifikationsnummer |
| `owner`, `phone` | nein | Inhaber und Telefon |
| `small_business` | nein, Vorgabe `false` | `true`, wenn du Kleinunternehmer bist (Paragraf 19 Umsatzsteuergesetz); dann ohne Umsatzsteuer |
| `payment_terms_days` | nein, Vorgabe 14 | Zahlungsziel, das im Vertrag steht, in Tagen |

## 6. Die Schrift Archivo

Die App bringt die Schrift Archivo selbst mit: `app/public/fonts/Archivo-Variable.woff2` mit der Lizenz `OFL.txt` (SIL Open Font License 1.1). Sie wird nicht von Google geladen, weil das die Adresse jedes Besuchers an Google überträgt und in Deutschland abmahnfähig ist. Die PDF-Berichte im Container nutzen DejaVu Sans, solange Archivo dort nicht installiert ist.

## 7. Freiwillig: Bezahldienste und weitere KI-Assistenten

| Dienst | Wofür | Wo du den Schlüssel bekommst | Zeile in `.env` |
|---|---|---|---|
| DataForSEO | Suchvolumen, Linkdaten, rankende Begriffe (Prüfungen c2, c12, i1–i3, i5, j5) und die Google-Suchen, bei denen der Browser gesperrt wird; standardmäßig an, mit Monatslimit in den App-Einstellungen | https://app.dataforseo.com → **API Access** | `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` |
| OpenAI | fragt ChatGPT, wenn chatgpt.com den Server-Browser sperrt | https://platform.openai.com/api-keys → **Create new secret key** | `OPENAI_API_KEY` |
| Google Gemini | fragt Gemini, wenn gemini.google.com den Server-Browser sperrt | https://aistudio.google.com → **Get API key** | `GEMINI_API_KEY` |
| Perplexity | fragt Perplexity, wenn perplexity.ai den Server-Browser sperrt oder eine Anmeldung verlangt | https://www.perplexity.ai/settings/api → **Generate** | `PERPLEXITY_API_KEY` |

ChatGPT, Gemini und Perplexity werden zuerst auf ihren Websites im Server-Browser gefragt; Claude immer über Claude Code. Alle vier Dienste oben kosten je Abfrage Geld. Nach einer Änderung an `.env`: `docker compose up -d` startet den Sammler mit den neuen Werten neu.

## 8. Mails lesen

Der Mail-Dienst des CRM legt eingehende Mails ab und legt dem RadarAuditor zwei Arten vor: Mails unbekannter Absender (ist das ein möglicher Kunde?) und Mails von Kunden mit verschicktem Angebot (sagen sie zu oder ab?). Bei jedem Leerlauf nimmt der Sammler auf dem Server mehrere davon auf einmal mit `crm.claim_mail_triage` und fragt Claude Code je Leerlauf zu einer Mail, nach den angeforderten Dokumenten, über das Claude-Abo und ganz ohne Werkzeuge, ohne Einstellungen, Plugins, Hooks oder MCP-Server des Servers; die bezahlte Schnittstelle wird nie benutzt. Ist Claude Code nicht angemeldet oder das Abo-Kontingent aufgebraucht, gibt der Sammler die geholten Mails frei, ohne sie als gescheitert zu zählen, und liest erst weiter, wenn Claude Code neu geprüft wurde. Die Antworten gehen zurück ans CRM, das den Absender ablegt oder die Annahme einträgt.

| Zeile in `.env` | Vorgabe | Bedeutung |
|---|---|---|
| `MAIL_TRIAGE_BATCH` | 5 | Mails, die auf einmal aus dem CRM geholt werden, höchstens 50; je Leerlauf wird eine gelesen; 0 schaltet das Mail-Lesen ab |
| `MAIL_TRIAGE_TIMEOUT_SECONDS` | 120 | Sekunden, die eine Antwort dauern darf |
| `MAIL_TRIAGE_CLAIM_SECONDS` | 900 | Sekunden, die genommene Mails für den Sammler reserviert bleiben, 30 bis 3600; mindestens Anzahl mal Antwortzeit, und das darf 3600 nicht übersteigen |
| `MAIL_TRIAGE_MODEL` | `sonnet` | Modell, mit dem Claude Code die Mails liest |

Das Mail-Lesen braucht die CRM-Migration mit `crm.mail_triage` und ein auf dem Server angemeldetes Claude Code. Ob es läuft, steht in der App neben dem Serverstand („Mails lesen steht: …“ mit Grund) und im Lebenszeichen (`mail_reading_available`, `mail_reading_reason`).

## 9. Google-Zugriff für Audits mit Kundenzugang

Ein Audit mit Zugang liest Anrufe, Routenanfragen und Website-Klicks aus dem Unternehmensprofil, Suchanfragen mit Klicks aus der Search Console, Besuche und Anfragen aus Google Analytics und die Antworten des Inhabers auf Bewertungen (Prüfungen m1 bis m4, dazu g4 und g5 aus den echten Antworten). Es liest sie mit **deinem** Google-Konto, das jeder Kunde als Manager des Profils und als Nutzer der Search Console und von Analytics einträgt (Anleitung für den Kunden am Ende der [Betriebsanleitung](BETRIEBSANLEITUNG.de.md#anhang-anleitung-für-den-kunden)). Du gibst RadarAuditor die Erlaubnis einmal; danach erneuert der Sammler den Zugriff selbst.

1. **Schalt die sechs Schnittstellen ein.** Im Google-Cloud-Projekt aus Abschnitt 3: **APIs & Services** → **Library**, nacheinander suchen und **Enable**: `Google Search Console API`, `Google Analytics Data API`, `Business Profile Performance API`, `My Business Account Management API`, `My Business Business Information API`, `Google My Business API`.
2. **Beantrag den Zugang zu den Unternehmensprofil-Schnittstellen.** Google gibt sie erst nach einem kurzen Antrag frei: https://developers.google.com/my-business/content/prereqs öffnen, **Request access to the API** folgen und die Projektnummer eintragen (Übersicht des Projekts, **Project info**). Die Freigabe dauert einige Tage. Bis dahin stehen die Profilzahlen (m1) und die Antworten des Inhabers (m4) auf „nicht messbar“ und das Audit trägt eine Warnung; Search Console und Analytics laufen schon.
3. **Richte den Zustimmungsbildschirm ein.** **APIs & Services** → **OAuth consent screen** (neuere Oberfläche: **Google Auth Platform**). **Get started**: App-Name `RadarAuditor`, deine E-Mail als Support-Adresse, bei **Audience** **External**, Kontakt-E-Mail, zustimmen, **Create**. Unter **Audience** deine eigene Google-Adresse als **Test user** eintragen. Unter **Data access** → **Add or remove scopes** die Zeilen `.../auth/business.manage`, `.../auth/webmasters.readonly` und `.../auth/analytics.readonly` anhaken, **Update**, **Save**.
4. **Leg den Desktop-Client an.** **APIs & Services** → **Credentials** → **Create credentials** → **OAuth client ID**, **Application type** **Desktop app**, Name `RadarAuditor PC`, **Create**. **Client ID** und **Client secret** in `.env` als `GOOGLE_OAUTH_CLIENT_ID` und `GOOGLE_OAUTH_CLIENT_SECRET` eintragen.
5. **Erteil die Erlaubnis einmal.** Am PC im Projektordner: `uv run radarauditor google auth`. Der Befehl zeigt eine lange Adresse; im Browser öffnen, mit dem Google-Konto anmelden, das die Kunden eintragen, die Warnung „Google hat diese App nicht überprüft“ mit **Weiter** bestätigen und alle drei Zugriffe erlauben. Der Tab zeigt danach „RadarAuditor received the Google authorisation“, das Terminal `stored Google authorisation for 3 scopes`. Der Erneuerungsschlüssel liegt in Supabase (Tabelle `settings`, Schlüssel `google_oauth`); der Sammler-Container braucht nichts weiter. Läuft der Browser auf einem anderen Gerät: `uv run radarauditor google auth --manual`. Nach der Zustimmung zeigt der Browser eine Fehlerseite; deren ganze Adresse ins Terminal kopieren.
6. **Halt den Zugriff am Leben.** Solange der Zustimmungsbildschirm im Status **Testing** steht, beendet Google die Erlaubnis nach sieben Tagen. Unter **Audience** auf **Publish app** klicken (Status **In production**); für dein eigenes Konto prüft Google nichts, die Warnung aus Schritt 5 bleibt. Meldet ein Audit „Google authorisation was revoked or expired“, Schritt 5 wiederholen.
