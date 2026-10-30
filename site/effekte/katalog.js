// Die dreizehn gewichteten Bereiche und ihre 104 Prüfungen, Stand config/pruefkatalog.yaml.
// Je Prüfung: Name deutsch, Name englisch, Kundenfrage, Folge (beide deutsch, Wortlaut des Katalogs), Kennung.
// Die Namen stehen hier zweisprachig, weil die Projektseite ohne Build-Schritt läuft und
// der Ring die Karten im Browser zeichnet. Bereich m (Wirkung mit Kundenzugang, Gewicht 0)
// zählt nicht zur Punktzahl und bleibt deshalb außerhalb des Rings.
export const bereiche = [
  { id: "a", gewicht: 9, de: "Technik der Website", en: "Website technology",
    pruefungen: [
      ["Verschlüsselte Seite unter einer einzigen Adresse", "Encrypted site under a single address", "Ist deine Seite verschlüsselt und unter genau einer Adresse erreichbar?", "Ohne Schloss im Browser warnt Google Besucher; mehrere Adressen teilen deine Sichtbarkeit auf.", "a1"],
      ["Seite lädt am Handy in unter 2,5 Sekunden", "Page loads on a phone in under 2.5 seconds", "Lädt deine Seite auf dem Handy in unter 2,5 Sekunden?", "Jede Sekunde mehr kostet Anrufe, weil Besucher abspringen; Google stuft langsame Seiten zurück.", "a2"],
      ["Seite am Handy ohne Zoomen bedienbar", "Page usable on a phone without zooming", "Ist deine Seite auf dem Handy bedienbar, ohne zu zoomen?", "Die meisten Kunden suchen am Handy und geben auf, wenn sie zoomen müssen.", "a3"],
      ["Google findet alle deine Seiten", "Google finds all your pages", "Findet Google alle deine Seiten?", "Seiten, die Google nicht kennt, bringen keine Kunden.", "a4"],
      ["Keine toten Links", "No dead links", "Führen alle Links auf deiner Seite zum Ziel?", "Tote Links wirken ungepflegt, auf Besucher und auf Google.", "a5"],
      ["Jede Seite gibt es für Google nur einmal", "Each page exists only once for Google", "Weiß Google, welche Fassung einer Seite die richtige ist?", "Ohne diese Angabe konkurrieren Kopien deiner Seite miteinander.", "a6"],
      ["Bilder klein und beschrieben", "Images small and described", "Sind deine Bilder klein genug und beschrieben?", "Große Bilder bremsen die Seite; fehlende Beschreibungen kosten Sichtbarkeit in der Bildersuche.", "a7"],
      ["Text auch ohne Zusatzprogramme lesbar", "Text readable without add-ons", "Kann Google deinen Text lesen, ohne die Seite auszuführen?", "Was nur per Skript erscheint, übersehen Suchmaschinen und KI-Assistenten oft.", "a8"],
      ["Seite als deutsch gekennzeichnet", "Page marked as German", "Ist deine Seite als deutsch gekennzeichnet?", "Google ordnet die Seite sonst dem falschen Sprachraum zu.", "a9"],
      ["Keine Sicherheitswarnungen im Browser", "No security warnings in the browser", "Ist deine Seite sauber abgesichert?", "Browser-Warnungen schrecken Kunden ab.", "a10"]
    ] },
  { id: "b", gewicht: 10, de: "Aufbau der Seiten", en: "Page structure",
    pruefungen: [
      ["Jede Seite nennt oben Leistung und Ort", "Each page names service and place at the top", "Sagt die Überschrift jeder Seite, was du wo anbietest?", "Google liest die Hauptüberschrift als Thema der Seite.", "b1"],
      ["Titel bei Google nennt, wonach Kunden suchen", "Title on Google names what customers search for", "Steht in deinem Seitentitel, wonach Kunden suchen?", "Der Titel ist die blaue Zeile bei Google; er entscheidet, ob geklickt wird.", "b2"],
      ["Eigener Vorschautext bei Google je Seite", "Own preview text on Google for each page", "Hat jede Seite einen eigenen Vorschautext für Google?", "Ohne Vorschautext schreibt Google selbst etwas, meist Unpassendes.", "b3"],
      ["Überschriften sauber gegliedert", "Headings cleanly structured", "Sind deine Überschriften sauber gegliedert?", "Eine klare Gliederung hilft Google und KI-Assistenten, Inhalte zuzuordnen.", "b4"],
      ["Jede Leistung in höchstens drei Klicks erreichbar", "Each service within three clicks", "Sind deine Leistungen von der Startseite aus schnell erreichbar?", "Versteckte Seiten werden seltener gefunden, von Kunden und von Google.", "b5"],
      ["Verständliche Seitenadressen", "Readable page addresses", "Sagt die Internetadresse einer Seite, worum es geht?", "\"/dachsanierung-muenster\" wird öfter geklickt als \"/seite-17\".", "b6"],
      ["Geteilte Links zeigen Bild und Titel", "Shared links show image and title", "Sieht ein geteilter Link zu deiner Seite gut aus?", "Ein Link ohne Bild und Titel wird in WhatsApp seltener angeklickt.", "b7"],
      ["Kein doppelter Text auf mehreren Seiten", "No duplicate text across pages", "Hat jede Seite eigenen Text?", "Kopierte Seiten verdrängen sich gegenseitig bei Google.", "b8"],
      ["Seite zeigt, wo man gerade ist", "Page shows where you are", "Zeigt deine Seite, wo man sich gerade befindet?", "Google zeigt diesen Pfad im Suchergebnis an; das bringt Klicks.", "b9"],
      ["Hilfreiche Fehlerseite", "Helpful error page", "Landet ein Besucher auf einer toten Seite, findet er zurück?", "Sonst ist der Besucher weg.", "b10"]
    ] },
  { id: "c", gewicht: 12, de: "Inhalte und Leistungsseiten", en: "Content and service pages",
    pruefungen: [
      ["Eigene Seite je Leistung", "A page of its own for each service", "Hat jede deiner Leistungen eine eigene Seite?", "Google zeigt für \"Dachsanierung\" die Seite, die nur davon handelt, nicht die Sammelseite.", "c1"],
      ["Seite für jede gesuchte Leistung in deinem Ort", "A page for every service searched for in your town", "Gibt es für jede gesuchte Leistung in deinem Ort eine Seite?", "Für jede fehlende Seite gehen Suchende zum Wettbewerber.", "c2"],
      ["Leistungsseiten mit Ablauf, Dauer und Preisrahmen", "Service pages with process, duration and price range", "Beantwortet jede Leistungsseite Ablauf, Dauer und Preisrahmen?", "Dünne Seiten ranken nicht und überzeugen nicht.", "c3"],
      ["Die Suchwörter der Kunden stehen im Text", "The customers' search words are in the text", "Stehen die Wörter, nach denen Kunden suchen, im Text?", "Google findet nur, was auch dasteht.", "c4"],
      ["Häufige Kundenfragen beantwortet", "Frequent customer questions answered", "Beantwortet deine Seite die Fragen, die Google unter \"Nutzer fragen auch\" zeigt?", "Diese Fragen stellen Kunden auch KI-Assistenten; wer sie beantwortet, wird genannt.", "c5"],
      ["Stadtteile und Nachbarorte im Text", "Districts and nearby towns in the text", "Nennt dein Text Stadtteile und Nachbarorte?", "Nur der Stadtname im Titel reicht nicht, um für das Umland gefunden zu werden.", "c6"],
      ["Nichts Veraltetes auf der Seite", "Nothing outdated on the page", "Steht auf deiner Seite nichts Veraltetes?", "Alte Jahreszahlen und Preise wirken wie ein geschlossener Laden.", "c7"],
      ["Über-uns mit Namen, Fotos und Qualifikation", "About page with names, photos and qualifications", "Zeigt deine Seite, wer dahinter steht?", "Google bewertet Erfahrung und Vertrauenswürdigkeit; Kunden auch.", "c8"],
      ["Kontakt mit einem Tippen", "Contact with a single tap", "Kann ein Kunde dich mit einem Tipp anrufen?", "Jeder Umweg kostet Anfragen.", "c9"],
      ["Ratgeber zu Themen aus deiner Region", "Guides on topics from your region", "Hat deine Seite Beiträge zu Themen aus deiner Region?", "Regionale Themen bringen Besucher, die sonst niemand bedient.", "c10"],
      ["Keine fast leeren Seiten", "No almost empty pages", "Gibt es Seiten mit fast keinem Inhalt?", "Sie ziehen die Bewertung der ganzen Seite nach unten.", "c11"],
      ["Suchbegriffe, bei denen der Wettbewerber gefunden wird und du nicht", "Search terms where the competitor is found and you are not", "Für welche Begriffe wird dein Wettbewerber gefunden und du nicht?", "Das ist die Liste der Aufträge, die heute woanders landen.", "c12"]
    ] },
  { id: "d", gewicht: 6, de: "Firmendaten auf der Website", en: "Company details on the website",
    pruefungen: [
      ["Name, Adresse, Telefon auf jeder Seite als Text", "Name, address, phone as text on every page", "Stehen deine Firmendaten auf jeder Seite als Text?", "Google gleicht sie mit deinem Profil ab; als Bild sind sie unsichtbar.", "d1"],
      ["Adresse und Telefon überall gleich geschrieben", "Address and phone written the same everywhere", "Sind Adresse und Telefon überall gleich geschrieben?", "Abweichungen lassen Google zweifeln, ob es dieselbe Firma ist.", "d2"],
      ["Öffnungszeiten auf Website und Google gleich", "Opening hours the same on website and Google", "Stimmen deine Öffnungszeiten auf Website und Google überein?", "Falsche Zeiten kosten Besuche und bringen schlechte Bewertungen.", "d3"],
      ["Eigene Seite je Standort", "A page of its own for each location", "Hat jeder Standort eine eigene Seite?", "Sonst wird nur ein Standort gefunden.", "d4"],
      ["Website verlinkt dein Google-Profil", "Website links your Google profile", "Verlinkt deine Seite auf dein Google-Profil?", "Das verbindet Website und Profil für Google.", "d5"],
      ["Telefon und E-Mail antippbar", "Phone and email tappable", "Sind Telefonnummer und E-Mail antippbar?", "Am Handy wählt sonst niemand eine Nummer ab.", "d6"]
    ] },
  { id: "e", gewicht: 7, de: "Unsichtbare Angaben für Google und KI", en: "Hidden data for Google and AI",
    pruefungen: [
      ["Firmendaten maschinenlesbar auf der Startseite", "Company data machine-readable on the home page", "Erklärt deine Seite Google und KI-Assistenten in deren Sprache, was du bist?", "Ohne diese unsichtbaren Angaben raten Maschinen; mit ihnen wissen sie Adresse, Zeiten, Leistungen.", "e1"],
      ["Logo für Google hinterlegt", "Logo provided for Google", "Ist dein Logo für Google hinterlegt?", "Google zeigt es dann neben deinem Namen.", "e2"],
      ["Leistungen und Einsatzgebiet maschinenlesbar", "Services and service area machine-readable", "Sind deine Leistungen maschinenlesbar hinterlegt?", "KI-Assistenten nennen Betriebe, deren Leistungen sie sicher kennen.", "e3"],
      ["Häufige Fragen für Google markiert", "Frequent questions marked up for Google", "Sind deine häufigen Fragen für Google markiert?", "Google kann sie direkt im Suchergebnis zeigen.", "e4"],
      ["Seitenpfad maschinenlesbar", "Page path machine-readable", "Ist der Seitenpfad maschinenlesbar?", "Ergibt den Pfad im Suchergebnis.", "e5"],
      ["Sterne nur bei echten Bewertungen", "Stars only for real reviews", "Werden Sterne nur dort markiert, wo echte Bewertungen stehen?", "Falsch markierte Sterne bestraft Google.", "e6"],
      ["Unsichtbare Angaben fehlerfrei", "Hidden data free of errors", "Sind die unsichtbaren Angaben fehlerfrei?", "Fehlerhafte Angaben ignoriert Google komplett.", "e7"],
      ["Website verweist auf alle deine Profile", "Website points to all your profiles", "Verweist deine Seite auf alle deine Profile?", "So erkennen Maschinen, dass alle Einträge zu einer Firma gehören.", "e8"]
    ] },
  { id: "f", gewicht: 14, de: "Google-Unternehmensprofil", en: "Google Business Profile",
    pruefungen: [
      ["Profil gehört dir und ist bestätigt", "Profile is yours and verified", "Hast du dein Google-Profil in der Hand?", "Ohne Profil keine Karte; ohne Besitz kann jeder deine Daten ändern.", "f1"],
      ["Hauptkategorie wie bei den stärksten Wettbewerbern", "Main category as with the strongest competitors", "Steht dein Profil in derselben Schublade wie deine Wettbewerber?", "Die Kategorie entscheidet, bei welchen Suchen du auf der Karte erscheinst.", "f2"],
      ["Drei bis neun Nebenkategorien", "Three to nine secondary categories", "Deckt dein Profil alle deine Leistungen als Kategorien ab?", "Jede fehlende Kategorie ist eine Suche, bei der du nicht erscheinst.", "f3"],
      ["Beschreibung nutzt den Platz, Leistung und Ort vorn", "Description uses the space, service and place first", "Nutzt deine Profilbeschreibung den Platz?", "Die ersten 250 Zeichen sieht jeder; sie müssen Leistung und Ort nennen.", "f4"],
      ["Leistungen eingetragen", "Services listed", "Sind deine Leistungen im Profil eingetragen?", "Google zeigt Profile mit passenden Leistungen bevorzugt.", "f5"],
      ["Produkte oder Angebote eingetragen", "Products or offers listed", "Zeigt dein Profil Produkte oder Angebote?", "Produkte bringen Anfragen direkt aus dem Profil.", "f6"],
      ["Öffnungszeiten vollständig, auch an Feiertagen", "Opening hours complete, holidays included", "Sind deine Öffnungszeiten vollständig?", "\"Geschlossen\" an einem Werktag ohne Angabe kostet Kunden.", "f7"],
      ["Mindestens zehn Fotos, Logo und Titelbild", "At least ten photos, logo and cover image", "Zeigt dein Profil aktuelle Fotos?", "Profile mit Fotos bekommen mehr Anrufe und Routenanfragen.", "f8"],
      ["Neuestes Foto jünger als 90 Tage", "Newest photo under 90 days old", "Wie alt ist das neueste Foto in deinem Profil?", "Alte Fotos wirken wie ein verlassener Betrieb; Kunden rufen lieber dort an, wo sie aktuelle Arbeit sehen.", "f15"],
      ["Beitrag in den letzten 30 Tagen", "A post within the last 30 days", "Postest du regelmäßig in deinem Profil?", "Google belohnt aktive Profile mit besserer Platzierung.", "f9"],
      ["Kundenfragen im Profil beantwortet", "Customer questions answered in the profile", "Sind die typischen Kundenfragen in deinem Profil beantwortet?", "Sonst beantwortet sie irgendwer, oft falsch.", "f10"],
      ["Einsatzorte eingetragen", "Service areas listed", "Sind deine Einsatzorte im Profil eingetragen?", "Nur dort erscheinst du bei Suchen aus dem Umland.", "f11"],
      ["Website, Terminbuchung und Nachrichten verknüpft", "Website, booking and messages linked", "Kann ein Kunde aus deinem Profil heraus Kontakt aufnehmen?", "Jeder zusätzliche Weg bringt Anfragen.", "f12"],
      ["Zahlungsarten und Besonderheiten eingetragen", "Payment methods and features listed", "Sind Zahlungsarten, Barrierefreiheit und Besonderheiten eingetragen?", "Google filtert danach.", "f13"],
      ["Profil verstößt gegen keine Google-Regel", "Profile breaks no Google rule", "Verstößt dein Profil gegen Googles Regeln?", "Schlüsselwörter im Namen oder doppelte Profile können zur Sperre führen.", "f14"]
    ] },
  { id: "g", gewicht: 8, de: "Bewertungen", en: "Reviews",
    pruefungen: [
      ["Mehr Bewertungen als die Betriebe neben dir auf der Karte", "More reviews than the businesses next to you on the map", "Hast du mehr oder weniger Bewertungen als deine Wettbewerber?", "Kunden wählen den mit mehr Bewertungen, Google auch.", "g1"],
      ["Durchschnitt über 4,3 Sterne", "Average above 4.3 stars", "Liegt dein Schnitt über 4,3 Sternen?", "Unter 4 Sternen filtern viele Kunden dich weg.", "g2"],
      ["Mindestens zwei neue Bewertungen im Monat", "At least two new reviews a month", "Kommen laufend neue Bewertungen?", "Alte Bewertungen zählen weniger; Google will aktuelle.", "g3"],
      ["Du antwortest auf Bewertungen", "You reply to reviews", "Antwortest du auf Bewertungen?", "Antworten zeigen Kunden und Google, dass jemand da ist.", "g4"],
      ["Sachliche Antworten auf Kritik", "Factual replies to criticism", "Reagierst du auf Kritik?", "Eine gute Antwort auf eine schlechte Bewertung überzeugt mehr als zehn Sterne.", "g5"],
      ["Kunden nennen in Bewertungen Leistung und Ort", "Customers name service and place in reviews", "Erwähnen Kunden in Bewertungen, was du für sie gemacht hast?", "Google liest Bewertungen als Beleg für deine Leistungen.", "g6"],
      ["Bewertungen auch auf anderen Portalen", "Reviews on other portals too", "Gibt es dich auch auf anderen Bewertungsportalen?", "KI-Assistenten ziehen aus mehreren Quellen.", "g7"],
      ["Bewertungen sehen echt aus", "Reviews look genuine", "Sehen deine Bewertungen echt aus?", "Auffällige Muster führen zu Löschung oder Sperre.", "g8"]
    ] },
  { id: "h", gewicht: 7, de: "Verzeichnisse und Einträge", en: "Directories and listings",
    pruefungen: [
      ["Eintrag in Apple Karten", "Listing in Apple Maps", "Findet man dich auf dem iPhone in Apple Karten?", "Viele iPhone-Nutzer suchen dort statt bei Google.", "h1"],
      ["Eintrag bei Bing", "Listing on Bing", "Findet man dich bei Bing und in Copilot?", "Windows-Nutzer und Microsofts KI suchen hier.", "h2"],
      ["Einträge in Gelbe Seiten, Das Örtliche und 11880", "Listings in Gelbe Seiten, Das Örtliche and 11880", "Stehst du in Gelbe Seiten, Das Örtliche, 11880 und Co.?", "Diese Einträge bestätigen Google, dass es dich gibt.", "h3"],
      ["Einträge bei Kammer, Innung und Fachportalen", "Listings with chamber, guild and trade portals", "Stehst du bei Kammer, Innung und Fachportalen?", "Fachportale bringen eigene Anfragen und Vertrauen.", "h4"],
      ["Facebook und Instagram mit denselben Daten", "Facebook and Instagram with the same data", "Stimmen deine Firmendaten in den sozialen Netzwerken?", "Abweichungen verwirren Kunden und Maschinen.", "h5"],
      ["Keine alten Adressen oder Nummern im Netz", "No old addresses or numbers on the web", "Gibt es alte Adressen oder Nummern von dir im Netz?", "Kunden rufen die falsche Nummer an; Google vertraut keiner.", "h6"],
      ["Eintrag bei Wikidata", "Entry on Wikidata", "Kennt Wikidata deinen Betrieb?", "KI-Assistenten und Googles Wissensfeld schöpfen daraus.", "h7"]
    ] },
  { id: "i", gewicht: 6, de: "Empfehlungen durch Links", en: "Recommendations through links",
    pruefungen: [
      ["So viele Empfehlungs-Links wie die Wettbewerber", "As many recommending links as the competitors", "Verlinken so viele Seiten auf dich wie auf deine Wettbewerber?", "Links sind Empfehlungen; Google zählt sie.", "i1"],
      ["Links von Vereinen, Partnern und Lokalpresse", "Links from clubs, partners and local press", "Verlinken Vereine, Partner und Lokalpresse auf dich?", "Lokale Links zählen für lokale Suchen doppelt.", "i2"],
      ["Keine Links von zwielichtigen Seiten", "No links from shady sites", "Verlinkt Schrott auf deine Seite?", "Schlechte Nachbarschaft drückt die Bewertung.", "i3"],
      ["Nennungen deines Namens ohne Link", "Mentions of your name without a link", "Wird dein Name im Netz genannt, ohne dass verlinkt wird?", "Jede Nennung lässt sich oft mit einer Mail in einen Link verwandeln.", "i4"],
      ["Verlauf deiner Sichtbarkeit", "Trend of your visibility", "Wie hat sich deine Sichtbarkeit entwickelt?", "Der Verlauf zeigt, ob etwas wirkt.", "i5"],
      ["Links zu Herstellern, Verbänden und Partnern", "Links to manufacturers, associations and partners", "Verlinkst du auf Hersteller, Verbände, Partner?", "Zeigt Google dein Umfeld.", "i6"]
    ] },
  { id: "j", gewicht: 10, de: "Platzierung bei Google und Bing", en: "Ranking on Google and Bing",
    pruefungen: [
      ["Platz bei den wichtigsten Suchen in deinem Ort", "Position for the most important searches in your town", "Auf welchem Platz stehst du für deine wichtigsten Suchen?", "Die obersten Plätze bekommen die meisten Klicks, ab Seite 2 fast niemand mehr.", "j1"],
      ["Unter den drei Betrieben auf der Karte", "Among the three businesses on the map", "Bist du einer der drei Betriebe auf der Karte?", "Die Karte steht über allen Ergebnissen; wer dort fehlt, wird am Handy kaum gesehen.", "j2"],
      ["Sichtbarkeit rund um deinen Standort", "Visibility around your location", "Wo im Umkreis bist du auf der Karte sichtbar und wo nicht?", "Zeigt, aus welchen Stadtteilen keine Kunden kommen können.", "j3"],
      ["Platz 1 bei der Suche nach deinem Namen", "Position 1 when searching for your name", "Findet man dich, wenn man deinen Namen sucht?", "Wer nach dir sucht, hat sich fast entschieden; da darf nichts dazwischen stehen.", "j4"],
      ["Anzahl der Suchen, bei denen du gefunden wirst", "Number of searches you are found for", "Für wie viele Suchen wirst du überhaupt gefunden?", "Mehr Begriffe, mehr Türen.", "j5"],
      ["Genannt in Googles KI-Antwort", "Named in Google's AI answer", "Nennt Googles KI-Antwort deinen Betrieb?", "Diese Antwort steht ganz oben und verdrängt die normalen Ergebnisse.", "j6"],
      ["Gefunden bei Bing", "Found on Bing", "Wirst du auch bei Bing gefunden?", "Bing speist Copilot und ChatGPT-Suche.", "j7"],
      ["Deine Bilder in der Google-Bildersuche", "Your images in Google image search", "Erscheinen deine Bilder bei Google Bilder?", "Für Handwerk und Gastronomie ein eigener Kundenweg.", "j8"],
      ["Entwicklung über die Zeit", "Development over time", "Geht es aufwärts?", "Der Nachweis für das Abo.", "j9"],
      ["Wettbewerber werben auf deinen Namen", "Competitors advertise on your name", "Schaltet ein Wettbewerber Werbung auf deinen Namen?", "Er fängt deine Kunden ab.", "j10"]
    ] },
  { id: "k", gewicht: 5, de: "Sichtbarkeit in KI-Assistenten", en: "Visibility in AI assistants",
    pruefungen: [
      ["Genannt, wenn Kunden eine KI nach einem Betrieb fragen", "Named when customers ask an AI for a business", "Nennt ChatGPT dich, wenn jemand nach einem Betrieb wie deinem fragt?", "Immer mehr Kunden fragen zuerst eine KI statt Google.", "k1"],
      ["Genannt, wenn Kunden ihr Problem beschreiben", "Named when customers describe their problem", "Wirst du genannt, wenn jemand sein Problem beschreibt?", "Hier entstehen die Aufträge, bevor der Kunde einen Namen kennt.", "k2"],
      ["KI-Assistenten sagen Richtiges über dich", "AI assistants say correct things about you", "Stimmt, was die KI über dich sagt?", "Falsche Öffnungszeiten aus der KI kosten Besuche.", "k3"],
      ["Präsent dort, wo KI-Assistenten nachlesen", "Present where AI assistants look things up", "Stehst du dort, wo die KI nachliest?", "Meist Google-Profil, Verzeichnisse, Bewertungsportale, Lokalpresse.", "k4"],
      ["KI-Assistenten dürfen deine Seite lesen", "AI assistants may read your site", "Dürfen KI-Assistenten deine Seite lesen?", "Wer sie aussperrt, kommt in keiner KI-Antwort vor.", "k5"]
    ] },
  { id: "l", gewicht: 4, de: "Recht und Vertrauen", en: "Law and trust",
    pruefungen: [
      ["Impressum vollständig und schnell erreichbar", "Legal notice complete and quick to reach", "Ist dein Impressum vollständig?", "Ein fehlendes Impressum ist der häufigste Grund für Abmahnungen.", "l1"],
      ["Datenschutzerklärung passt zur Seite", "Privacy policy matches the site", "Passt deine Datenschutzerklärung zu dem, was deine Seite tut?", "Google Maps, Schriften und Formulare ohne Erwähnung sind abmahnfähig.", "l2"],
      ["Fremde Dienste erst nach Zustimmung", "Third-party services only after consent", "Lädt deine Seite fremde Dienste erst nach Zustimmung?", "Google-Schriften vom Google-Server gelten in Deutschland als Verstoß.", "l3"],
      ["Auch mit Einschränkungen nutzbar", "Usable with impairments too", "Können auch Menschen mit Einschränkungen deine Seite nutzen?", "Kontraste und Beschriftungen helfen allen und zählen bei Google.", "l4"]
    ] },
  { id: "n", gewicht: 2, de: "Werkzeuge auf der Website", en: "Tools on the website",
    pruefungen: [
      ["Chat oder Sofort-Kontakt auf der Website", "Chat or instant contact on the website", "Kann ein Besucher dir sofort schreiben, auch außerhalb der Öffnungszeiten?", "Wer abends eine Frage hat und niemanden erreicht, fragt beim Nächsten.", "n1"],
      ["Besucherzählung eingebaut, mit Zustimmung", "Visitor counting built in, with consent", "Weißt du, wie viele Besucher deine Website hat?", "Ohne Zählung weiß niemand, wie viele Kunden die Seite bringt und was eine Verbesserung bewirkt.", "n2"],
      ["Eigene Google-Anzeigen geschaltet", "Own Google ads running", "Bezahlst du gerade für Google-Anzeigen?", "Zeigt, ob du gerade für Werbung zahlst und ob sie auf eine passende Seite führt.", "n3"]
    ] }
];

// Flache Liste aller Karten in Ringreihenfolge: Bereich, Kennung, Name je Sprache, Frage, Folge.
export function karten(sprache) {
  const liste = [];
  bereiche.forEach((b, bi) => {
    b.pruefungen.forEach((p) => {
      liste.push({ bereich: bi, kennung: p[4], name: sprache === "en" ? p[1] : p[0], frage: p[2], wirkung: p[3] });
    });
  });
  return liste;
}

export const texte = {
  de: { pruefungen: "Prüfungen", gewicht: "Gewicht" },
  en: { pruefungen: "checks", gewicht: "weight" }
};
