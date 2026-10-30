# RepoCity RadarAuditor

Englische Fassung: [README.md](README.md)

Misst, wie sichtbar ein örtlicher Betrieb bei Google, in Verzeichnissen und in KI-Assistenten ist, und macht daraus Verkaufsbericht und Angebot.

Projektseite: [radarauditor.speedofthespirit.dev/#projekt](https://radarauditor.speedofthespirit.dev/#projekt)

Stand: 108 Prüfungen in 14 Bereichen; Teststand ohne Kundenbetrieb.

RadarAuditor misst, wie sichtbar ein örtlicher Betrieb bei Google, auf Google Maps, in Verzeichnissen und in KI-Assistenten ist, bewertet das gegen einen festen Prüfkatalog, formuliert die Befunde in Alltagssprache und erzeugt Verkaufsbericht, Angebot und den monatlichen Vorher-Nachher-Bericht. Eine installierbare Web-App für Handy, Tablet und PC; ein Python-Sammler, der Daten holt; Claude Code schreibt über das Claude-Abo.

## Arbeitsteilung

Entschieden am 24. Oktober 2026; die vollständige Absprache steht in `Dokumentation/INTEGRATION.de.md` im Firmen-Repository RepoCity.

| Teil | Zuständig für |
|---|---|
| RadarAuditor | Prüfungen, Auswertung, Angebote vor Ort (`crm.create_quote`), Optimierung von Website und Google-Profil, Prüfberichte, Lesen eingehender Mails mit Claude Code auf dem Server, Beantworten des Assistenten im Cockpit |
| CRM | Kunden und ihr Stand, Versand jeder Mail (auch des Angebots über `crm.send_quote`), Aufträge, Abos, Rechnungen, Zahlungen, Mahnungen, Paket für den Steuerberater, Termine der Monatsprüfung |

Die Monatsprüfung eines Abos stößt das CRM an (`public.queue_monthly_audit`, Zweck `monthly`); der RadarAuditor schreibt dann den Vorher-Nachher-Bericht. Ist die letzte Änderung eines Auftrags erledigt, schreibt die Datenbank das Ereignis `optimization.finished`, und das CRM schließt den Auftrag ab.

## Stand

Die App ist unter app.radarauditor.speedofthespirit.dev veröffentlicht. Die automatische Prüfung läuft bei jedem Hochladen (`.github/workflows/ci.yml`: `ruff`, `mypy`, `pytest`, App-Prüfung, App-Tests und App-Bau) und noch einmal vor jeder Veröffentlichung. Nur ein Hochladen auf den Branch `app-veroeffentlichen` veröffentlicht die App (`.github/workflows/deploy-app.yml`); ein Hochladen auf `main` veröffentlicht nichts. Offene Punkte stehen im [Baubericht](Dokumentation/BUILD_REPORT.de.md#offene-punkte).

Teststand ohne Kundenbetrieb.

## Bestandteile

| Teil | Läuft wo | Zweck |
|---|---|---|
| `app/` | Cloudflare Pages unter app.radarauditor.speedofthespirit.dev, installierbare Web-App | Reiter Prüfen (Website-Adresse, Auswahl der Prüfungen, Fortschritt), Auswertung (erkannte Angaben, Gesprächstext, Hebel, alle Prüfungen, Bericht), Angebot (Positionen aus den offenen Punkten, Preise aus dem CRM, PDF, Entscheidung, Optimierung), Einstellungen |
| `site/` | Cloudflare Pages unter https://radarauditor.speedofthespirit.dev (veröffentlicht) | öffentliche Projektseite, Deutsch und Englisch |
| `src/radarauditor/` | Docker-Container auf dem RepoCity-Server | sammelt Daten, bewertet Prüfungen, erzeugt Berichte und Dokumente, liest die Mails, die das CRM vorlegt, beantwortet den Assistenten im Cockpit |
| `.claude/` und `skills/` | Claude Code auf dem Server, angemeldet mit dem Claude-Abo | macht aus Rohdaten Befunde, Lösungen und Berichtstexte (`/auswerten`) |
| `supabase/` | Supabase (kostenloser Tarif, Frankfurt) | Datenbank, Anmeldung, Dateiablage, Auftragswarteschlange |

## Assistent im Cockpit

Daniel tippt oder spricht im Cockpit eine Anfrage. Neben den Aufträgen holt der Worker (`src/radarauditor/jobs/assistant.py`) eine Anfrage nach der anderen mit `crm.claim_assistant_request` ab und:

1. lädt bei einer Aufnahme `crm-assistant/anfragen/<id>.wav` mit dem Dienstschlüssel herunter, schickt sie an die Spracherkennung des Telefonassistenten (`POST /transcribe?language=de`, Mono-WAV mit 16 Kilohertz, höchstens 60 Sekunden, Antwort `{"text": ...}`), löscht die Aufnahme in jedem Fall und speichert den Text mit `crm.save_assistant_transcript`;
2. lässt Claude Code mit dem Abo-Login ohne jedes Werkzeug (wie beim Lesen der Mails) die Anfrage als genau eine Absicht im JSON-Format lesen: `create_contact`, `create_appointment` (`starts_at` mit Versatz zu UTC), `draft_mail`, `revenue` oder `unclear`. Die Anweisung nennt Datum und Uhrzeit in Europe/Berlin, setzt die Anfrage zwischen zwei Zeilen mit einer zufälligen Kennung und sagt, dass sie Daten sind und keine Anweisungen;
3. prüft die Antwort mit pydantic (eine Form je `action`, keine weiteren Schlüssel, eine Uhrzeit nur mit Versatz) und übergibt sie an `crm.apply_assistant_intent`, das noch einmal prüft, die Aufgabe ausführt und im Cockpit meldet. Eine entworfene Mail wartet auf „Senden“.

Jeder andere Fehler beendet die Anfrage mit einem deutschen Satz über `crm.fail_assistant_request`, zum Beispiel „Die Spracherkennung ist gerade nicht erreichbar.“ oder „Claude Code ist nicht angemeldet.“; die Einzelheiten stehen im Protokoll des Workers, der Text der Anfrage nie. Solange keine Anfrage wartet, löscht der Worker höchstens alle zehn Minuten die Aufnahmen, die `crm.orphan_recordings` nennt: älter als zehn Minuten und zu keiner Anfrage gehörend, etwa wenn das Hochladen klappte, die Anfrage aber nie gespeichert wurde.

| Einstellung in `.env` | Vorgabe | Bedeutung |
|---|---|---|
| `SPEECH_URL` | leer | Adresse der Spracherkennung; leer beantwortet gesprochene Anfragen mit „Die Spracherkennung ist auf dem Server nicht eingerichtet.“ |
| `SPEECH_TIMEOUT_SECONDS` | 60 | Sekunden je Umwandlung, bis zu 3 Versuche |
| `COCKPIT_ASSISTANT_POLL_SECONDS` | 10 | Pause, solange keine Anfrage wartet; 0 schaltet den Assistenten ab |
| `COCKPIT_ASSISTANT_TIMEOUT_SECONDS` | 120 | Sekunden, die Claude Code brauchen darf; 3 × `SPEECH_TIMEOUT_SECONDS` plus dieser Wert muss unter 600 bleiben, weil das CRM eine Anfrage nach 10 Minuten wieder ausgibt |
| `COCKPIT_ASSISTANT_MODEL` | `sonnet` | Modell von Claude Code |

Die Spracherkennung auf demselben Server erreichen: Die Spracherkennung aus `telefon/` nutzt das Netz des Servers und hört nur auf 127.0.0.1:8090; dort kommt der Container des Workers nicht hin. Drei Schritte öffnen sie für die Container des Servers und sonst niemanden:

1. `telefon/.env`: `SPEECH_HOST=0.0.0.0`, dann in `telefon/` `docker compose up -d speech`.
2. Firewall: `sudo ufw allow from 172.16.0.0/12 to any port 8090 proto tcp` (die Docker-Netze; nach außen bleibt der Zugang zu).
3. `radarauditor/.env`: `SPEECH_URL=http://host.docker.internal:8090`, dann hier `docker compose up -d`; `docker-compose.yml` lenkt `host.docker.internal` auf den Server (`host-gateway`).

Prüfen aus dem Worker: `docker compose exec worker python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:8090/health', timeout=3).read())"`.

## Dokumentation

| Dokument | Inhalt |
|---|---|
| [Betriebsanleitung](Dokumentation/BETRIEBSANLEITUNG.de.md) | jeder Schritt von der Website-Adresse bis zum Abschicken des Angebots, Optimierung und Vorher-Nachher-Bericht, mit Bildern |
| [Einrichtung](Dokumentation/SETUP.de.md) | Supabase, Cloudflare Pages, Google Cloud, `.env`, Firmendaten, Mails lesen |
| [Server](Dokumentation/SERVER.de.md) | Sammler auf einem gemieteten Ubuntu-Server: Docker, `.env`, Datenbank-Änderungen, einmalige Anmeldungen, Aktualisieren, Sicherungen |
| [Architektur](Dokumentation/ARCHITEKTUR.de.md) | Teile, Datenflüsse, Übergabe an Claude Code, Grenzen, Entscheidungen |
| [Baubericht](Dokumentation/BUILD_REPORT.de.md) | Module, Testzahlen, Prüfergebnisse, offene Punkte |

## Befehle

```
uv sync --all-extras
uv run radarauditor --help
```

| Befehl | Tut |
|---|---|
| `uv run radarauditor health` | prüft `.env` und die Verbindung zu Supabase, meldet `ok` |
| `uv run radarauditor worker run` | arbeitet die Warteschlange ab (macht der Container) |
| `uv run radarauditor audit run <Kunden-Kennung> [--with-access]` | legt ein Audit ohne die App an |
| `uv run radarauditor jobs pull` | schreibt wartende Auswertungen nach `work/analyse/` für Claude Code |
| `uv run radarauditor jobs push` | lädt `findings.json` und `texte.json` aus `work/analyse/` hoch |
| `uv run radarauditor report sales <Audit-Kennung>` | erstellt den Verkaufsbericht als PDF |
| `uv run radarauditor report monthly <Audit-Kennung>` | erstellt den Vorher-Nachher-Bericht als PDF |
| `uv run radarauditor document build <Art> <Kunden-Kennung> <daten.json>` | erstellt Angebot, Leistungsbeschreibung, Vertrag oder Leistungsnachweis |
| `uv run radarauditor google auth [--manual]` | erteilt einmal die Google-Erlaubnis für Audits mit Kundenzugang |
| `docker compose up -d` | startet den Sammler-Container |
| `uv run python scripts/screenshots.py` | erzeugt die Bilder der Betriebsanleitung neu aus Beispieldaten |

Die Befehle der App stehen in [app/README.md](app/README.md).

## Erstes Audit in 10 Schritten

Ausführlich und mit Bildern: [Betriebsanleitung, Abschnitt 2](Dokumentation/BETRIEBSANLEITUNG.de.md#2-ein-betrieb-von-der-website-adresse-bis-zum-angebot).

1. Dienste einmal nach [SETUP.de.md](Dokumentation/SETUP.de.md) einrichten und den Sammler auf dem Server starten ([SERVER.de.md](Dokumentation/SERVER.de.md)).
2. Unter app.radarauditor.speedofthespirit.dev anmelden. Die App öffnet den Reiter **Prüfen** mit dem Radar.
3. Nur die Website-Adresse eingeben und **Prüfen** tippen. Betrieb, Ort, Branche und Suchbegriffe ermittelt der Server selbst (`src/radarauditor/collectors/identify.py`; die erste Quelle mit einem Wert gilt: JSON-LD auf der Startseite, Impressum, Fußbereich, Google-Profil).
4. Prüfungen auswählen: 108 Prüfungen in 14 Bereichen a bis n aus `config/pruefkatalog.yaml`. Jeder Bereich klappt auf Tippen auf und zeigt je Prüfung einen Satz; die Auswahl bleibt auf dem Gerät gespeichert und geht mit der Prüfung mit (`audits.disabled_checks`).
5. **Prüfung starten** tippen. Der Fortschritt zeigt Wartet, Sammelt, Bewertet, Wertet aus, Fertig; im Schritt „Wertet aus“ lässt der Sammler Claude Code `/auswerten` ohne Bedienung ausführen.
6. **Auswertung ansehen** tippen. Oben steht **Erkannt** mit Betrieb, Ort und Branche; **Korrigieren** startet eine neue Prüfung mit den richtigen Angaben und denselben Prüfungen.
7. **Das sagen Sie dem Kunden** dem Kunden vorlesen (`talk_text` aus `texte.json`) und **Die größten Hebel** durchgehen, die drei Prüfungen mit dem meisten offenen Potenzial.
8. Unter **Alle Prüfungen** einzelne Prüfungen aufklappen: warum sie zählt, was gemessen wurde, die Lösung; bei g4 stehen Beispiele für Bewertungen ohne Antwort des Inhabers.
9. Reiter **Angebot**, Prüfung wählen: Die Positionen kommen aus den offenen Punkten der Prüfung, die Preise aus dem CRM. **Angebot erstellen**, dann **Angebot abschicken** (das CRM schickt das Angebot mit dem Prüfbericht als PDF per Mail; fehlt die E-Mail-Adresse, fragt die App danach und speichert sie im CRM), dann die Entscheidung des Kunden (**Angenommen** legt im CRM den Auftrag an); nach der Annahme **Optimierung starten**.
10. **Kundenakte im CRM öffnen** öffnet die Akte des Kunden im CRM, wo Angebote und Aufträge liegen.
