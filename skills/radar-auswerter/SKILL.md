---
name: radar-auswerter
description: Wandelt die Rohdaten und Prüfergebnisse eines RadarAuditor-Audits in Befunde, Lösungen, Texte und eine Prognose-Einordnung in Alltagssprache um. Wird vom Befehl /auswerten gelesen.
---

# Radar-Auswerter

## Eingabe je Auftrag (Ordner `work/analyse/<job-id>/`)

| Datei | Inhalt |
|---|---|
| `audit.json` | Kunde, Standorte, Wettbewerber, Suchbegriffe |
| `raw.json` | RawAudit: Website, Ladezeit, Google-Profil, Suchergebnisse, Verzeichnisse, KI-Antworten |
| `check_results.json` | Ergebnis jeder Prüfung mit Messwerten |
| `katalog.json` | Prüfkatalog mit Kundenfrage und Kundennutzen je Prüfung |
| `vorheriges_audit.json` | nur, wenn es ein früheres fertiges Audit gibt: das letzte davon |
| `rankings.json` | Positionen je Suchbegriff für Kunde und Wettbewerber, aus diesem und dem vorherigen Audit; Grundlage für den Verlauf (Prüfung j9) |

## Ausgabe

`findings.json`: Liste nach dem Modell `Finding`. Je nicht bestandener oder teilweise bestandener Prüfung genau ein Eintrag mit
- `severity` 1 bis 3 (3 = kostet heute Anfragen oder birgt Abmahnrisiko)
- `title`: ein Satz, den ein Dachdecker versteht
- `explanation`: was gemessen wurde, mit Zahl
- `customer_impact`: was es ihn kostet, mit Bezug auf Wettbewerber, wo vorhanden
- `fix`: die konkrete Maßnahme
- `fix_payload`: fertiger Text, Code (JSON-LD, Meta-Angaben) oder Profil-Eintrag, wenn die Maßnahme ein Ergebnis hat, das sich kopieren lässt

`texte.json`: ein JSON-Objekt nach dem Modell `ReportTexts` (`src/radarauditor/reporting/texts.py`), genau diese Felder, alle Texte an den Kunden mit "Sie":

| Feld | Pflicht | Inhalt | Wo es im Bericht steht |
|---|---|---|---|
| `introduction` | ja | Einleitung, zwei bis drei Sätze | Titelseite |
| `summary` | ja | genau drei Sätze: was gut ist, was fehlt, was es kostet | "So sichtbar sind Sie heute" |
| `forecast_note` | ja | Einordnung der Prognose als Spanne, ausdrücklich als Schätzung | "Was eine Optimierung bringen kann" |
| `subscription_reason` | ja | warum sich die laufende Betreuung für genau diesen Kunden lohnt | "Warum eine laufende Betreuung" |
| `offer_text` | ja | Angebotstext ohne Preise (die stehen im Angebot) | "Ihr Angebot" |
| `talk_text` | ja | etwa fünf Sätze, die der Betreiber dem Kunden 1 zu 1 sagen kann: 1) Stand heute mit Vergleich zu den Nachbarn auf der Karte, 2) größter Hebel mit gemessener Zahl, 3) zwei weitere Punkte, 4) Nutzen als geschätzte Spanne aus der Prognose, ausdrücklich als Schätzung | nur App |
| `before_after` | nur bei Monatsprüfungen (`audit.purpose` in `audit.json` ist `monthly`) mit `vorheriges_audit.json`, sonst `null` | was sich seit dem letzten Audit verändert hat, mit Zahlen | Monatsbericht, erster Abschnitt |
| `post_suggestions` | nein, Standard `[]` | zwei fertige Beiträge für das Google-Profil | nur App |
| `review_replies` | nein, Standard `[]` | je unbeantworteter Bewertung ein Objekt mit `review_excerpt` (Auszug der Bewertung) und `reply` (Antwortvorschlag) | nur App |

Kein Pflichtfeld darf leer sein. Fehlt `texte.json` oder ist sie ungültig, setzt der Bericht neutrale Standardsätze ein; das ist der Notfall, nicht der Normalfall.

```json
{
  "introduction": "…",
  "summary": "…",
  "forecast_note": "…",
  "subscription_reason": "…",
  "offer_text": "…",
  "talk_text": "…",
  "before_after": null,
  "post_suggestions": ["…", "…"],
  "review_replies": [{"review_excerpt": "…", "reply": "…"}]
}
```

## Bewertungen

Gilt für die Prüfgruppe Bewertungen (Gruppe g im Prüfkatalog) und für `review_replies`. Grundlage sind nur die Bewertungstexte in `raw.json`.

**Zusammenfassen, bevor Befunde geschrieben werden:**
1. Grundstimmung in einem Satz, mit Zahl der gelesenen Bewertungen und der Plattform, von der sie stammen.
2. Höchstens drei Stärken: die häufigsten lobenden Themen, je ein kurzes wörtliches Zitat.
3. Höchstens drei Schwachstellen: die häufigsten Kritikpunkte, je ein kurzes wörtliches Zitat.

Gibt es weniger Bewertungen oder Themen, weniger nennen statt auffüllen. Die Zusammenfassung ist kein eigenes Feld in `texte.json`: sie dient als Beleg in `explanation` der Bewertungs-Befunde und als Stoff für Punkt 3 von `talk_text`. Zitate kürzen, nie umformulieren; Namen von Bewertenden weglassen.

**Antwortvorschläge (`review_replies`) in fünf Schritten:**
1. Den Ärger direkt anerkennen.
2. Das konkrete Anliegen kurz benennen, ohne die Vorwürfe zu wiederholen.
3. Verantwortung oder Bedauern ruhig ausdrücken.
4. Den nächsten Schritt nennen, den der Betrieb wirklich anbietet (zum Beispiel Rückruf, Termin zur Nachbesserung).
5. Mit einem freundlichen Schluss enden, der das Gespräch offenhält.

Ruhig, nicht verteidigend, nicht spöttisch, nicht unterwürfig; keine Floskeln. Keine Erstattung, Kulanz, Rabatte oder Zusagen, die nicht aus `audit.json` oder vom Betreiber stammen; fehlt ein Angebot, bleibt Schritt 4 bei „melden Sie sich gern direkt bei uns“ mit dem Kontaktweg aus dem Google-Profil. Kurz: zwei bis vier Sätze, mit „Sie“, unterschrieben mit dem Betriebsnamen.

## Regeln

- Deutsch, "du" gegenüber dem Betreiber, "Sie" gegenüber dem Kunden in Berichtstexten.
- Jede Abkürzung ausschreiben. Keine Fachbegriffe ohne Erklärung im selben Satz.
- Keine Zusagen: Prognosen sind Spannen und heißen Schätzung. Versprochen wird Arbeit, nie eine Platzierung; kein Text kündigt einen bestimmten Platz bei Google, in Google Maps oder in KI-Assistenten an.
- Nichts erfinden: jede Zahl stammt aus `raw.json`, `check_results.json` oder `rankings.json`.
- Bewertungsfilter (nur Zufriedene zu Google leiten) nie empfehlen; Googles Richtlinie verbietet das.
- Leistung-mal-Ort-Seiten nur empfehlen, wo Nachfrage belegt ist.
- Verbotene Wörter: revolutionär, nahtlos, KI-gestützt, Game-Changer, ganzheitlich, Synergie.
