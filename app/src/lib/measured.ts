// Plain-German lines for the `measured` JSON the scorer stores per check
// (src/radarauditor/scoring/checks/*.py); measured.test.ts keeps both in step.
import { formatDate } from "./format";

export interface MeasuredLine {
  key: string;
  label: string;
  value: string;
}

type Kind =
  | "yesno" | "manual" | "found" | "count" | "number" | "words" | "chars" | "searches" | "clicks" | "place"
  | "ms" | "decimal" | "score" | "share" | "change" | "rating" | "days" | "perMonth" | "date" | "year"
  | "month" | "url" | "text" | "term" | "urls" | "hosts" | "terms" | "quotes" | "positions" | "counts"
  | "shares" | "changes" | "metrics" | "searchTotals" | "urlTerms" | "urlCounts" | "urlShares"
  | "listings" | "pairs" | "gaps" | "nap" | "schemaErrors" | "reason" | "sources" | "businesses" | "hidden";

interface Field {
  label: string;
  kind: Kind;
}

const LISTED = 4;
const NOT_FOUND = "nicht gefunden";

const integer = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });
const oneDecimal = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 });
const twoDecimals = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 });

/** Every key the scorer writes, in the order the lines appear. */
const FIELDS: Readonly<Record<string, Field>> = {
  reason: { label: "Grund", kind: "reason" },
  sources: { label: "Ausgeschöpft", kind: "sources" },
  capped_sources: { label: "Ohne Antworten von (Tageslimit)", kind: "sources" },
  // Shown as the small route note of the check (routes.ts), not as a line.
  route: { label: "Datenweg", kind: "hidden" },
  routes: { label: "Datenwege", kind: "hidden" },
  pages_total: { label: "Seiten geprüft", kind: "count" },
  uses_https: { label: "Verschlüsselt (HTTPS)", kind: "yesno" },
  redirects_to_single_host: { label: "Alle Varianten führen auf eine Adresse", kind: "yesno" },
  lcp_ms: { label: "Hauptinhalt sichtbar nach", kind: "ms" },
  lcp_ms_max: { label: "Ziel: Hauptinhalt nach höchstens", kind: "ms" },
  cls: { label: "Verrutschen beim Laden (CLS)", kind: "decimal" },
  cls_max: { label: "Ziel: Verrutschen höchstens", kind: "decimal" },
  inp_ms: { label: "Reaktion auf Tippen", kind: "ms" },
  inp_ms_max: { label: "Ziel: Reaktion nach höchstens", kind: "ms" },
  mobile_performance: { label: "Tempo-Wertung Handy", kind: "score" },
  desktop_performance: { label: "Tempo-Wertung Computer", kind: "score" },
  mobile_score_min: { label: "Ziel: Tempo-Wertung Handy mindestens", kind: "score" },
  pages_without_viewport: { label: "Seiten ohne Handy-Darstellung", kind: "urls" },
  pages_without_viewport_count: { label: "Anzahl Seiten ohne Handy-Darstellung", kind: "count" },
  has_robots_txt: { label: "robots.txt vorhanden", kind: "yesno" },
  sitemap_count: { label: "Sitemaps", kind: "count" },
  blocks_google: { label: "Sperrt Google aus", kind: "yesno" },
  broken_urls: { label: "Tote Links", kind: "urls" },
  broken_count: { label: "Anzahl toter Links", kind: "count" },
  crawl_errors: { label: "Nicht abrufbar", kind: "urls" },
  max_broken: { label: "Ziel: tote Links höchstens", kind: "count" },
  pages_missing_canonical: { label: "Seiten ohne Canonical-Angabe", kind: "urls" },
  pages_missing_count: { label: "Anzahl Seiten ohne Canonical-Angabe", kind: "count" },
  images_without_alt: { label: "Bilder ohne Beschreibung (Alt-Text)", kind: "count" },
  oversized_images: { label: "Zu große Bilder", kind: "count" },
  pages_with_issues: { label: "Seiten mit Bildproblemen", kind: "urls" },
  max_image_kb: { label: "Ziel: Bilder höchstens (Kilobyte)", kind: "count" },
  max_missing_alt: { label: "Ziel: Bilder ohne Beschreibung höchstens", kind: "count" },
  pages_without_text: { label: "Seiten ohne lesbaren Text", kind: "urls" },
  pages_without_text_count: { label: "Anzahl Seiten ohne lesbaren Text", kind: "count" },
  pages_without_german_lang: { label: "Seiten nicht als deutsch gekennzeichnet", kind: "urls" },
  open_admin_pages: { label: "Offene Verwaltungsseiten", kind: "urls" },
  insecure_links: { label: "Unverschlüsselte Links (http)", kind: "urls" },
  insecure_link_count: { label: "Anzahl unverschlüsselter Links", kind: "count" },
  pages_without_single_h1: { label: "Seiten ohne genau eine Hauptüberschrift", kind: "urls" },
  pages_without_single_h1_count: { label: "Anzahl Seiten ohne genau eine Hauptüberschrift", kind: "count" },
  start_page_h1: { label: "Hauptüberschrift der Startseite", kind: "text" },
  start_page_h1_has_city: { label: "Hauptüberschrift nennt den Ort", kind: "yesno" },
  pages_ok: { label: "Seiten in Ordnung", kind: "count" },
  pages_missing: { label: "Seiten ohne Angabe", kind: "urls" },
  pages_too_short: { label: "Zu kurz", kind: "urls" },
  pages_too_long: { label: "Zu lang", kind: "urls" },
  pages_duplicate: { label: "Doppelt vergeben", kind: "urls" },
  min_chars: { label: "Ziel: mindestens", kind: "chars" },
  max_chars: { label: "Ziel: höchstens", kind: "chars" },
  pages_with_jumps: { label: "Seiten mit Sprüngen in der Gliederung", kind: "urls" },
  pages_too_deep: { label: "Seiten zu tief versteckt", kind: "urls" },
  pages_too_deep_count: { label: "Anzahl zu tief versteckter Seiten", kind: "count" },
  deepest_click_depth: { label: "Tiefste Seite (Klicks ab Startseite)", kind: "count" },
  max_depth: { label: "Ziel: Klicks höchstens", kind: "count" },
  cryptic_urls: { label: "Unverständliche Adressen", kind: "urls" },
  pages_incomplete: { label: "Seiten mit unvollständiger Link-Vorschau", kind: "urlTerms" },
  pages_incomplete_count: { label: "Anzahl Seiten mit unvollständiger Link-Vorschau", kind: "count" },
  duplicate_pairs: { label: "Fast gleiche Seiten", kind: "pairs" },
  pages_involved: { label: "Seiten mit doppeltem Text", kind: "count" },
  max_similarity: { label: "Ziel: Ähnlichkeit höchstens", kind: "share" },
  pages_without_breadcrumb: { label: "Seiten ohne Brotkrümelpfad", kind: "urls" },
  subpages_total: { label: "Unterseiten geprüft", kind: "count" },
  error_page_url: { label: "Getestete tote Adresse", kind: "url" },
  links_on_error_page: { label: "Links auf der Fehlerseite", kind: "count" },
  services_without_page: { label: "Leistungen ohne eigene Seite", kind: "terms" },
  services_total: { label: "Leistungen geprüft", kind: "count" },
  phrases_with_demand: { label: "Suchbegriffe mit Nachfrage", kind: "terms" },
  phrases_without_page: { label: "Suchbegriffe ohne eigene Seite", kind: "terms" },
  min_volume: { label: "Ziel: Nachfrage ab", kind: "searches" },
  pages_below_min_words: { label: "Zu kurze Leistungsseiten", kind: "urlCounts" },
  lowest_word_count: { label: "Kürzeste Leistungsseite", kind: "words" },
  min_words: { label: "Ziel: mindestens", kind: "words" },
  phrases_missing_in_text: { label: "Suchbegriffe fehlen im Text", kind: "terms" },
  pages_over_density: { label: "Seiten mit zu vielen Wiederholungen", kind: "urlShares" },
  max_density: { label: "Ziel: Anteil eines Begriffs höchstens", kind: "share" },
  unanswered_questions: { label: "Unbeantwortete Fragen", kind: "quotes" },
  questions_total: { label: "Fragen geprüft", kind: "count" },
  pages_without_city: { label: "Seiten ohne Ort im Text", kind: "urls" },
  city: { label: "Ort", kind: "text" },
  latest_year_on_site: { label: "Neuestes Jahr auf der Website", kind: "year" },
  audit_year: { label: "Jahr der Prüfung", kind: "year" },
  about_page: { label: "Über-uns-Seite", kind: "url" },
  mentions_qualification: { label: "Nennt Qualifikationen", kind: "yesno" },
  contact_page: { label: "Kontaktseite", kind: "url" },
  has_tel_link: { label: "Telefonnummer antippbar", kind: "yesno" },
  guide_posts: { label: "Ratgeber-Artikel", kind: "count" },
  guide_posts_with_city: { label: "davon mit Ortsbezug", kind: "count" },
  min_posts: { label: "Ziel: Artikel mindestens", kind: "count" },
  thin_pages: { label: "Seiten mit wenig Text", kind: "urlCounts" },
  thin_page_count: { label: "Anzahl Seiten mit wenig Text", kind: "count" },
  gap_keywords: { label: "Suchbegriffe nur bei Wettbewerbern", kind: "count" },
  top_gap_keywords: { label: "Wichtigste davon", kind: "gaps" },
  footer_nap: { label: "Firmendaten im Fußbereich", kind: "nap" },
  missing_fields: { label: "Fehlt im Fußbereich", kind: "terms" },
  website: { label: "Auf der Website", kind: "nap" },
  google: { label: "Im Google-Profil", kind: "nap" },
  mismatches: { label: "Abweichungen", kind: "terms" },
  hours_on_website: { label: "Öffnungszeiten auf der Website", kind: "yesno" },
  hours_on_google: { label: "Öffnungszeiten im Google-Profil", kind: "yesno" },
  hours_text_compared: { label: "Öffnungszeiten verglichen", kind: "manual" },
  location_pages: { label: "Seiten mit der Adresse", kind: "urls" },
  postal_code: { label: "Postleitzahl", kind: "text" },
  google_profile_links: { label: "Links zum Google-Profil", kind: "urls" },
  tel_links: { label: "Telefon-Links", kind: "urls" },
  mailto_links: { label: "E-Mail-Links", kind: "urls" },
  local_business_type: { label: "Eingetragener Firmentyp", kind: "terms" },
  missing_properties: { label: "Fehlende Angaben", kind: "terms" },
  organization_found: { label: "Firmenangabe vorhanden", kind: "yesno" },
  logo_found: { label: "Logo angegeben", kind: "yesno" },
  pages_without_service_schema: { label: "Leistungsseiten ohne Leistungs-Markup", kind: "urls" },
  service_pages_total: { label: "Leistungsseiten geprüft", kind: "count" },
  faq_pages_without_schema: { label: "Fragen-Seiten ohne FAQ-Markup", kind: "urls" },
  faq_pages_total: { label: "Seiten mit Fragen und Antworten", kind: "count" },
  pages_without_breadcrumb_schema: { label: "Seiten ohne Brotkrümel-Markup", kind: "urls" },
  pages_with_rating_markup: { label: "Seiten mit Sterne-Markup", kind: "count" },
  pages_without_visible_reviews: { label: "Sterne-Markup ohne sichtbare Bewertungen", kind: "urls" },
  errors: { label: "Fehler im Markup", kind: "schemaErrors" },
  pages_with_errors: { label: "Seiten mit Fehlern", kind: "count" },
  pages_with_structured_data: { label: "Seiten mit strukturierten Daten", kind: "count" },
  same_as: { label: "Verknüpfte Profile", kind: "urls" },
  missing_targets: { label: "Fehlende Verknüpfungen", kind: "terms" },
  profile_found: { label: "Google-Profil gefunden", kind: "yesno" },
  business_status: { label: "Status", kind: "term" },
  ownership_checked: { label: "Inhaberschaft bestätigt", kind: "manual" },
  primary_type: { label: "Hauptkategorie", kind: "text" },
  competitor_types: { label: "Hauptkategorien der Wettbewerber", kind: "terms" },
  secondary_categories: { label: "Weitere Kategorien", kind: "terms" },
  count: { label: "Anzahl weiterer Kategorien", kind: "count" },
  min: { label: "Ziel: mindestens", kind: "count" },
  max: { label: "Ziel: höchstens", kind: "count" },
  has_opening_hours: { label: "Öffnungszeiten eingetragen", kind: "yesno" },
  holiday_hours_checked: { label: "Feiertagszeiten geprüft", kind: "manual" },
  photo_count: { label: "Fotos", kind: "count" },
  min_photos: { label: "Ziel: Fotos mindestens", kind: "count" },
  photo_age_checked: { label: "Alter der Fotos geprüft", kind: "manual" },
  logo_and_cover_checked: { label: "Logo und Titelbild geprüft", kind: "manual" },
  newest_photo_date: { label: "Neuestes Foto aufgenommen am", kind: "date" },
  newest_photo_days: { label: "Neuestes Foto ist alt", kind: "days" },
  photos_with_date: { label: "Fotos mit Aufnahmedatum", kind: "count" },
  profile_website: { label: "Website im Profil", kind: "url" },
  website_host: { label: "Eigene Website", kind: "text" },
  booking_and_messages_checked: { label: "Buchung und Nachrichten geprüft", kind: "manual" },
  profile_name: { label: "Name im Profil", kind: "text" },
  website_name: { label: "Name auf der Website", kind: "text" },
  keywords_in_name: { label: "Zusatzwörter im Profilnamen", kind: "terms" },
  description_chars: { label: "Länge der Beschreibung", kind: "chars" },
  city_in_front: { label: "Ort in den ersten 250 Zeichen", kind: "yesno" },
  service_in_front: { label: "Leistung in den ersten 250 Zeichen", kind: "yesno" },
  services: { label: "Eingetragene Leistungen", kind: "terms" },
  service_count: { label: "Anzahl Leistungen", kind: "count" },
  phrases_covered: { label: "Suchbegriffe unter den Leistungen", kind: "terms" },
  services_order_checked: { label: "Reihenfolge nach Nachfrage geprüft", kind: "manual" },
  products: { label: "Eingetragene Produkte", kind: "terms" },
  product_count: { label: "Anzahl Produkte", kind: "count" },
  post_count: { label: "Sichtbare Beiträge", kind: "count" },
  newest_post_days: { label: "Neuester Beitrag vor", kind: "days" },
  max_age_days: { label: "Ziel: höchstens so alt", kind: "days" },
  question_count: { label: "Fragen im Profil", kind: "count" },
  owner_answers_checked: { label: "Antworten des Inhabers geprüft", kind: "manual" },
  service_areas: { label: "Einzugsgebiet", kind: "terms" },
  service_area_count: { label: "Anzahl Orte im Einzugsgebiet", kind: "count" },
  attributes: { label: "Eingetragene Merkmale", kind: "terms" },
  attribute_count: { label: "Anzahl Merkmale", kind: "count" },
  customer_reviews: { label: "Eigene Bewertungen", kind: "count" },
  competitors: { label: "Wettbewerber", kind: "counts" },
  competitor_average: { label: "Schnitt der Wettbewerber", kind: "number" },
  compared_businesses: { label: "Verglichene Betriebe", kind: "businesses" },
  comparison_source: { label: "Verglichen mit", kind: "term" },
  phrase: { label: "Suchbegriff der Kartenliste", kind: "text" },
  without_review_count: { label: "Ohne lesbare Bewertungszahl", kind: "terms" },
  rating: { label: "Durchschnitt", kind: "rating" },
  review_count: { label: "Bewertungen", kind: "count" },
  min_rating: { label: "Ziel: mindestens", kind: "rating" },
  reviews_in_sample: { label: "Bewertungen in der Stichprobe", kind: "count" },
  oldest_review: { label: "Älteste davon", kind: "date" },
  newest_review: { label: "Neueste davon", kind: "date" },
  reviews_per_month: { label: "Neue Bewertungen pro Monat", kind: "perMonth" },
  min_per_month: { label: "Ziel: mindestens", kind: "perMonth" },
  replied: { label: "Beantwortet", kind: "count" },
  reply_rate: { label: "Antwortquote", kind: "share" },
  average_reply_days: { label: "Antwortzeit im Schnitt", kind: "days" },
  reply_data_available: { label: "Antworten öffentlich sichtbar", kind: "manual" },
  // Shown as small review cards below the lines (unansweredReviews), not as a line.
  unanswered_examples: { label: "Unbeantwortete Bewertungen", kind: "hidden" },
  min_reply_rate: { label: "Ziel: Antwortquote mindestens", kind: "share" },
  max_reply_days: { label: "Ziel: Antwortzeit höchstens", kind: "days" },
  review_source: { label: "Quelle der Bewertungen", kind: "term" },
  negative_reviews: { label: "Kritische Bewertungen", kind: "count" },
  tone_checked: { label: "Ton der Antworten geprüft", kind: "manual" },
  reviews_with_keywords: { label: "Bewertungen mit Leistung oder Ort", kind: "count" },
  reviews_with_text: { label: "Bewertungen mit Text", kind: "count" },
  keywords: { label: "Gesuchte Wörter", kind: "terms" },
  found_on: { label: "Gefunden auf", kind: "terms" },
  checked: { label: "Geprüfte Portale", kind: "terms" },
  flags: { label: "Auffälligkeiten", kind: "terms" },
  listings: { label: "Einträge", kind: "listings" },
  found: { label: "Gefunden", kind: "found" },
  missing: { label: "Fehlt in", kind: "terms" },
  found_ratio: { label: "Gefunden in", kind: "share" },
  phone_variants: { label: "Schreibweisen der Telefonnummer", kind: "quotes" },
  address_variants: { label: "Schreibweisen der Adresse", kind: "quotes" },
  listings_compared: { label: "Einträge verglichen", kind: "count" },
  max_variants: { label: "Ziel: Schreibweisen höchstens", kind: "count" },
  listing_url: { label: "Eintrag", kind: "url" },
  referring_domains: { label: "Verweisende Websites", kind: "count" },
  best_competitor: { label: "Stärkster Wettbewerber", kind: "text" },
  share_of_best: { label: "Anteil am Stärksten", kind: "share" },
  local_share: { label: "Anteil örtlicher Links", kind: "share" },
  spam_share: { label: "Anteil verdächtiger Links", kind: "share" },
  spam_score: { label: "Spam-Wert", kind: "score" },
  domain_first_capture: { label: "Erstmals im Webarchiv", kind: "date" },
  domain_age_years: { label: "Alter der Domain in Jahren", kind: "decimal" },
  first_month: { label: "Vor zwölf Monaten", kind: "month" },
  first_value: { label: "Begriffe unter den ersten zehn damals", kind: "count" },
  last_month: { label: "Zuletzt", kind: "month" },
  last_value: { label: "Begriffe unter den ersten zehn heute", kind: "count" },
  history: { label: "Verlauf", kind: "hidden" },
  mentions: { label: "Fundstellen", kind: "urls" },
  mention_count: { label: "Anzahl Fundstellen", kind: "count" },
  partner_hosts: { label: "Verlinkte Partner", kind: "hosts" },
  partner_host_count: { label: "Anzahl verlinkter Partner", kind: "count" },
  positions: { label: "Platz je Suchbegriff", kind: "positions" },
  in_top: { label: "Suchbegriffe ganz oben", kind: "count" },
  in_good: { label: "Suchbegriffe auf Seite 1", kind: "count" },
  in_visible: { label: "Suchbegriffe sichtbar", kind: "count" },
  phrases_total: { label: "Suchbegriffe geprüft", kind: "count" },
  top: { label: "Ganz oben heißt bis", kind: "place" },
  good: { label: "Seite 1 heißt bis", kind: "place" },
  visible: { label: "Sichtbar heißt bis", kind: "place" },
  phrases_with_map: { label: "Suchbegriffe mit Kartenblock", kind: "count" },
  maps_phrases: { label: "Platz aus Google Maps statt Kartenblock", kind: "terms" },
  business_name: { label: "Firmenname", kind: "text" },
  points_total: { label: "Punkte im Kartenraster", kind: "count" },
  points_in_top: { label: "davon ganz oben", kind: "count" },
  share_in_top: { label: "Anteil ganz oben", kind: "share" },
  brand_phrase: { label: "Suche nach dem Namen", kind: "text" },
  position: { label: "Platz", kind: "place" },
  knowledge_panel: { label: "Infokasten neben den Treffern", kind: "yesno" },
  phrases_with_overview: { label: "Suchbegriffe mit KI-Übersicht", kind: "count" },
  phrases_mentioning_business: { label: "davon mit Nennung", kind: "count" },
  source_positions: { label: "Platz in der Bildersuche", kind: "positions" },
  sources_seen: { label: "Bildquellen je Suchbegriff", kind: "counts" },
  foreign_ads: { label: "Anzeigen fremder Betriebe", kind: "hosts" },
  ads_seen: { label: "Anzeigen gesehen", kind: "count" },
  ranked_keywords: { label: "Suchbegriffe mit Platzierung", kind: "count" },
  mention_rate: { label: "Genannt in", kind: "share" },
  mentioned: { label: "Antworten mit Nennung", kind: "count" },
  answers: { label: "Antworten", kind: "count" },
  by_provider: { label: "Genannt je Assistent", kind: "shares" },
  min_rate: { label: "Ziel: genannt in mindestens", kind: "share" },
  answers_checked: { label: "Antworten geprüft", kind: "count" },
  answers_with_wrong_facts: { label: "Antworten mit falschen Angaben", kind: "count" },
  wrong_examples: { label: "Beispiele", kind: "quotes" },
  top_cited_hosts: { label: "Meistzitierte Quellen", kind: "counts" },
  missing_on: { label: "Fehlt auf", kind: "hosts" },
  blocked_crawlers: { label: "Ausgesperrte KI-Crawler", kind: "terms" },
  legal_notice_url: { label: "Impressum", kind: "url" },
  click_depth: { label: "Klicks bis zum Impressum", kind: "count" },
  max_clicks: { label: "Ziel: Klicks höchstens", kind: "count" },
  missing_elements: { label: "Fehlt im Impressum", kind: "terms" },
  privacy_url: { label: "Datenschutzerklärung", kind: "url" },
  services_in_use: { label: "Eingebundene Dienste", kind: "terms" },
  services_not_named: { label: "Nicht in der Datenschutzerklärung", kind: "terms" },
  font_hosts: { label: "Schriften geladen von", kind: "hosts" },
  tracking_hosts: { label: "Messdienste", kind: "hosts" },
  consent_hint: { label: "Hinweis auf Einwilligung", kind: "yesno" },
  third_party_hosts: { label: "Fremde Server", kind: "hosts" },
  accessibility_score: { label: "Wertung Barrierefreiheit", kind: "score" },
  min_score: { label: "Ziel: mindestens", kind: "score" },
  metrics: { label: "Google-Profil, letzte 28 Tage", kind: "metrics" },
  metrics_previous: { label: "Google-Profil, 28 Tage davor", kind: "metrics" },
  change_percent: { label: "Veränderung", kind: "changes" },
  actions: { label: "Anrufe, Routen und Website-Klicks", kind: "count" },
  actions_previous: { label: "28 Tage davor", kind: "count" },
  actions_change_percent: { label: "Veränderung", kind: "change" },
  matching_queries: { label: "Suchanfragen mit Ihren Leistungen", kind: "clicks" },
  top_queries: { label: "Häufigste Suchanfragen", kind: "clicks" },
  queries_total: { label: "Suchanfragen", kind: "count" },
  totals: { label: "Search Console gesamt", kind: "searchTotals" },
  tracked_phrases: { label: "Verfolgte Suchbegriffe", kind: "terms" },
  sessions: { label: "Besuche", kind: "count" },
  engaged_sessions: { label: "Besuche mit Interaktion", kind: "count" },
  key_events: { label: "Anfragen (Zielereignisse)", kind: "count" },
  reviews: { label: "Bewertungen", kind: "count" },
  replied_within_days: { label: "Rechtzeitig beantwortet", kind: "count" },
  reply_share_in_time: { label: "Anteil rechtzeitig", kind: "share" },
  reply_days: { label: "Frist", kind: "days" },
  chat_tools: { label: "Chatfenster", kind: "terms" },
  messenger_links: { label: "Links zu Messengern", kind: "terms" },
  analytics_tools: { label: "Besucherzählung", kind: "terms" },
  tag_ids: { label: "Kennungen der Google-Einbindung", kind: "terms" },
  ads_found: { label: "Google-Werbung eingebunden", kind: "yesno" },
  ads_ids: { label: "Kennungen der Google-Werbung", kind: "terms" },
  conversion_tracking: { label: "Anfragen aus Werbung werden gezählt", kind: "yesno" },
  conversions: { label: "Gezählte Anfragen aus Werbung", kind: "terms" },
  start_page: { label: "Geprüfte Startseite", kind: "url" },
};

