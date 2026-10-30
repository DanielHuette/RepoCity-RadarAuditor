# Betriebsanleitung RepoCity RadarAuditor

Englische Fassung: [BETRIEBSANLEITUNG.md](BETRIEBSANLEITUNG.md)

So arbeitest du mit RadarAuditor, von der Website-Adresse bis zum monatlichen Vorher-Nachher-Bericht. Jeder Schritt: was du tust, was du dann siehst, das Bild dazu. Zuerst das Bild vom Handy, darunter das vom PC. Die Bilder zeigen Beispieldaten (Dachdeckerei Muster GmbH in Münster); sie entstehen mit `uv run python scripts/screenshots.py` neu.

Vier Teile arbeiten zusammen:

| Teil | Wo | Was er tut |
|---|---|---|
| App | Handy, Tablet, PC unter app.radarauditor.speedofthespirit.dev | Website prüfen, Auswertung zeigen, Angebot erstellen, Optimierung freigeben |
| Sammler | Docker-Container auf dem RepoCity-Server | ermittelt den Betrieb aus der Website, holt die Daten aus dem Netz, vergibt die Punkte, lässt Claude Code auswerten, erstellt Bericht und Angebot als PDF (druckfertige Datei), lässt Claude Code die Mails lesen, die das CRM vorlegt |
| Claude Code | auf dem Server, über dein Claude-Abo | schreibt mit `/auswerten` die Befunde, Lösungen und Texte und liest eingehende Mails; der Sammler ruft es dafür selbst auf |
| CRM | crm.speedofthespirit.dev | Kunden, Versand jeder Mail, Aufträge, Abos, Rechnungen, Zahlungen, Mahnungen, Paket für den Steuerberater, Termine der Monatsprüfung |

## 1. Einmalige Einrichtung

1. **Richte die Dienste nach [SETUP.de.md](SETUP.de.md) ein.** Supabase, Cloudflare Pages, Google Cloud, die Datei `.env`, deine Firmendaten. Danach ist die App unter app.radarauditor.speedofthespirit.dev erreichbar.
2. **Melde dich in der App an.** Adresse im Browser öffnen, E-Mail und Passwort deines Betreiber-Kontos eingeben, **Anmelden** tippen. Du landest im Reiter **Prüfen**. Die Anmeldung bleibt auf dem Gerät gespeichert.

   Es gibt zwei Rollen. Der **Administrator** darf alles: Prüfungen starten, erkannte Angaben korrigieren, Angebote erstellen, Optimierungen freigeben. Jedes andere Konto darf **nur lesen**: Oben steht dann „Nur Lesen“, Formulare und Aktionsknöpfe sind ausgeblendet, und die Datenbank verweigert jede Änderung auch dann, wenn jemand die App umgeht. Zugangsdaten der Kunden (WordPress) sehen nur Administratoren. Wer Administrator ist, steht in der Tabelle `public.app_admins`.

   ![Handy: Anmeldung mit E-Mail und Passwort](bilder/anmelden-handy.png)
   ![PC: Anmeldung](bilder/anmelden-desktop.png)

3. **Installier die App auf Handy und Tablet.** Oben rechts das Symbol **Einstellungen** tippen. Unter „App installieren“ steht, was zu tun ist: auf dem iPhone oder iPad in Safari auf **Teilen** tippen, dann **Zum Home-Bildschirm**; auf Android im Browser-Menü **App installieren** oder **Zum Startbildschirm hinzufügen**. Bietet der Browser die Installation direkt an, erscheint stattdessen der Knopf **Auf diesem Gerät installieren**. Danach liegt RadarAuditor als eigenes Symbol auf dem Startbildschirm.

   ![Handy: Hinweis zum Installieren in den Einstellungen](bilder/einstellungen-installieren-handy.png)
   ![PC: Einstellungen mit Datenquellen und Installation](bilder/einstellungen-desktop.png)

4. **Start den Sammler auf dem Server.** Wie in [SERVER.de.md](SERVER.de.md) beschrieben im Projektordner `docker compose up -d` ausführen; danach startet der Sammler mit dem Server von selbst. Er fragt alle 15 Sekunden nach neuen Aufträgen. Ob er läuft, zeigt `docker compose ps` (Dienst `worker`, Zustand „Up“).
5. **Melde Claude Code einmal an.** Den Projektordner in Claude Code öffnen und mit deinem Claude-Abo anmelden. Der Sammler lässt Claude Code jede Auswertung selbst schreiben; solange Claude Code nicht antwortet, nimmt er keine neue Prüfung an. `/auswerten` von Hand brauchst du nur, wenn eine Prüfung bei „Wertet aus“ stehen bleibt.

