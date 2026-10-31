# Kontaktwege der Kundensuche

Englische Fassung: [KONTAKTWEGE.md](KONTAKTWEGE.md)

Diese Seite begründet, welche Kontaktwege die Kundensuche je Betrieb anzeigt und welche nicht. Sie gibt den Stand der Quellen vom 31. Oktober 2026 wieder und ersetzt keine Rechtsberatung; was die Quellen nicht eindeutig beantworten, steht unten als unsicher.

## Ergebnis
**Context:** Erstansprache von Betrieben, die die Kundensuche gefunden hat und die noch nie Kontakt zu RepoCity hatten. | **Input:** § 7 Gesetz gegen den unlauteren Wettbewerb (UWG), Merkblätter der Industrie- und Handelskammern (IHK), Urteile des Bundesgerichtshofs (BGH), Orientierungshilfe der Datenschutzkonferenz (DSK). | **Output:** Einstufung je Kontaktweg, umgesetzt in `config/kundensuche.yaml`.

| Kontaktweg | Einstufung in der App | Was gilt | Belegt durch | Sicherheit |
|---|---|---|---|---|
| E-Mail | nicht erlaubt, kein Knopf | Werbung per E-Mail nur mit vorheriger ausdrücklicher Einwilligung des Empfängers, auch wenn der Empfänger ein Betrieb ist; schon eine einzige unerwünschte Werbemail ist unzulässig; eine Adresse auf Webseite oder Visitenkarte ist keine Einwilligung | § 7 Abs. 2 Nr. 2 UWG; IHK Nord Westfalen | belegt |
| Anruf | „nur mit Anlass“, kein Wählknopf | Bei Betrieben reicht eine mutmaßliche Einwilligung, aber nur bei konkreten Umständen, aus denen ein sachliches Interesse am Anruf folgt; dass die Leistung dem Betrieb nützen würde, reicht nicht; die Einwilligung muss die Art der Werbung (Anruf) decken, nicht nur ihren Inhalt | § 7 Abs. 2 Nr. 1 UWG; IHK Nord Westfalen; BGH I ZR 88/05 und I ZR 191/03 | Grundsatz belegt; ob ein lückenhaftes Google-Profil ein solcher Umstand ist, ist unsicher (siehe unten) |
| Brief | erlaubt | Werbebriefe sind grundsätzlich zulässig; ein Widerspruch des Empfängers und ein Aufkleber gegen Werbung am Briefkasten sind zu beachten; der Brief darf keine persönliche Empfehlung vortäuschen | § 7 Abs. 1 UWG; IHK Nord Westfalen | belegt |
| Persönlicher Besuch | erlaubt | Vertreterbesuche sind nach ständiger Rechtsprechung des BGH grundsätzlich zulässig; unzulässig, sobald erkennbar ist, dass der Besuch unerwünscht ist (etwa ein Schild), bei verdecktem Werbezweck und bei Drängen | IHK Frankfurt am Main | für Wohnungen belegt; für Geschäftsräume nicht ausdrücklich, siehe unten |

## Datenschutz
**Context:** Die Kundensuche verarbeitet Namen, Anschrift und Telefonnummer von Betrieben; bei Einzelunternehmern sind das Daten einer Person. | **Input:** Datenschutz-Grundverordnung (DSGVO), DSK-Orientierungshilfe Direktwerbung (Februar 2022). | **Output:** Regeln für Speicherung und ersten Brief.