/** Words the scorer writes as values: missing fields, flags, statuses, providers, metrics. */
const TERMS: Readonly<Record<string, string>> = {
  name: "Name",
  street: "Straße",
  postal_code: "Postleitzahl",
  city: "Ort",
  phone: "Telefon",
  email: "E-Mail",
  address: "Adresse",
  phone_or_email: "Telefon oder E-Mail",
  responsible: "Verantwortliche Person",
  title: "Titel",
  description: "Beschreibung",
  image: "Bild",
  telephone: "Telefon",
  url: "Website",
  geo: "Koordinaten",
  openingHours: "Öffnungszeiten",
  openingHoursSpecification: "Öffnungszeiten",
  "@context": "Angabe schema.org",
  "@type": "Typangabe",
  google: "Google-Profil",
  directories: "Verzeichnis oder Netzwerk",
  wikidata: "Wikidata",
  perfect_rating_many_reviews: "glatte 5,0 bei vielen Bewertungen",
  empty_five_star_reviews: "auffällig viele Fünf-Sterne-Bewertungen ohne Text",
  OPERATIONAL: "geöffnet",
  CLOSED_TEMPORARILY: "vorübergehend geschlossen",
  CLOSED_PERMANENTLY: "dauerhaft geschlossen",
  places: "öffentliche Stichprobe von Google",
  profile: "eigenes Google-Profil",
  browser: "neueste Bewertungen auf Google Maps",
  places_api: "Stichprobe der Google-Schnittstelle",
  maps_result_list: "allen Betrieben der Kartenliste bei Google Maps",
  competitor_profiles: "den drei stärksten hinterlegten Wettbewerbern",
  chatgpt: "ChatGPT",
  claude: "Claude",
  gemini: "Gemini",
  perplexity: "Perplexity",
  BUSINESS_IMPRESSIONS_DESKTOP_MAPS: "Aufrufe Karte am Computer",
  BUSINESS_IMPRESSIONS_MOBILE_MAPS: "Aufrufe Karte am Handy",
  BUSINESS_IMPRESSIONS_DESKTOP_SEARCH: "Aufrufe Suche am Computer",
  BUSINESS_IMPRESSIONS_MOBILE_SEARCH: "Aufrufe Suche am Handy",
  CALL_CLICKS: "Anrufe",
  WEBSITE_CLICKS: "Website-Klicks",
  BUSINESS_DIRECTION_REQUESTS: "Routenanfragen",
  BUSINESS_CONVERSATIONS: "Nachrichten",
  BUSINESS_BOOKINGS: "Buchungen",
};

