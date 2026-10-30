# Architektur

Englische Fassung: [ARCHITEKTUR.md](ARCHITEKTUR.md)

## Teile und Datenflüsse
**Context:** Wie App, Sammler, Datenbank und Claude Code zusammenspielen. | **Input:** Website-Adresse und Auswahl der Prüfungen. | **Output:** Auswertung, Bericht, Angebot, Dokumente, eingeordnete Mails.

```
Handy/Tablet/PC ──► App (Cloudflare Pages, installierbare Web-App) ──► Supabase (Datenbank, Dateien, Warteschlange)
                                                        ▲            │
                                                        │            ▼
                              Claude Code (Server, Abo) ◄── jobs pull / push ── Sammler-Container (Server)
                                                                                   │
                                     Website · PageSpeed · Places · Google-Suche · Verzeichnisse · KI-Assistenten
```

| Teil | Ordner | Läuft wo | Was gebaut ist |
|---|---|---|---|
| App | `app/` | Cloudflare Pages, app.radarauditor.speedofthespirit.dev | Anmeldung; Reiter Prüfen mit Radar: nur Website-Adresse, Auswahl der Prüfungen je Bereich, Fortschritt live; Reiter Auswertung: erkannter Betrieb, Ort und Branche mit Korrektur, Gesprächstext, die drei größten Hebel, alle Prüfungen mit Messwerten und Lösung (g4 mit Bewertungen ohne Antwort des Inhabers), Bericht, Beiträge und Bewertungsantworten, Platzierungen, Kartenraster, Verlauf, Knopf zur Kundenakte im CRM; Reiter Angebot: Positionen aus den offenen Prüfungen mit Produkten und Preisen aus dem CRM, Angebot im Bereich `crm`, PDF, Entscheidung, Optimierungsentwürfe; Einstellungen mit Datenquellen und Installation |
| Sammler | `src/radarauditor/collectors/` | Docker-Container | Erkennung von Betrieb, Anschrift, Telefon, Branche und Suchbegriffen aus der Website (`identify.py`), Website-Crawler, PageSpeed Insights, Google Maps im Browser (Profil, neueste Bewertungen mit Antworten des Inhabers, Wettbewerber, Platz je Suchbegriff, Kartenraster 5x5; Places API (New) als Ersatzweg, wenn Maps sperrt), Google- und Bing-Suche im Browser, Verzeichnisse, KI-Assistenten (ChatGPT, Gemini, Perplexity auf ihren Websites im Browser, bezahlte Schnittstellen als Ersatzweg; Claude über Claude Code), bezahlte Platzierungen (DataForSEO, abgeschaltet) |
| Bewertung | `src/radarauditor/scoring/` | Docker-Container | Prüfkatalog laden, je Prüfung bestanden, teilweise, nicht bestanden oder nicht messbar; Bereichs- und Gesamtpunkte; Platzierungen aus den Rohdaten |
| Berichte | `src/radarauditor/reporting/` | Docker-Container | Verkaufsbericht und Monatsbericht als PDF mit Grafiken und Prognose-Spanne |
| Dokumente | `src/radarauditor/documents/` | Docker-Container | Prüfbericht mit Angebot, Angebot, Leistungsbeschreibung, Vertrag, Leistungsnachweis als PDF; Rechnungen schreibt das CRM |
| Aufträge | `src/radarauditor/jobs/` | Docker-Container | Warteschlange abarbeiten (collect, score, analyse, report, serp_retry, optimize, apply); der Auswertungsschritt startet Claude Code ohne Bildschirm; `jobs pull` / `jobs push` für Handbetrieb; im Leerlauf liest `mail_triage.py` die Mails, die das CRM vorlegt |
| Ereignisse | `src/radarauditor/integrations/` | Docker-Container | Ereignisse wie „Bericht fertig“ an ein Kundenverwaltungs-Programm senden, wenn `EVENT_ENDPOINT` gesetzt ist |
| Datenbank | `supabase/migrations/` | Supabase | Tabellen, Zugriffsregeln, private Ablagen; `start_field_audit` und `link_crm_customer` (Migration 011); Zweck des Audits und `queue_monthly_audit` (013); Abrechnung entfernt (014) |
| Auswertung | `.claude/commands/auswerten.md`, `skills/radar-auswerter/` | Claude Code im Sammler-Container auf dem Server (`claude -p`) | Befunde, Lösungen, Berichtstexte, Beiträge und Bewertungsantworten |
| Bildschirmfotos | `scripts/screenshots.py` | PC oder Container mit Playwright | baut die App gegen Beispieldaten und erzeugt die Bilder der Betriebsanleitung |
| Veröffentlichung | `.github/workflows/deploy-app.yml` | GitHub Actions | nur bei einem Hochladen auf den Branch `app-veroeffentlichen`: `npm run check`, `npm test`, `npm run build`, dann `wrangler pages deploy` in das Pages-Projekt `radarauditor-app`; `ci.yml` prüft jedes Hochladen und veröffentlicht nichts |

