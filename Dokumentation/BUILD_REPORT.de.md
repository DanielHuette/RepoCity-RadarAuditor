# Baubericht

Englische Fassung: [BUILD_REPORT.md](BUILD_REPORT.md)

## Skelett angelegt
**Context:** Erster Stand vor dem Bau. | **Input:** Gespräch vom 2026-10-15/16, Prüfkatalog. | **Output:** Ordnerstruktur, Signaturen, Schema, Katalog, Doku-Gliederung.

| Befund | Änderung | Nachweis |
|---|---|---|
| Kein Projekt vorhanden | Skelett nach senior-engineering-standard | Commit „Skeleton“ |

## Python-Paket gebaut
**Context:** `src/radarauditor/`, Stand 2026-10-16 nach vier Bauwellen (Kern, Sammler, Bewertung; Abgleich, Berichte, App; Anbindung, Doku; Zugang, Bezahldaten, Betrieb). | **Input:** Signaturen und Docstrings des Skeletts. | **Output:** Lauffähige Module mit Tests.

| Modul | Gebaut | Testdateien | Tests |
|---|---|---|---|
| `collectors/` | Website-Crawler und -Parser, PageSpeed Insights, Places API (New), Google-, Bing- und Bildersuche im Browser, Verzeichnisse, KI-Assistenten (OpenAI, Claude, Gemini, Perplexity), DataForSEO (Platzierungen, Raster, Suchvolumen, Links, Lücken), Google-Zugang (OAuth, Unternehmensprofil-Leistung, Search Console, Analytics, Inhaber-Antworten), Gesamtlauf | `test_website_parser`, `test_pagespeed`, `test_places`, `test_serp_parser`, `test_directories`, `test_assistants`, `test_paid_rankings`, `test_paid_data`, `test_access`, `test_google_auth`, `test_collect_run` | 180 |
| `scoring/` | Katalog laden und prüfen, Bewertung aller 108 Prüfungen in den 14 Bereichen a bis n, Bereichs- und Gesamtpunkte, Platzierungen und Rasterpunkte aus Rohdaten | `test_catalog`, `test_evaluator`, `test_rankings`, `test_checks_a` bis `test_checks_n` | 455 |
| `reporting/` | Grafiken, Prognose-Spanne, Verkaufs- und Monatsbericht mit Profilzahlen und Suchbegriff-Lücken, deutsche Zahlen und Daten | `test_charts`, `test_forecast`, `test_report`, `test_report_repositories`, `test_rendering` | 92 |
| `documents/` | Dokumentarten, Anforderungen aus der App | `test_documents`, `test_document_requests` | 72 |
| `jobs/` | Warteschlange, Wiederholungen, Leerlaufschritt für Dokumente, Übergabe an Claude Code mit Schemaprüfung | `test_handoff`, `test_worker` | 76 |
| `db/` | Zugriff auf Tabellen und Ablagen | `test_repositories` | 48 |
| `integrations/` | Ereignisse an ein Kundenverwaltungs-Programm | `test_events` | 7 |
| `config.py` | Einstellungen aus `.env` mit verständlichen Fehlermeldungen | `test_config` | 10 |
| `cli.py` | `health`, `worker run`, `audit run`, `jobs pull`, `jobs push`, `report sales`, `report monthly`, `document build`, `google auth` | `test_cli` | 24 |
| Summe | | 41 Dateien | 983 |

## App gebaut
**Context:** Installierbare Web-App in `app/` (Preact, Supabase, Chart.js, Progressive Web App). | **Input:** Schema und Übergabe-Absprachen. | **Output:** Seiten, Komponenten, Tests.