/** Sources with a switch and a daily cap, as named on the settings page. */
const SOURCES: Readonly<Record<string, string>> = {
  places: "Google Places API",
  google_search: "Google-Suche",
  google_maps: "Google Maps",
  dataforseo: "DataForSEO",
  chatgpt: "ChatGPT",
  claude: "Claude",
  gemini: "Gemini",
  perplexity: "Perplexity",
};

/** Why a check could not be measured, by the reason code of the scorer. */
const REASONS: Readonly<Record<string, string>> = {
  no_website: "Keine Website gefunden oder nicht erreichbar.",
  no_pagespeed: "Google PageSpeed hat keine Werte geliefert.",
  no_place: "Kein Google-Unternehmensprofil gefunden.",
  collector_failed: "Der Abruf ist fehlgeschlagen.",
  manual: "Nicht öffentlich abrufbar, bitte von Hand prüfen.",
  no_competitors: "Keine Wettbewerber hinterlegt.",
  no_access: "Ohne Kundenzugang nicht messbar.",
  no_paid_data: "Braucht den Bezahldienst, der ausgeschaltet ist.",
  paid_disabled: "Braucht den Bezahldienst, der ausgeschaltet ist.",
  needs_error_page: "Die Fehlerseite ließ sich nicht testen.",
  needs_history: "Braucht den Verlauf mehrerer Prüfungen.",
  too_few_pages: "Zu wenige Seiten zum Vergleichen.",
  no_subpages: "Keine Unterseiten gefunden.",
  no_search_phrases: "Keine Suchbegriffe hinterlegt.",
  no_volumes: "Für die Suchbegriffe ist keine Nachfrage bekannt.",
  no_service_pages: "Keine Leistungsseiten gefunden.",
  no_questions: "Google hat keine Fragen unter „Nutzer fragen auch“ gezeigt.",
  no_city: "Der Ort ist unbekannt.",
  no_keyword_gap: "Keine Suchbegriffe der Wettbewerber verfügbar.",
  no_footer_nap: "Keine Firmendaten im Fußbereich der Website.",
  no_profile_nap: "Im Google-Profil fehlen Telefon und Adresse.",
  no_address: "Die Adresse ist unbekannt.",
  no_start_page: "Die Startseite war nicht erreichbar.",
  no_faq_sections: "Keine Seite mit Fragen und Antworten gefunden.",
  no_structured_data: "Keine strukturierten Daten gefunden.",
  no_reviews: "Keine Bewertungen vorhanden.",
  no_review_dates: "Die Bewertungen haben kein Datum.",
  no_negative_reviews: "Keine kritischen Bewertungen vorhanden.",
  no_review_texts: "Keine Bewertungen mit Text vorhanden.",
  no_keywords: "Keine Suchwörter zum Abgleich vorhanden.",
  no_review_platforms_checked: "Keine Bewertungsportale geprüft.",
  directory_not_checked: "Das Verzeichnis wurde nicht geprüft.",
  directories_not_checked: "Die Verzeichnisse wurden nicht geprüft.",
  no_listing_data: "Keine Einträge zum Vergleichen gefunden.",
  no_backlink_data: "Keine Linkdaten verfügbar.",
  no_competitor_backlinks: "Keine Linkdaten der Wettbewerber verfügbar.",
  short_history: "Der Verlauf ist kürzer als zwölf Monate.",
  no_business_name: "Der Firmenname ist unbekannt.",
  no_brand_search: "Die Suche nach dem Firmennamen fehlt.",
  no_serps: "Keine Suchergebnisse erfasst.",
  no_local_pack: "Google hat keinen Kartenblock gezeigt.",
  no_grid: "Das Kartenraster wurde nicht gemessen.",
  description_not_read: "Die Beschreibung steht nur auf Google Maps, das diesmal nicht lesbar war.",
  services_not_read: "Die Leistungen stehen nur auf Google Maps, das diesmal nicht lesbar war.",
  products_not_read: "Die Produkte stehen nur auf Google Maps, das diesmal nicht lesbar war.",
  posts_not_read: "Die Beiträge stehen nur auf Google Maps, das diesmal nicht lesbar war.",
  questions_not_shown: "Google Maps zeigt für dieses Profil keine Fragen und Antworten.",
  service_areas_not_read: "Das Einzugsgebiet steht nur auf Google Maps, das diesmal nicht lesbar war.",
  attributes_not_read: "Die Merkmale stehen nur auf Google Maps, das diesmal nicht lesbar war.",
  categories_not_read: "Google Maps zeigt nur die Hauptkategorie; die weiteren liefert nur die Google-Schnittstelle.",
  mixed_category_sources: "Kategorien von Google Maps und der Google-Schnittstelle lassen sich nicht vergleichen.",
  no_ai_overview: "Google hat keine KI-Übersicht gezeigt.",
  no_bing_serp: "Keine Bing-Suche erfasst.",
  no_image_serp: "Keine Bildersuche erfasst.",
  no_ranked_keywords: "Keine Platzierungsdaten verfügbar.",
  no_competitor_keywords: "Keine Platzierungsdaten der Wettbewerber verfügbar.",
  no_recommendation_answers: "Keine Antworten auf Empfehlungsfragen erfasst.",
  no_problem_answers: "Keine Antworten auf Problemfragen erfasst.",
  no_reference_facts: "Telefon und Postleitzahl sind unbekannt.",
  no_checkable_answers: "Keine Antwort nannte Telefon oder Postleitzahl.",
  no_cited_sources: "Die Assistenten haben keine Quellen genannt.",
  legal_notice_not_crawled: "Das Impressum war nicht abrufbar.",
  privacy_not_crawled: "Die Datenschutzerklärung war nicht abrufbar.",
  no_analytics: "Kein Zugang zu Google Analytics.",
  no_search_console: "Kein Zugang zur Search Console.",
  no_profile_metrics: "Keine Kennzahlen aus dem Google-Profil.",
  no_owner_reviews: "Keine Bewertungen im eigenen Profil.",
  photos_not_read: "Fotos bei Google Maps nicht gelesen.",
  no_dated_photos: "Kein Foto mit Datum gefunden.",
  scripts_not_recorded: "Startseite wurde vor dieser Prüfung erfasst; neu prüfen.",
  no_listed_businesses: "Keine Betriebe in der Kartenliste gefunden.",
  no_review_counts: "Bewertungszahlen der Betriebe nicht lesbar.",
};