## Ablauf eines Audits
**Context:** Vom Knopf in der App bis zum Bericht. | **Input:** Website-Adresse und Auswahl der Prüfungen. | **Output:** Auswertung und Bericht-PDF.

| Schritt | Wer | Ergebnis |
|---|---|---|
| 1 | App: „Prüfung starten“ ruft `start_field_audit` (Migration 011) mit der Website und den abgeschalteten Prüfungen auf; die Korrektur in der Auswertung gibt Name, Ort und Branche mit | Kunde über den Host der Website gefunden oder neu angelegt, mit dem Host als vorläufigem Namen und leerem Ort und leerer Branche; `link_crm_customer` verknüpft oder legt den Kunden im CRM an, sobald Name und Ort bekannt sind; Audit `requested` mit `audits.disabled_checks`, Auftrag `collect`; Audits mit Zugang kommen aus `radarauditor audit run --with-access` und aus `public.queue_monthly_audit`, das das CRM für die Monatsprüfung eines Abos aufruft (Zweck `monthly`) |
| 2 | Sammler | zuerst ergänzt `identify.py`, was fehlt (Name noch der Host, Ort oder Branche leer, keine Suchbegriffe); je Angabe gilt die erste Quelle: JSON-LD auf der Startseite, Impressum, Kontaktdaten im Fußbereich, Google-Places-Textsuche; danach `link_crm_customer`; ohne gefundenen Ort scheitert das Audit sofort mit deutscher Meldung; dann Rohdaten als `raw.json` in Ablage `raw` mit den erkannten Angaben und ihren Quellen, Platzierungen in `rankings`, Audit `collected`, Auftrag `score` |
| 3 | Sammler | Prüfergebnisse je Katalogpunkt in `check_results` mit Punktepotenzial und erwarteten Mehranfragen, Prüfungen aus `audits.disabled_checks` als `skipped` ohne Gewicht, Gesamtpunkte, Auftrag `analyse`, Audit `awaiting_analysis` |
| 4 | Sammler mit Claude Code ohne Bildschirm (`/auswerten <Ordner>`) | Ordner wie bei `jobs pull`, `findings.json` und `texte.json`, Hochladen wie bei `jobs push`: Befunde in `findings`, Texte und `auswertung.json` in Ablage `raw`, Audit `analysed`, Auftrag `report` |
| 5 | Sammler | Vorher-Nachher-Bericht für ein Audit mit Zweck `monthly`, das ein früheres fertiges Audit hat, sonst der Verkaufsbericht, in Ablage `reports`; Audit `reported`, Ereignis `audit.reported` |
| 6 | App | Auswertung und Bericht öffnen, im Reiter Angebot das Angebot erstellen |

Ein Auftrag, der scheitert, wird bis zu dreimal wiederholt; danach stehen Auftrag und Audit auf `failed` mit Fehlertext.

## Arbeitsteilung, Dokumente und Mails lesen
**Context:** Was neben den Audits läuft. Der RadarAuditor prüft, wertet aus und schreibt Angebote; Kundenstand, Rechnungen, Zahlungen, das Paket für den Steuerberater, die Termine der Monatsprüfung und jede ausgehende Mail gehören dem CRM (entschieden am 24. Oktober 2026, Absprache `Dokumentation/INTEGRATION.de.md` im Firmen-Repository RepoCity). | **Input:** Anforderungen aus der App, der Befehlszeile und dem CRM. | **Output:** PDF-Dateien, verschickte Angebote, eingeordnete Mails, Ereignisse.