| Frage | Ergebnis | Belegt durch | Sicherheit |
|---|---|---|---|
| Rechtsgrundlage für Werbung ohne Einwilligung | berechtigtes Interesse nach Art. 6 Abs. 1 Buchstabe f DSGVO, gestützt auf Erwägungsgrund 47 | DSK, Abschnitt 1.2 | belegt |
| Verhältnis zum UWG | ist ein Kontaktweg nach dem UWG unzulässig, fehlt schon das berechtigte Interesse; deshalb bietet die App keine Werbe-Mail und keinen Anruf ohne Anlass an | DSK, Abschnitt 1.4 | belegt |
| Information im ersten Brief | stammen die Daten nicht vom Betrieb selbst, ist er spätestens bei der ersten Werbung zu informieren (Herkunft der Daten, Zweck, Widerspruchsrecht), klar getrennt von der Werbung | Art. 14 DSGVO; DSK, Abschnitt 2.2 | belegt |
| Widerspruch | ist sofort zu beachten; eine Sperrliste ist zulässig | Art. 21 DSGVO; DSK, Abschnitte 5.1 und 5.2 | belegt |
| Daten aus dem Google-Profil | die DSK hält Daten aus einem Impressum für Werbung meist für nicht verwendbar, weil das Impressum Pflicht ist und nicht freiwillig; ein Google-Profil legt der Betrieb freiwillig an, um gefunden zu werden | DSK, Abschnitt 4.2 | unsicher: ob die Überlegung der DSK für ein Google-Profil gilt, sagt keine der gelesenen Quellen |

## Speicherung der Suchergebnisse und Übernahme ins CRM
**Context:** Was von den Daten der Places API wie lange bleiben darf. RepoCity hat eine Rechnungsanschrift in Deutschland, deshalb gelten die EEA-Fassungen (Europäischer Wirtschaftsraum) der Google-Bedingungen. | **Input:** Google Maps Platform EEA Service Specific Terms (Stand 10. Juni 2026), Abschnitte A.3 und 15; Places API EEA Permitted Uses (Stand 4. Juni 2025). | **Output:** Speicherdauer (`public.prospect_cache_hours()`), Ablauf von „Ins CRM als Interessent“.

| Inhalt | Was Google erlaubt | Was RepoCity tut | Sicherheit |
|---|---|---|---|
| Kennung des Orts (`place_id`) | darf gespeichert werden (A.3 „Google ID Caching“) | bleibt dauerhaft, auch an der Verknüpfung zum CRM und an der Sperrliste der Widersprüche | belegt |
| Koordinaten | höchstens 30 Tage (15.4) | nicht gespeichert | belegt |
| Name, Anschrift, Telefon, Webseite, Bewertungszahl, Fotos | nur für die erlaubten Zwecke (15.2); erlaubt ist unter anderem, Places-Inhalte zu Kunden und Verkaufschancen eines Vertriebs anzuzeigen und zu verwalten (Permitted Use 3); eine Erlaubnis, diese Inhalte zwischenzuspeichern, enthalten die Bedingungen nicht, sie verweisen auf das Verbot des Speicherns im Vertrag | 24 Stunden in der Liste (`public.prospect_cache_hours()`), so lange, wie Daniel die Liste bearbeitet; danach löscht die Datenbank alle diese Felder selbst (stündlicher Zeitplan `radar-prospect-purge` in pg_cron, unabhängig vom laufenden Server); es bleiben nur Kennung, CRM-Verknüpfung und Widerspruchsmarke | Anzeigen und Verwalten belegt; dass 24 Stunden Zwischenspeicher gedeckt sind, ist nicht belegt; kürzer geht es nur, wenn die App die Liste gar nicht mehr speichert |
| Daten im CRM | dauerhaftes Übernehmen der Places-Felder ins CRM ist durch keine der gelesenen Stellen erlaubt | „Ins CRM als Interessent“ übernimmt keine Places-Felder: Daniel trägt Name, Straße, Postleitzahl, Ort und Telefon selbst ein, abgeschrieben aus dem öffentlichen Google-Profil (Link im Formular); gespeichert wird nur, was er einträgt, und die `place_id` an der Verknüpfung; der Eintrag in der Kundenakte nennt keine Gründe aus der Liste | belegt für die `place_id`; was Daniel aus der öffentlichen Quelle abschreibt, ist kein Ergebnis der Places API |