const SEARCH_TOTALS: readonly (readonly [string, string, (value: number) => string])[] = [
  ["clicks", "Klicks", (value) => integer.format(value)],
  ["impressions", "Einblendungen", (value) => integer.format(value)],
  ["ctr", "Klickrate", (value) => percent(value)],
  ["position", "Platz im Schnitt", (value) => oneDecimal.format(value)],
];

const GRID_KEY = /^r\d+c\d+$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function percent(share: number): string {
  return `${oneDecimal.format(share * 100)} %`;
}

function term(value: string): string {
  return TERMS[value] ?? value;
}

/** A known reason code in German; text the worker already wrote in German is kept as it is. */
export function reasonText(reason: string): string {
  return REASONS[reason] ?? (/^[a-z0-9_]+$/.test(reason) ? "Nicht messbar." : reason);
}

/** Address without scheme and "www.", the start page as its host, any other page as its path. */
export function shortUrl(value: string): string {
  const scheme = /^(tel|mailto):/i.exec(value);
  if (scheme !== null) return decodeURIComponent(value.slice(scheme[0].length));
  try {
    const url = new URL(value.includes("://") ? value : `https://${value}`);
    const host = url.hostname.replace(/^www\./, "");
    const path = decodeURIComponent(url.pathname).replace(/\/$/, "");
    return path === "" ? host : path;
  } catch {
    return value;
  }
}