| Vorgang | Auslöser | Wer erledigt es | Ergebnis |
|---|---|---|---|
| Dokument | der Reiter Angebot legt eine Zeile der Art `audit_quote` in `documents` mit `file_path` leer an; andere Arten kommen aus `radarauditor document build` | der Sammler erstellt angeforderte Dokumente in seinem Leerlaufschritt, wenn kein Auftrag wartet | PDF in Ablage `documents` |
| Angebot abschicken | **Angebot abschicken** im Reiter Angebot | die App speichert eine fehlende E-Mail-Adresse in `crm.customers`, fordert den Prüfbericht mit Angebot als PDF an, wenn es keinen gibt, wartet darauf und ruft `crm.send_quote` auf; der Mail-Dienst des CRM hängt das PDF an und verschickt die Mail | Angebot `sent`, Mail im Postausgang des CRM |
| Monatsprüfung | die tägliche Routine des CRM meldet ein Abo fällig; ein Tipp im Cockpit ruft `crm.start_audit_run` auf, das `public.queue_monthly_audit` aufruft | Sammler | Audit mit Zweck `monthly`; sein Bericht ist der Vorher-Nachher-Bericht |
| Auftrag fertig | die letzte Änderung eines Auftrags ist nicht mehr `draft`, `approved` oder `manual`, und kein Auftrag `optimize` oder `apply` ist offen | Datenbank-Auslöser (Migration 013) | Ereignis `optimization.finished`; das CRM schließt den Auftrag ab und verschickt die Rechnung |
| Mails lesen | jeder Leerlauf nach den angeforderten Dokumenten und den Ereignissen, solange das Lebenszeichen sagt, dass Mails gelesen werden können, und `MAIL_TRIAGE_BATCH` über null liegt; nach einem Problem erst, wenn Claude Code neu geprüft wurde, nach verlorener Anmeldung oder aufgebrauchtem Abo sofort | Sammler auf dem Server: `crm.claim_mail_triage` nimmt bis zu `MAIL_TRIAGE_BATCH` Mails für `MAIL_TRIAGE_CLAIM_SECONDS`, danach bekommt je Leerlauf eine Mail ein `claude -p` ganz ohne Werkzeuge (`--tools "" --permission-mode dontAsk --setting-sources "" --strict-mcp-config --disable-slash-commands`, Modell `MAIL_TRIAGE_MODEL`, in einem leeren Zwischenordner), die Mail zwischen einer jedes Mal neuen Kennung und als ungeprüfte Daten erklärt | die Antwort, geprüft gegen die beiden Ergebnisformen der Absprache (lange Texte gekürzt), geht an `crm.classify_mail_triage`; eine unbrauchbare, langsame oder gescheiterte Antwort und eine Antwort, die das CRM ablehnt, gehen mit deutschem Grund an `crm.fail_mail_triage`; ohne Anmeldung von Claude Code oder mit aufgebrauchtem Abo werden die geholten Mails ohne Fehlversuch freigegeben; eine Ladung, bei der jede Mail scheiterte, meldet das Lebenszeichen |
| Prüfungen und Quellen | die App legt die Auswahl der Prüfungen mit jedem Audit in `audits.disabled_checks` ab (Migration 011; nur diese Auswahl gilt, leer heißt alle Prüfungen) und setzt den Eintrag `sources` in `settings` (je Quelle `enabled`, `daily_cap`, wahlweise `monthly_cost_cap_eur`; Aufbau und Vorgaben in `src/radarauditor/db/app_settings.py`) | der Sammler liest die Auswahl des Audits beim Bewerten und `sources` zu Beginn jedes Auftrags | abgeschaltete Prüfungen als `skipped` ohne Gewicht; jede Anfrage nach außen wird über `consume_source_quota` in `source_usage` gezählt; ist das Tageslimit erreicht, sind die betroffenen Prüfungen `not_measurable` mit Grund „Tageslimit erreicht“ |
| Lebenszeichen | jede Abfrage der Warteschlange | Sammler | Eintrag `worker_heartbeat` in `settings`: Zeit, Version, Fähigkeiten (Chromium, Claude-Code-Kommandozeile und deren Anmeldung, Browserprofil, `analysis_available` mit dem deutschen Grund `analysis_reason`, wenn falsch, `mail_reading_available` mit `mail_reading_reason`); `analysis_available` ist wahr, wenn Claude Code angemeldet ist und eine kurze Frage ohne Werkzeuge beantwortet hat (`claude -p "Antworte nur mit OK." --tools "" --model haiku`), geprüft alle 10 Minuten; solange es falsch ist, nimmt der Sammler keine neuen `collect`-Aufträge an und liest keine Mails, alle anderen laufen weiter; `mail_reading_available` ist falsch, solange das Mail-Lesen abgeschaltet ist, Claude Code nicht antwortet oder der letzte Durchgang keine Mails aus dem CRM holen konnte, und die App zeigt den Grund neben dem Serverstand |

## Übergabe an Claude Code
**Context:** Der Sammler schreibt nichts in Alltagssprache; das macht Claude Code über das Abo. | **Input:** Ordner `work/analyse/<Auftragsnummer>/`. | **Output:** Befunde und Texte in Supabase.