## Kosten der Kundensuche
**Context:** Jede Seite der Textsuche ist eine bezahlte Abfrage. | **Input:** Preisliste der Google Maps Platform (Stand 28. Oktober 2026). | **Output:** Buchung über `SourceGate.record_cost`, Grenzen in den Einstellungen.

| Abfrage | Preisstufe | Preis je 1.000 Abfragen, erste Mengenstufe | Gebucht auf |
|---|---|---|---|
| Kundensuche, je Seite (`search_prospects`) | Text Search Enterprise (Webseite, Telefon, Öffnungszeiten, Bewertungszahl) | 35 US-Dollar | Quelle `prospect_search`: eigenes Tageslimit (Vorgabe 15 Seiten) und Monatslimit (Vorgabe 10 Euro) |
| Audit: Erkennung (`search_businesses`), Liste mit Bewertungszahlen (`listed_businesses`) | Text Search Enterprise | 35 US-Dollar | Quelle `places`, Monatslimit Vorgabe 25 Euro |
| Audit: Wettbewerber (`top_competitors`) | Text Search Enterprise + Atmosphere (Bewertungen) | 40 US-Dollar | Quelle `places` |
| Audit: Profil (`place_details`) | Place Details Enterprise + Atmosphere | 25 US-Dollar | Quelle `places` |
| Audit: Kennung und Rangfolge (`find_place`, `ranked_place_ids`) | Text Search Essentials, nur Kennungen | kostenlos | Quelle `places` (nur Tageslimit) |

Der Dollarbetrag wird mit `GOOGLE_EUR_PER_USD` (Vorgabe 1) in Euro umgerechnet. Die freien Abfragen je Monat und Preisstufe zieht der Server nicht ab; die gebuchte Summe liegt deshalb nie unter der Rechnung von Google.

## Was unsicher bleibt
**Context:** Punkte, die die gelesenen Quellen nicht entscheiden. | **Input:** Abschnitte oben. | **Output:** offene Fragen mit der vorsichtigen Einstellung der App.

| Frage | Einstellung der App bis zur Klärung |
|---|---|
| Ist ein lückenhaftes Google-Profil ein konkreter Umstand für eine mutmaßliche Einwilligung zum Anruf? Der BGH hielt einen kostenlosen Verzeichniseintrag nicht für ausreichend, um einen Anruf für einen bezahlten Eintrag zu rechtfertigen (I ZR 191/03). | Anruf als „nur mit Anlass“, kein Wählknopf, im CRM-Vorgang „kein Anruf ohne konkreten Anlass“ |
| Gilt die Rechtsprechung zu Vertreterbesuchen auch für unangemeldete Besuche in Geschäftsräumen während der Öffnungszeiten? Die gelesene Quelle behandelt den Besuch an der Wohnungstür. | Besuch als erlaubt mit dem Hinweis, ein Nein oder ein Schild sofort zu beachten |
| Die Urteile des BGH sind nach einer Zusammenfassung (omsels.info) zitiert; der Volltext von I ZR 88/05 ließ sich nicht abrufen. | keine Folge für die App; vor einer Verwendung in Texten an Kunden den Volltext lesen |
| Das Merkblatt der IHK Nord Westfalen nennt für E-Mail-Werbung § 7 Abs. 2 Nr. 3 UWG, die Fassung auf gesetze-im-internet.de führt sie unter Nr. 2. Das Merkblatt nennt die frühere Nummerierung; am Inhalt ändert das nichts. | keine |

## Was die App daraus macht
**Context:** Umsetzung. | **Input:** Ergebnis oben. | **Output:** Verhalten von App, Datenbank und CRM.