| Teil | Gebaut | Testdateien | Tests |
|---|---|---|---|
| Seiten | Anmelden, Start, Kunden, Kunde (Standorte, Wettbewerber, Suchbegriffe, Zugang, Audits, Dokumente), Audit (Punkte, Ampel, Befunde mit Kopieren, Wettbewerber, Verlauf, Beiträge und Bewertungsantworten), Dokumente, Einstellungen | | |
| Bewertung | Bereichsnamen, Punkte wie `evaluator.py` | `scoring.test.ts` | 10 |
| Dokumente | Formularfelder, Prüfung, Fehlerzeile | `documents.test.ts` | 12 |
| Platzierungen | Vergleich je Suchbegriff, Achsenbeschriftung | `rankings.test.ts` | 9 |
| Kartenraster | Rasterpunkte | `grid.test.ts` | 6 |
| Texte | `texte.json` lesen und prüfen | `texts.test.ts` | 8 |
| Formate | Euro, Datum, Beträge, Adressen, Kennungen | `format.test.ts` | 11 |
| Startseite | nächste Schritte, Suche | `pipeline.test.ts` | 4 |
| Kopieren | Inhalt des Kopieren-Knopfs | `clipboard.test.ts` | 6 |
| Fehler | verständliche Fehlermeldungen | `errors.test.ts` | 5 |
| Summe | | 10 Dateien | 81 |

## Dokumentation
**Context:** Unterlagen für den Betreiber. | **Input:** Code, Bildschirmfotos. | **Output:** Anleitungen mit Bildern.

| Datei | Inhalt |
|---|---|
| `BETRIEBSANLEITUNG.de.md` / `.md` | acht Abschnitte, 24 Bilder (Handy und PC), Anhang mit Anleitung für den Kunden |
| `SETUP.de.md` / `.md` | Supabase, Cloudflare Pages, Google Cloud, `.env`, Lieferantendaten, Schrift, Bezahldienste, Mails lesen, Google-Zugang |
| `ARCHITEKTUR.de.md` / `.md` | Teile, Ablauf, Übergabedateien, Entscheidungen, Grenzen |
| `scripts/screenshots.py` | erzeugt alle Bilder gegen nachgebildete Daten |

## Abschlussprüfung
**Context:** Stand 2026-10-16. | **Input:** Repository. | **Output:** Prüfergebnisse.

| Prüfung | Befehl | Ergebnis |
|---|---|---|
| Python-Formatierung | `uv run ruff format --check .` | bestanden |
| Python-Regeln | `uv run ruff check .` | All checks passed! |
| Typprüfung | `uv run mypy src` | Success: no issues found in 57 source files |
| Python-Tests | `uv run pytest -q` | 983 passed, 1 warning (WeasyPrint: HarfBuzz-Subset fehlt im Prüfsystem) |
| App-Prüfung | `npm run check` | ohne Fehler (TypeScript und ESLint) |
| App-Tests | `npm test` | 81 passed (10 Dateien) |
| App-Bau | `npm run build` | erfolgreich |
| Bildschirmfotos | `uv run python scripts/screenshots.py --font …` | 48 Bilder, geprüft |
| Berichte und Dokumente | PDF-Seiten aus den Tests angesehen | Verkaufsbericht 9 Seiten, Angebot mit allen Angaben |

## Abrechnung ins CRM verlegt
**Context:** Entschieden von Daniel am 24. Oktober 2026: Der RadarAuditor prüft, wertet aus und schreibt Angebote; Kundenstand, Rechnungen, Zahlungen, das Paket für den Steuerberater, die Termine der Monatsprüfung und jede ausgehende Mail gehören dem CRM. Die Tabellen oben zeigen den Stand vom 2026-10-16 ohne die hier entfernten Teile. | **Input:** Absprache `Dokumentation/INTEGRATION.de.md` im Firmen-Repository RepoCity. | **Output:** Abrechnung entfernt, Mails lesen neu, Angebot abschicken neu.