| Datei | Richtung | Inhalt |
|---|---|---|
| `audit.json` | Sammler → Claude Code | Audit, Kunde, Standorte, Wettbewerber, Suchbegriffe |
| `raw.json` | Sammler → Claude Code | alle Rohdaten des Audits |
| `check_results.json` | Sammler → Claude Code | Ergebnis und Messwerte je Prüfung |
| `katalog.json` | Sammler → Claude Code | Prüfkatalog mit Kundenfrage und Kundennutzen |
| `rankings.json` | Sammler → Claude Code | Platzierungen dieses und des vorherigen Audits |
| `vorheriges_audit.json` | Sammler → Claude Code | nur bei Folge-Audits: das letzte Audit |
| `findings.json` | Claude Code → Sammler | ein Befund je nicht oder teilweise bestandener Prüfung mit Dringlichkeit 1 bis 3, Erklärung, Kundennutzen, Lösung und kopierbarem Ergebnis (`fix_payload`) |
| `texte.json` | Claude Code → Sammler | Einleitung, Zusammenfassung, Prognose-Einordnung, Begründung der Betreuung, Angebotstext, Gesprächstext (`talk_text`: etwa fünf Sätze, die der Betreiber dem Kunden wörtlich sagt, nur in der App oben in der Auswertung), bei Folge-Audits Vorher-Nachher-Text, dazu Beitragsvorschläge und Bewertungsantworten |

`jobs push` prüft `findings.json` gegen das Datenmodell und die Prüfungen des Audits (keine unbekannten oder doppelten Prüfungen, Dringlichkeit 1 bis 3) und `texte.json` gegen das Textmodell; eine neue Auswertung ohne `talk_text` lehnt es ab. Ungültige Ordner bleiben unter `work/analyse/` liegen; gültige wandern nach `work/erledigt/`.

Der Sammler erledigt diese Schritte bei jedem Auftrag `analyse` selbst:

| Schritt | Was passiert |
|---|---|
| Vorbereiten | derselbe Ordner wie bei `jobs pull` |
| Claude Code | `claude -p "/auswerten <Ordner>" --output-format json --permission-mode dontAsk --tools Read,Glob,Grep,Write --allowedTools Read Glob Grep "Edit(//<Ordner>/findings.json)" "Edit(//<Ordner>/texte.json)" --add-dir <Ordner> --no-session-persistence` im Ordner `ANALYSIS_PROJECT_DIR`, abgebrochen nach `ANALYSIS_TIMEOUT_SECONDS` Sekunden; alles außer Lesen und dem Schreiben der zwei Dateien wird ohne Rückfrage abgelehnt |
| Keine Antwort | Claude Code schreibt die Auswertung allein, es gibt keinen Ersatz. Fehlt es, ist es nicht angemeldet, braucht es zu lange oder schreibt es Dateien, die `jobs push` ablehnen würde, kommt der Auftrag über `jobs.run_after` nach 10 Minuten, 30 Minuten, 2 Stunden und danach alle 6 Stunden wieder dran; das Audit bleibt `awaiting_analysis`; der Fehler am Auftrag nennt den Grund ("Claude Code ist nicht installiert", "Claude Code ist nicht angemeldet", "Claude Code hat nicht rechtzeitig geantwortet", "Claude Code hat mit einem Fehler geantwortet", "Claude Code hat ungültige Dateien geschrieben") |
| Hochladen | dieselbe Prüfung und Ablage wie bei `jobs push`; `auswertung.json` (`path`: `claude_code` vom Sammler oder `manual` von `jobs push`) liegt in Ablage `raw` unter `<audit_id>/auswertung.json` |

Potenzial und Mehranfragen je Prüfung (beim Bewerten in `check_results` gespeichert):

| Spalte | Berechnung |
|---|---|
| `potential_points` | Gesamtpunkte, wenn diese Prüfung bestanden wäre, minus die heutigen Gesamtpunkte, mit derselben Bereichsgewichtung wie die Bewertung; 0 bei bestandenen, abgeschalteten und nicht messbaren Prüfungen und im Bereich m |
| `uplift_low`, `uplift_high` | Prognose-Zuwachs (Anfragen nach der Optimierung minus heute, aus Platzierungen, `config/prognose.yaml` und Branche wie im Verkaufsbericht) mal dem Anteil dieser Prüfung am summierten Potenzial aller offenen Prüfungen |

## Grenzen
**Context:** Was das System heute nicht kann. | **Input:** Bau-Stand vom 2026-10-16. | **Output:** Ehrliche Liste für Vertrieb und Betrieb.