| Stelle | Verhalten |
|---|---|
| `config/kundensuche.yaml`, `contact_ways` | einzige Quelle für Einstufung und Hinweistext je Kontaktweg; der Server speichert sie mit jedem Treffer |
| Seite „Kunden“ der App | zeigt je Treffer Brief, Besuch (nur mit Straße) und Anruf (nur mit Nummer) mit Einstufung und Hinweis; E-Mail erscheint nie; es gibt keinen Mail- und keinen Wählknopf |
| `public.add_prospect_to_crm` | legt den CRM-Kunden mit Daniels Angaben und den Vorgang mit dem nächsten Schritt „Erstkontakt per Brief oder Besuch; keine Werbe-Mail, kein Anruf ohne konkreten Anlass.“ an; lehnt vor jedem Schreiben ab, wenn der Betrieb Werbung widersprochen hat, gefunden über Name und Ort, Telefonnummer oder `place_id`; geprüft wird mit Daniels Angaben und mit den gespeicherten Angaben des Treffers |
| `public.advertising_objections` | Sperrliste: jeder Widerspruch im CRM (`no_follow_ups`) landet dort mit Name, Ort, Telefonnummer und bekannten `place_id`s und bleibt, auch wenn der CRM-Kunde gelöscht wird; Namen und Orte werden ohne Rechtsform, Klammerzusätze und Sonderzeichen verglichen, und es reicht, wenn der eine Name mit dem anderen beginnt (ab 6 Zeichen; „Bäckerei Korn“ trifft „Bäckerei Korn Inh. Peter Müller“, „Münster (Westf.)“ ist „Münster“); ein Eintrag ohne Ort gilt überall; im Zweifel wird abgelehnt; Treffer darauf zeigt die Liste als „Hat Werbung widersprochen – nicht ansprechen.“ ohne Kontaktweg und ohne Knopf |
| `public.purge_prospect_searches` | löscht die Places-Felder nach 24 Stunden; läuft stündlich zur Minute 53 als pg_cron-Zeitplan `radar-prospect-purge` und zusätzlich vor jeder Suche des Servers |

## Quellen
**Context:** Abgerufen am 31. Oktober 2026. | **Input:** Netz. | **Output:** Belege der Tabellen oben.

| Quelle | Adresse |
|---|---|
| § 7 UWG, Fassung auf gesetze-im-internet.de | https://www.gesetze-im-internet.de/uwg_2004/__7.html |
| IHK Nord Westfalen, Werbung per Telefon, Brief oder E-Mail | https://www.ihk.de/nordwestfalen/recht/rechtsthemen/wettbewerbsrecht/werbung-per-telefon-telefax-oder-e-mail-3614212 |
| IHK Frankfurt am Main, Vertreterbesuche | https://www.frankfurt-main.ihk.de/recht/uebersicht-alle-rechtsthemen/wettbewerbsrecht/unlauterer-wettbewerb/unzumutbare-belaestigung/vertreterbesuche-5196346 |
| Zusammenfassung der BGH-Urteile I ZR 88/05 (20.09.2007), I ZR 191/03 (16.11.2006), I ZR 87/02 (05.02.2004), I ZR 27/08 (11.03.2010) zur mutmaßlichen Einwilligung | https://www.omsels.info/die-verbote-oder-was-darf-ich-nicht/7-uwg-belaestigende-werbung/2-7-abs-2-3-uwg/emutmassliche-einwilligung |
| DSK, Orientierungshilfe zur Verarbeitung personenbezogener Daten für Zwecke der Direktwerbung, Februar 2022 | https://datenschutzkonferenz-online.de/media/oh/OH-Werbung_Februar%202022_final.pdf |
| Preisliste der Google Maps Platform, Stand 28. Oktober 2026 | https://developers.google.com/maps/billing-and-pricing/pricing |
| Google Maps Platform EEA Service Specific Terms, Abschnitte A.3 und 15, Stand 10. Juni 2026 | https://cloud.google.com/terms/maps-platform/eea/maps-service-terms |
| Places API Permitted Uses für Kunden mit Rechnungsanschrift im EEA, Stand 4. Juni 2025 | https://cloud.google.com/terms/maps-platform/eea-places-api-permitted-uses |
| Places API: Anpassungen für EEA-Kunden (gültig seit 8. Juli 2025) | https://developers.google.com/maps/comms/eea/places |