## 2. Ein Betrieb, von der Website-Adresse bis zum Angebot

1. **Gib die Website-Adresse ein.** Reiter **Prüfen**: nur die Adresse eintragen (zum Beispiel `elektro-beispiel.de`), **Prüfen** tippen. Name, Ort, Branche und Suchbegriffe trägst du nicht ein; die ermittelt der Server beim Sammeln selbst (`src/radarauditor/collectors/identify.py`). Je Angabe gilt die erste Quelle, die sie nennt: JSON-LD auf der Startseite (unsichtbare Firmenangaben für Suchmaschinen), Impressum, Fußbereich der Website, Google-Profil.

   ![Handy: Reiter Prüfen mit Website-Adresse](bilder/pruefen-handy.png)
   ![PC: Reiter Prüfen](bilder/pruefen-desktop.png)

2. **Wähl die Prüfungen aus.** Der Katalog `config/pruefkatalog.yaml` hat 108 Prüfungen in 14 Bereichen, a bis n. Oben steht „Alle Prüfungen“ mit der Zahl der eingeschalteten. Jeder Bereich hat ein Kästchen für alle seine Prüfungen und klappt auf Tippen auf; dann steht jede Prüfung mit einem Satz, warum sie zählt. Die Auswahl bleibt auf dem Gerät gespeichert und wird mit der Prüfung abgelegt (`audits.disabled_checks`); nur sie gilt für diese Prüfung. **Prüfung starten** tippen. Steht dort „Prüfungen gerade nicht möglich“, kann Claude Code auf dem Server gerade nicht auswerten; der Grund steht dabei.

   ![Handy: Auswahl der Prüfungen mit aufgeklapptem Bereich](bilder/pruefen-auswahl-handy.png)
   ![PC: Auswahl der Prüfungen](bilder/pruefen-auswahl-desktop.png)

3. **Verfolg den Fortschritt.** Die App zeigt die Schritte Wartet, Sammelt, Bewertet, Wertet aus, Fertig. Der Sammler misst die Website, die Ladezeit, das Google-Profil, die Suchergebnisse, die Verzeichnisse und die KI-Assistenten und vergibt die Punkte; im Schritt „Wertet aus“ schreibt Claude Code die Befunde, Lösungen und Texte. Das dauert je nach Größe der Website einige Minuten bis eine halbe Stunde. Findet der Server keinen Ort, bricht die Prüfung mit „Ort nicht gefunden“ ab; dann im Reiter **Auswertung** die Prüfung wählen und den Ort über **Korrigieren** eintragen. Bei **Fertig** **Auswertung ansehen** tippen.

   ![Handy: Fortschritt einer Prüfung](bilder/pruefen-fortschritt-handy.png)
   ![PC: Fortschritt einer Prüfung](bilder/pruefen-fortschritt-desktop.png)

4. **Prüf, was erkannt wurde.** Oben in der Auswertung steht **Erkannt** mit Betrieb, Ort und Branche. Stimmt etwas nicht, **Korrigieren** tippen, die Angaben richtigstellen und **Neu prüfen** tippen: Das startet eine neue Prüfung derselben Website mit denselben Prüfungen.

   ![Handy: Auswertung mit Erkannt und Gesprächstext](bilder/auswertung-handy.png)
   ![PC: Auswertung mit Erkannt und Gesprächstext](bilder/auswertung-desktop.png)

5. **Sag dem Kunden, was Sache ist.** **Das sagen Sie dem Kunden** enthält etwa fünf Sätze, die Claude Code für das Gespräch geschrieben hat (`talk_text` in `texte.json`); du kannst sie wörtlich sagen. Darunter **Die größten Hebel**: die drei Prüfungen mit dem meisten offenen Potenzial, mit den Punkten, die sie bringen; Tippen springt zur Prüfung. Weiter unten stehen **Bericht öffnen (PDF)**, das offene Potenzial, die Gesamtpunkte mit dem Radar je Bereich und die Ampel je Bereich (grün ab 75, gelb ab 50, rot darunter). Die zusätzlichen Anfragen pro Monat bei einer offenen Prüfung sind eine Schätzung, keine Zusage; sag das so.

   ![Handy: Die größten Hebel](bilder/auswertung-hebel-handy.png)
   ![PC: Die größten Hebel](bilder/auswertung-hebel-desktop.png)