| Grenze | Folge | Ausweg |
|---|---|---|
| Google-Suchen aus dem Browser können auf eine Sperrseite (Captcha) laufen | Platzierungen kommen später; das Audit wird zuerst ohne sie bewertet | die übrigen Suchen laufen automatisch nach 15 Min., 1 Std., 4 Std. und 12 Std. noch einmal (Auftrag `serp_retry`), danach wird das Audit neu bewertet; die App zeigt „Google-Suche wird nachgeholt“; mit DataForSEO laufen die gesperrten Suchen sofort über die DataForSEO-Schnittstelle, nachgeholt wird nur, wenn DataForSEO aus ist oder sein Limit erreicht hat |
| Antworten des Inhabers auf Bewertungen liefert die Places-Schnittstelle nicht | sperrt Maps den Browser und antwortet stattdessen die Places-Schnittstelle, stehen Antwortquote und Antwortzeit (g4) auf „teilweise“; den Ton der Antworten auf Kritik (g5) beurteilst immer du | Google Maps im Browser liest die Antworten; ein Audit mit Zugang liest sie aus dem Profil |
| Google Maps zeigt das Datum einer Bewertung nur ungefähr („vor 2 Wochen“); die Places-Schnittstelle liefert höchstens 5 Bewertungen | Bewertungen pro Monat (g3) und Antwortzeit (g4) sind so genau wie Googles Angabe; über die Places-Schnittstelle ist g3 eine Schätzung | `review_source` im Ergebnis nennt die Quelle |
| Prüfungen f4, f5, f6, f9, f10, f11 und f13 (Beschreibung, Leistungen, Produkte, Beiträge, Fragen und Antworten, Servicegebiete, Attribute des Google-Profils) gibt keine Schnittstelle her | sie stehen auf „nicht messbar“ und zählen nicht in die Punkte | du siehst sie dir im Profil selbst an |
| DataForSEO-Prüfungen (c2, c12, i1, i2, i3, i5, j5), wenn das Monatslimit erreicht ist | „nicht messbar“ mit „Monatslimit erreicht“; in den App-Einstellungen abgeschaltet zählen sie nicht, ihr Gewicht verteilt sich im Bereich | Limit in den App-Einstellungen erhöhen; DataForSEO-Zugang in der Umgebung |
| Die Unternehmensprofil-Schnittstellen brauchen eine Freigabe des Cloud-Projekts durch Google; im Status Testing endet die Erlaubnis nach sieben Tagen | bis zur Freigabe stehen Profilzahlen (m1) und Inhaber-Antworten (m4) mit Warnung auf „nicht messbar“ | einmal beantragen und den Zustimmungsbildschirm veröffentlichen, [SETUP Abschnitt 9](SETUP.de.md#9-google-zugriff-für-audits-mit-kundenzugang) |
| Supabase pausiert Projekte im kostenlosen Tarif nach einer Woche ohne Zugriff | App und Sammler erreichen die Datenbank nicht | im Supabase-Dashboard „Restore project“; läuft der Sammler, pausiert das Projekt nicht |

## Entscheidungen
**Context:** Festgelegt am 2026-10-16. | **Input:** Gespräch. | **Output:** Vorgaben.

| Entscheidung | Begründung |
|---|---|
| Keine Anthropic-Schnittstelle, Auswertung über Claude Code und Abo | Keine nutzungsabhängigen Kosten |
| Rankings zuerst über echte Suchen im Browser; DataForSEO, wo kein kostenloser Weg das volle Ergebnis liefert | vollständige Audits; Ausgaben durch das Monatslimit begrenzt |
| Supabase kostenloser Tarif, Cloudflare Pages | Konten vorhanden, keine Zusatzabos |
| Die App wird nur vom Branch `app-veroeffentlichen` veröffentlicht | Veröffentlichen geschieht mit Absicht; ein Hochladen auf `main` ändert die laufende App nie |
| Sammler als Container | Läuft ohne Änderung auf dem RepoCity-Server |
| Abrechnung, Kundenstand, Termine der Monatsprüfung und ausgehende Mails nur im CRM | ein Nummernkreis und ein Ort fürs Geld; der RadarAuditor bleibt das Werkzeug für Prüfungen und Angebote |
| Mails liest Claude Code ohne Werkzeuge | das Abo deckt es ab, und eine Mail kann Claude Code zu nichts bringen außer einer Antwort |
| Prognosen nur als Spanne | Keine Zusagen, die rechtlich binden |