function fullUrl(value: string): string {
  try {
    const url = new URL(value);
    return `${url.hostname.replace(/^www\./, "")}${decodeURIComponent(url.pathname).replace(/\/$/, "")}`;
  } catch {
    return value;
  }
}

/** "a, b, c, d und 3 weitere"; null for an empty list. */
export function joinListed(items: readonly string[], separator = ", "): string | null {
  if (items.length === 0) return null;
  const shown = items.slice(0, LISTED).join(separator);
  const rest = items.length - LISTED;
  return rest > 0 ? `${shown} und ${String(rest)} weitere` : shown;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function entries(value: unknown): [string, unknown][] {
  return isRecord(value) ? Object.entries(value) : [];
}

function place(value: unknown): string {
  return isNumber(value) ? `Platz ${integer.format(value)}` : NOT_FOUND;
}

function signed(value: unknown): string {
  if (!isNumber(value)) return "–";
  return `${value > 0 ? "+" : ""}${oneDecimal.format(value)} %`;
}

function listing(name: string, value: unknown): string {
  if (!isRecord(value)) return name;
  if (value.found !== true) return `${name}: ${NOT_FOUND}`;
  const differs = strings(value.mismatches).map((field) => `${term(field)} weicht ab`);
  return `${name}: ${differs.length === 0 ? "gefunden, Daten stimmen" : `gefunden, ${differs.join(", ")}`}`;
}

function business(value: unknown): string | null {
  if (!isRecord(value) || typeof value.name !== "string" || value.name === "") return null;
  const count = value.review_count;
  if (!isNumber(count)) return value.name;
  return `${value.name}: ${integer.format(count)} ${count === 1 ? "Bewertung" : "Bewertungen"}`;
}

function pair(value: unknown): string | null {
  if (!Array.isArray(value)) return null;
  const [first, second, similarity] = value as unknown[];
  if (typeof first !== "string" || typeof second !== "string") return null;
  const share = isNumber(similarity) ? ` (${percent(similarity)} gleich)` : "";
  return `${shortUrl(first)} und ${shortUrl(second)}${share}`;
}

function gap(value: unknown): string | null {
  if (!isRecord(value) || typeof value.phrase !== "string") return null;
  const details = [isNumber(value.volume) ? `${integer.format(value.volume)} Suchen im Monat` : ""];
  if (typeof value.competitor_domain === "string") {
    const where = isNumber(value.competitor_position) ? ` auf Platz ${integer.format(value.competitor_position)}` : "";
    details.push(`${shortUrl(value.competitor_domain)}${where}`);
  }
  const shown = details.filter(Boolean).join(", ");
  return shown === "" ? value.phrase : `${value.phrase} (${shown})`;
}

function schemaError(value: unknown): string | null {
  if (!isRecord(value) || typeof value.problem !== "string") return null;
  const page = typeof value.url === "string" ? `${shortUrl(value.url)}: ` : "";
  const type = typeof value.type === "string" && value.type !== "" ? `${value.type} ohne ` : "fehlt ";
  return `${page}${type}${term(value.problem)}`;
}

function mapped(value: unknown, show: (entry: [string, unknown]) => string | null): string | null {
  return joinListed(entries(value).flatMap((entry) => show(entry) ?? []), " · ");
}

function listed(value: unknown, show: (item: unknown) => string | null, separator?: string): string | null {
  return Array.isArray(value) ? joinListed(value.flatMap((item: unknown) => show(item) ?? []), separator) : null;
}

const SCALAR: Partial<Record<Kind, (value: number) => string>> = {
  count: (value) => integer.format(value),
  number: (value) => oneDecimal.format(value),
  words: (value) => `${integer.format(value)} Wörter`,
  chars: (value) => `${integer.format(value)} Zeichen`,
  searches: (value) => `${integer.format(value)} Suchen im Monat`,
  ms: (value) => (value >= 1000 ? `${twoDecimals.format(value / 1000)} Sekunden` : `${integer.format(value)} Millisekunden`),
  decimal: (value) => twoDecimals.format(value),
  score: (value) => `${integer.format(value)} von 100`,
  share: (value) => percent(value),
  rating: (value) => `${oneDecimal.format(value)} Sterne`,
  days: (value) => `${oneDecimal.format(value)} ${value === 1 ? "Tag" : "Tage"}`,
  perMonth: (value) => twoDecimals.format(value),
  year: (value) => String(Math.round(value)),
};

function formatScalar(kind: Kind, value: unknown): string | null {
  const format = SCALAR[kind];
  if (format === undefined) return null;
  return isNumber(value) ? format(value) : null;
}

function formatText(kind: Kind, value: unknown): string | null {
  if (typeof value !== "string" || value.trim() === "") return null;
  switch (kind) {
    case "text":
      return value;
    case "term":
      return term(value);
    case "url":
      return fullUrl(value);
    case "reason":
      return reasonText(value);
    case "date":
      return formatDate(value.slice(0, 10));
    case "month":
      return /^\d{4}-\d{2}/.test(value) ? `${value.slice(5, 7)}/${value.slice(0, 4)}` : value;
    default:
      return null;
  }
}

function formatList(kind: Kind, value: unknown): string | null {
  switch (kind) {
    case "urls":
      return joinListed(strings(value).map(shortUrl));
    case "hosts":
      return joinListed(strings(value).map((host) => host.replace(/^www\./, "")));
    case "terms":
      return joinListed(strings(value).map(term));
    case "sources":
      return joinListed(strings(value).map((source) => SOURCES[source] ?? source));
    case "quotes":
      return joinListed(strings(value).map((text) => `„${text}“`), " · ");
    case "pairs":
      return listed(value, pair, " · ");
    case "gaps":
      return listed(value, gap, " · ");
    case "schemaErrors":
      return listed(value, schemaError, " · ");
    default:
      return null;
  }
}

const MAP_ENTRIES: Partial<Record<Kind, (entry: [string, unknown]) => string | null>> = {
  positions: ([key, position]) => `${key}: ${place(position)}`,
  counts: ([key, count]) => (isNumber(count) ? `${shortUrl(key)}: ${integer.format(count)}` : null),
  clicks: ([key, count]) => (isNumber(count) ? `${key}: ${integer.format(count)} Klicks` : null),
  shares: ([key, share]) => (isNumber(share) ? `${term(key)}: ${percent(share)}` : null),
  changes: ([key, change]) => `${term(key)}: ${signed(change)}`,
  metrics: ([key, count]) => (isNumber(count) ? `${term(key)}: ${integer.format(count)}` : null),
  nap: ([key, text]) => (typeof text === "string" && text !== "" ? `${term(key)}: ${text}` : null),
  urlTerms: ([url, missing]) => `${shortUrl(url)}: ${strings(missing).map(term).join(", ")} fehlt`,
  urlCounts: ([url, words]) => (isNumber(words) ? `${shortUrl(url)}: ${integer.format(words)} Wörter` : null),
  urlShares: ([url, share]) => (isNumber(share) ? `${shortUrl(url)}: ${percent(share)}` : null),
  listings: ([name, details]) => listing(name, details),
};

function formatSearchTotals(value: unknown): string | null {
  const totals = isRecord(value) ? value : {};
  const shown = SEARCH_TOTALS.flatMap(([key, label, format]) => {
    const total = totals[key];
    return isNumber(total) ? [`${label}: ${format(total)}`] : [];
  });
  return shown.length === 0 ? null : shown.join(" · ");
}

function formatMap(kind: Kind, value: unknown): string | null {
  if (kind === "searchTotals") return formatSearchTotals(value);
  if (kind === "positions" && entries(value).some(([key]) => GRID_KEY.test(key))) return null;
  const entry = MAP_ENTRIES[kind];
  return entry === undefined ? null : mapped(value, entry);
}

function formatValue(kind: Kind, value: unknown): string | null {
  switch (kind) {
    case "hidden":
      return null;
    case "yesno":
      return typeof value === "boolean" ? (value ? "ja" : "nein") : null;
    case "manual":
      return typeof value === "boolean" ? (value ? "ja" : "nein, bitte von Hand prüfen") : null;
    case "found":
      if (typeof value === "boolean") return value ? "ja" : "nein";
      return joinListed(strings(value).map(term)) ?? "nirgends";
    case "place":
      return place(value);
    case "businesses":
      return isNumber(value) ? integer.format(value) : listed(value, business, " · ");
    case "change":
      return signed(value);
    default:
      return formatScalar(kind, value) ?? formatText(kind, value) ?? formatList(kind, value) ?? formatMap(kind, value);
  }
}

/** Lines for the known keys with a value worth showing, in catalogue order of FIELDS. */
export function formatMeasured(measured: Readonly<Record<string, unknown>>): MeasuredLine[] {
  return Object.entries(FIELDS).flatMap(([key, field]) => {
    if (!(key in measured)) return [];
    const value = formatValue(field.kind, measured[key]);
    return value === null ? [] : [{ key, label: field.label, value }];
  });
}

export interface ReviewExample {
  /** Day of the review as TT.MM.JJJJ, or Google's wording such as "vor 2 Wochen"; null if neither. */
  when: string | null;
  /** Star rating 1 to 5; null when the review carries none. */
  stars: number | null;
  text: string;
}

function reviewWhen(value: unknown): string | null {
  if (typeof value !== "string" || value.trim() === "") return null;
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? formatDate(value) : value.trim();
}

function reviewStars(value: unknown): number | null {
  return isNumber(value) && value >= 0 && value <= 5 ? value : null;
}

/** "2 von 5 Sternen", for screen readers next to the gold stars. */
export function starsLabel(stars: number): string {
  return `${oneDecimal.format(stars)} von 5 Sternen`;
}

/** The newest reviews without an owner reply that g4 lists, ready for small cards. */
export function unansweredReviews(measured: Readonly<Record<string, unknown>>): ReviewExample[] {
  const examples = measured.unanswered_examples;
  if (!Array.isArray(examples)) return [];
  return examples.flatMap((item: unknown) => {
    if (!isRecord(item)) return [];
    const text = typeof item.text === "string" ? item.text.trim() : "";
    const when = reviewWhen(item.date);
    const stars = reviewStars(item.stars);
    if (text === "" && when === null && stars === null) return [];
    return [{ when, stars, text }];
  });
}

export const MEASURED_KEYS: readonly string[] = Object.keys(FIELDS);
export const REASON_CODES: readonly string[] = Object.keys(REASONS);