6. **Geh die Prüfungen durch.** **Alle Prüfungen** lässt sich nach Potenzial, Bereich oder Dringlichkeit sortieren; der Schalter „Nur offenes Potenzial“ blendet Bestandenes aus. Jede Prüfung klappt auf Tippen auf: warum sie zählt, was gemessen wurde, was es den Kunden kostet und die Lösung, fertige Texte und Code mit **Kopieren**. Bei g4 („Du antwortest auf Bewertungen“) stehen die neuesten Bewertungen ohne Antwort des Inhabers als Beispiele. Darunter folgen die Platzierungen im Vergleich mit den Wettbewerbern, das Kartenraster und der Verlauf.

   ![Handy: Aufgeklappte Prüfung g4 mit Bewertungen ohne Antwort](bilder/auswertung-pruefung-handy.png)
   ![PC: Aufgeklappte Prüfung g4](bilder/auswertung-pruefung-desktop.png)
   ![Handy: Platzierungen im Vergleich mit den Wettbewerbern](bilder/auswertung-wettbewerber-handy.png)
   ![PC: Platzierungen und Kartenraster](bilder/auswertung-wettbewerber-desktop.png)

7. **Erstell das Angebot.** Reiter **Angebot**, unter „Letzte Prüfungen“ die Prüfung wählen. Unter **Positionen** steht ein Vorschlag aus den offenen Punkten der Prüfung: die Produkte aus dem CRM, deren Bereiche durchgefallene oder teilweise bestandene Prüfungen haben, mit den Preisen aus dem CRM. Positionen abwählen oder Mengen ändern, dann **Angebot erstellen**. Ist der Betrieb noch mit keinem Kunden im CRM verbunden, sagt die App das; dann über **Prüfen** neu prüfen.

   ![Handy: Reiter Angebot mit den letzten Prüfungen](bilder/angebot-handy.png)
   ![PC: Reiter Angebot](bilder/angebot-desktop.png)

8. **Schick das Angebot ab.** **Angebot abschicken** tippen; die App zeigt die E-Mail-Adresse des Kunden aus dem CRM. Fehlt sie dort, trägst du sie ein, und sie wird im CRM gespeichert. **Jetzt abschicken**: Gibt es den Prüfbericht mit Angebot noch nicht als PDF, erstellt der Sammler ihn zuerst (lass die Seite so lange offen); dann trägt das CRM das Angebot als gesendet ein und verschickt es per Mail mit dem PDF im Anhang. Mit **PDF herunterladen** siehst du dir das PDF vorher an.
9. **Trag die Entscheidung des Kunden ein.** **Angenommen** legt im CRM den Auftrag an, **Abgelehnt** schließt das Angebot ab. Sagt der Kunde per Mail zu, musst du nichts tippen: Claude Code liest die Antwort auf dem Server, und das CRM trägt die Annahme ein. Angebote und Aufträge liegen in der Akte des Kunden im CRM; **Kundenakte im CRM öffnen** in der Auswertung oder im Angebot öffnet sie.

## 3. Vom Angebot zum Kunden

Ob ein Betrieb Kunde ist, steht im CRM: Ein angenommenes Angebot wird dort zum Auftrag, jede monatliche Position zu einem Abo.