| Befund | Änderung | Nachweis |
|---|---|---|
| Rechnungen, Gutschriften, eigene Rechnungsnummern und Zahlungen im RadarAuditor | aus Code, Vorlagen, Modellen und Datenbank entfernt (Migration 014; gespeicherte Rechnungen halten sie an, statt gelöscht zu werden) | `supabase/migrations/014_remove_billing.sql`, `test_migration_database.py` |
| Jahresexport fürs Finanzamt | `exports/`, `config/export.yaml` und `radarauditor export tax` entfernt | `test_cli.py` |
| Monatslauf und Kundenphase | `run_monthly`, `radarauditor monthly run`, Auftragsart `monthly`, `customers.stage` entfernt; die Monatsprüfung stößt das CRM an, der Berichtsschritt wählt den Vorher-Nachher-Bericht nach `audits.purpose` | `test_worker.py` |
| Mails von Kunden und unbekannten Absendern | `jobs/mail_triage.py` auf dem Server: je Mail ein Aufruf von Claude Code ganz ohne Werkzeuge, die Mail als ungeprüfte Daten abgegrenzt, die Antwort gegen die beiden Ergebnisformen geprüft; Lebenszeichen-Feld `mail_reading_available` | `test_mail_triage.py`, `test_heartbeat.py` |
| Prüfung vom 25. Oktober 2026 | je Leerlauf eine Mail nach den Dokumenten; nach einem Problem liest der Sammler erst, wenn Claude Code neu geprüft wurde; ein aufgebrauchtes Abo wird erkannt und gibt die Mails ohne Fehlversuch frei; im Aufruf ohne Werkzeuge laden keine Einstellungen, Plugins, Hooks oder MCP-Server; lange Antworten werden gekürzt; lehnt das CRM eine Antwort ab, scheitert die Mail mit seinem Grund; **Angebot abschicken** lässt sich abbrechen und gibt nach fünf Minuten auf; 014 löscht die leere Ablage `exports` | `test_mail_triage.py`, `test_worker.py`, `test_claude_code.py`, `SendQuote.test.tsx`, `test_migration_database.py` |
| Angebot von Hand verschickt | **Angebot abschicken**: fragt eine fehlende E-Mail-Adresse ab und speichert sie im CRM, fordert das PDF an, wenn es fehlt, dann `crm.send_quote` | `SendQuote.test.tsx`, `quoteSending.test.ts` |

| Prüfung | Befehl | Ergebnis |
|---|---|---|
| Python-Regeln und Format | `uv run ruff check .`, `uv run ruff format --check .` | bestanden |
| Typen | `uv run mypy src` | keine Fehler in 83 Quelldateien |
| Python-Tests mit PostgreSQL | `uv run pytest` | 1804 bestanden (Lauf vom 30. Oktober 2026) |
| App | `npm run check`, `npm test`, `npm run build` | keine Fehler, 365 bestanden in 43 Testdateien, gebaut |

## Offene Punkte
**Context:** Nicht im Prüfsystem möglich. | **Input:** Code. | **Output:** Liste für den ersten echten Lauf.

| Befund | Folge | Nachweis |
|---|---|---|
| Kein Lauf gegen eine echte Firma, Google, DataForSEO oder Supabase; alle Fremddienste in den Tests nachgebildet | Erster echter Lauf auf dem PC des Betreibers nach der Einrichtung | `SETUP.de.md` |
| Docker-Abbild nicht gebaut (kein Docker im Prüfsystem); Abhängigkeiten lösen sich in frischer Umgebung auf | `docker compose up -d` beim ersten Mal beobachten | `Dockerfile`, `requirements.txt` |
| Unternehmensprofil-Schnittstellen erst nach Googles Freigabe nutzbar | Prüfungen m1 und m4 bis dahin „nicht messbar“ | `SETUP.de.md` Abschnitt 9 |
| Wortlaut von Vertrag und Leistungsbeschreibung ohne anwaltliche Prüfung | einmal prüfen lassen, danach nur ausfüllen | `templates/documents/` |
| Mails lesen und **Angebot abschicken** nur gegen Nachbildungen getestet; sie brauchen die CRM-Migration mit `crm.claim_mail_triage`, `crm.classify_mail_triage`, `crm.fail_mail_triage` und `crm.send_quote` | erste echte Mail und erstes verschicktes Angebot nach der CRM-Migration | `test_mail_triage.py`, `SendQuote.test.tsx` |
| Supabase kann das Löschen der Ablage `exports` per SQL ablehnen, und 014 lässt sie stehen, solange Dateien darin liegen | 014 sagt das dann; im Supabase-Dashboard löschen | `014_remove_billing.sql` |