1. **Hol dir den Zugang beim Kunden.** Schick ihm die [Anleitung für den Kunden](#anhang-anleitung-für-den-kunden) am Ende dieses Dokuments. Sie dauert für ihn etwa fünf Minuten.
2. **Trag den Zugang ein.** Die Angaben gehören in die Tabelle `public.customer_access` (Supabase **Table Editor**): `google_business_profile_granted` auf `true`, sobald die Einladung angenommen ist; `search_console_property` so, wie die Property in der Search Console heißt (zum Beispiel `sc-domain:dachdeckerei-muster.de`); `google_location_name` (`locations/` und die Unternehmensprofil-ID aus den erweiterten Einstellungen des Profils); falls der Kunde Analytics nutzt, `analytics_property_id` (nur Ziffern); bei WordPress `wordpress_url`, `wordpress_user` und das Anwendungspasswort in `wordpress_app_password_encrypted`; `granted_at` auf den heutigen Tag.
3. **Start das Audit mit Zugang.** Im Projektordner `uv run radarauditor audit run <Kunden-Kennung> --with-access`; die Monatsprüfung startet es von selbst mit Zugang, sobald der Profil-Zugang eingetragen ist. Zusätzlich holt der Sammler mit deiner einmal erteilten Google-Erlaubnis ([SETUP Abschnitt 9](SETUP.de.md#9-google-zugriff-für-audits-mit-kundenzugang)) die Wirkungszahlen: Aufrufe, Anrufe, Routenanfragen und Website-Klicks der letzten 28 Tage gegen die 28 Tage davor, Suchanfragen mit Klicks aus der Search Console, Besuche und Anfragen aus Analytics und die Antworten auf Bewertungen. Der Bericht zeigt sie im Abschnitt „Was Ihr Google-Profil heute bringt“; der Bereich „Wirkung mit Kundenzugang“ zählt nicht in den Gesamtwert.

## 4. Erstoptimierung

1. **Start die Optimierung.** Nach **Angenommen** steht im Reiter **Angebot** unter dem Angebot der Abschnitt **Optimierung** mit der Nummer des Auftrags. **Optimierung starten** tippen; der Sammler lässt Claude Code mit `/optimieren` die Änderungen als Entwürfe vorbereiten.
2. **Gib die Entwürfe frei.** Unter **Zur Freigabe** jede Änderung mit **Änderung ansehen** prüfen (vorher und nachher), dann **Freigeben** oder **Ablehnen**. Freigegebene Änderungen wendet der Sammler an („Freigegeben, wird angewendet“).
3. **Setz den Rest von Hand um.** Unter **Von Hand umsetzen** stehen Änderungen, die der Sammler nicht selbst vornehmen kann, mit **Kopieren**; einfügen im WordPress des Kunden, im Google-Profil oder im Website-Baukasten an der genannten Stelle. Code für den Kopfbereich der Website gehört in das Feld für zusätzlichen Code im Kopfbereich (in WordPress etwa über ein Plugin für Kopf- und Fußzeilen-Code). **Protokoll** zeigt, was angewendet wurde.
4. **Lass die nächste Monatsprüfung die Wirkung messen.** Sie misst neu, und erledigte Punkte stehen dann auf „bestanden“. Ist die letzte Änderung des Auftrags erledigt, schließt das CRM den Auftrag ab und verschickt die Rechnung. Willst du früher wissen, ob es gewirkt hat, prüf die Website im Reiter **Prüfen** noch einmal.

## 5. Monatsprüfung

1. **Lös die Monatsprüfung aus der Meldung aus.** Für jedes Abo meldet das CRM die Prüfung einmal im Monat fällig; im Cockpit erscheint „Monatsprüfung fällig“ mit dem Knopf **Prüfung auslösen**, der auch auf dem Sperrbildschirm geht. Der Sammler prüft den Betrieb dann neu mit dem Zweck `monthly`, mit Zugang, wenn der Profil-Zugang eingetragen ist. Die Prüfung steht im Reiter **Auswertung** unter **Letzte Prüfungen**.
2. **Lies die Auswertung.** Claude Code wertet wie beim ersten Mal ohne Bedienung aus. Für eine Monatsprüfung mit einer früheren Prüfung schreibt es zusätzlich, was sich seit dem letzten Mal verändert hat, und der Sammler erstellt einen Monatsbericht im Vorher-Nachher-Stil statt des Verkaufsberichts. Dazu schreibt Claude Code zwei fertige Beiträge fürs Google-Profil und Antwortvorschläge zu Bewertungen. Sie stehen in der Auswertung unter **Beiträge für das Google-Profil** und **Antworten auf Bewertungen**, jeder mit **Kopieren**-Knopf; von dort fügst du sie im Google-Profil ein.

   ![Handy: Beiträge und Bewertungsantworten mit Kopieren-Knopf](bilder/auswertung-texte-handy.png)
   ![PC: Beiträge und Bewertungsantworten](bilder/auswertung-texte-desktop.png)

3. **Schau dir den Verlauf an.** Unten in der Auswertung zeigt **Verlauf** die Gesamtpunkte aller Prüfungen des Kunden, hier von 41 auf 63. Über **Prüfung wechseln** öffnest du eine frühere Prüfung.

   ![Handy: Verlauf der Gesamtpunkte](bilder/auswertung-verlauf-handy.png)
   ![PC: Verlauf der Gesamtpunkte](bilder/auswertung-verlauf-desktop.png)

4. **Schick den Bericht und den Leistungsnachweis.** Monatsbericht über **Bericht öffnen (PDF)** speichern und dem Kunden schicken. Den Leistungsnachweis erzeugt der Sammler über die Befehlszeile: `uv run radarauditor document build proof_of_service <Kunden-Kennung> <daten.json>`. Welche Angaben die Datei braucht, steht je Dokumentart am Anfang von `src/radarauditor/documents/builder.py`.

## 6. Rechnungen, Zahlungen und Steuerberater

Der RadarAuditor schreibt keine Rechnungen. Rechnungen mit einem Nummernkreis, Zahlungen, Mahnungen und das Paket für den Steuerberater liegen im CRM.

## 7. Wenn etwas hakt

| Anzeichen | Ursache | Abhilfe |
|---|---|---|
| Prüfung bleibt bei „Wartet“ oder „Sammelt“ | Sammler läuft nicht | auf dem Server im Projektordner `docker compose ps`, steht `worker` nicht auf „Up“: `docker compose up -d` |
| Prüfung bleibt bei „Wertet aus“, darunter ein Hinweis mit Grund | Claude Code hat nicht geantwortet (nicht installiert, nicht angemeldet, zu langsam oder ungültige Dateien); der Sammler versucht es nach 10 Min., 30 Min., 2 Std. und dann alle 6 Std. erneut | Claude Code anmelden; wer nicht warten will: Claude Code im Projektordner öffnen, `/auswerten` eingeben |
| Prüfung steht auf „Fehler“ | ein Dienst war dreimal nicht erreichbar, oder der Server fand keinen Ort | Fehlertext im Fortschritt lesen; im Reiter **Prüfen** neu prüfen, bei fehlendem Ort in der Auswertung **Korrigieren** |
| „Google-Suche wird nachgeholt“ steht bei der Prüfung | Google hat die Suche mit einer Sperrseite (Captcha, „Ich bin kein Roboter“) beantwortet | nichts tun: der Server sucht nach 15 Min., 1 Std., 4 Std. und 12 Std. noch einmal und bewertet die Prüfung dann neu. Bleibt Google gesperrt, fehlen nur diese Platzierungen; dauerhaft hilft der Bezahldienst DataForSEO ([SETUP.de.md](SETUP.de.md), Abschnitt 7) |
| App meldet „Keine Verbindung zum Server“, `uv run radarauditor health` meldet einen Fehler | Supabase hat das Projekt im kostenlosen Tarif nach einer Woche ohne Zugriff pausiert | https://supabase.com/dashboard öffnen, Projekt wählen, **Restore project**; nach wenigen Minuten geht alles wieder. Solange der Sammler läuft, greift er regelmäßig zu und das Projekt pausiert nicht |
| App zeigt „App nicht eingerichtet“ | beim Bau fehlte eine der drei Variablen; die Seite nennt sie | `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` und `VITE_CRM_ORIGIN` in `.github/workflows/deploy-app.yml` prüfen, [SETUP.de.md](SETUP.de.md), Abschnitt 2 |
| Anmeldung: „E-Mail oder Passwort stimmt nicht“ | Tippfehler oder anderes Konto | in Supabase unter **Authentication** → **Users** prüfen; dort lässt sich ein neues Passwort setzen |
| Angebot bleibt bei „PDF wird auf dem Server erstellt …“ | Sammler läuft nicht, oder Firmendaten fehlen | Sammler prüfen wie oben; Zeile `supplier` nach [SETUP.de.md](SETUP.de.md), Abschnitt 5 anlegen; `docker compose logs worker` zeigt den Grund |
| PDF sieht schlicht aus, andere Schrift als die App | im Container ist die Schrift Archivo nicht installiert; der Bericht nimmt dann DejaVu Sans | stört den Inhalt nicht ([SETUP.de.md](SETUP.de.md), Abschnitt 6) |
| PDF zeigt Kästchen statt Buchstaben | der Sammler läuft außerhalb von Docker auf einem Rechner ohne passende Schrift | den Sammler im Docker-Container laufen lassen; dort ist DejaVu Sans installiert |
| **Angebot abschicken** wartet auf das PDF | Sammler läuft nicht, oder das PDF ist gescheitert | Sammler prüfen wie oben; nach einem gescheiterten PDF noch einmal **Jetzt abschicken** tippen, das fordert ein neues an |
| Beim Serverstand steht „Mails lesen steht“ mit Grund | Mails lesen ist abgeschaltet (`MAIL_TRIAGE_BATCH=0`), Claude Code ist nicht angemeldet, das Abo-Kontingent ist aufgebraucht, keine Mail der letzten Ladung ließ sich lesen, oder das CRM gibt keine Mails heraus; der Server versucht es nach der nächsten Prüfung von Claude Code wieder, spätestens nach 10 Minuten | Claude Code auf dem Server anmelden; `MAIL_TRIAGE_BATCH` in der `.env` prüfen; `docker compose logs worker` zeigt die Antwort des CRM |
| `/auswerten` meldet einen Fehler bei `jobs push` | `findings.json` passt nicht zum Audit | Claude Code die Meldung zeigen und die Datei korrigieren lassen; der Ordner bleibt unter `work/analyse/` liegen, bis er gültig ist |

## Anhang: Anleitung für den Kunden

Diesen Abschnitt kannst du dem Kunden so schicken. Ersetze `<deine Google-Adresse>` durch die Adresse deines Google-Kontos.

---

**Zugang für Ihre laufende Betreuung**

Damit wir Ihre Einträge pflegen und die Wirkung messen können, brauchen wir Zugang zu Ihrem Google-Unternehmensprofil, zur Google Search Console und, falls Sie es nutzen, zu Google Analytics. Sie behalten die volle Kontrolle und können den Zugang jederzeit wieder entfernen. Ihr Passwort brauchen wir nicht.

**1. Google-Unternehmensprofil (etwa 2 Minuten)**

1. Melden Sie sich mit dem Google-Konto an, mit dem Sie Ihr Unternehmensprofil verwalten.
2. Suchen Sie bei Google nach dem Namen Ihres Betriebs. Über dem Suchergebnis erscheint Ihr Profil mit Bearbeitungsknöpfen.
3. Tippen Sie auf das Menü mit den drei Punkten und dann auf **Unternehmensprofil-Einstellungen**.
4. Wählen Sie **Personen und Zugriff** und dann **Hinzufügen** (Symbol mit Person und Plus).
5. Tragen Sie `<deine Google-Adresse>` ein, wählen Sie als Rolle **Manager** und tippen Sie auf **Einladen**.

**2. Google Search Console (etwa 2 Minuten, nur wenn Sie sie nutzen)**

1. Öffnen Sie https://search.google.com/search-console und wählen Sie oben links Ihre Website.
2. Links unten **Einstellungen**, dann **Nutzer und Berechtigungen**.
3. **Nutzer hinzufügen**, `<deine Google-Adresse>` eintragen, Berechtigung **Eingeschränkt**, **Hinzufügen**.

**3. Google Analytics (etwa 1 Minute, nur wenn Sie es nutzen)**

1. Öffnen Sie https://analytics.google.com und wählen Sie oben Ihre Website.
2. Links unten **Verwaltung** (Zahnrad), dann bei **Property** **Property-Zugriffsverwaltung**.
3. Oben rechts **+**, **Nutzer hinzufügen**, `<deine Google-Adresse>` eintragen, Rolle **Betrachter**, **Hinzufügen**.
4. Schicken Sie uns die **Property-ID** (unter **Verwaltung** → **Property-Details**, nur Ziffern).

**4. WordPress (nur wenn Ihre Website mit WordPress läuft)**

1. Melden Sie sich in WordPress an, links **Benutzer** → **Neuen Benutzer hinzufügen**.
2. Benutzername `radarauditor`, E-Mail `<deine Google-Adresse>`, Rolle **Redakteur**, **Neuen Benutzer hinzufügen**.

Sie erhalten keine weiteren Fragen von uns; wir melden uns, sobald alles eingerichtet ist.

---
