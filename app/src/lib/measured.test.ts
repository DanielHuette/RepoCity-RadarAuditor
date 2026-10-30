import { describe, expect, it } from "vitest";
import { formatMeasured, joinListed, MEASURED_KEYS, REASON_CODES, reasonText, shortUrl, starsLabel, unansweredReviews } from "./measured";

const SCORER_SOURCES = import.meta.glob<string>(
  ["../../../src/radarauditor/scoring/checks/*.py", "../../../src/radarauditor/scoring/evaluator.py"],
  { query: "?raw", import: "default", eager: true },
);

// String keys in the scorer that name something other than a measured value.
const NOT_MEASURED = new Set([
  // Field names inside nested values (NAP_KEYS, schema errors, sameAs targets), formatted with their parent.
  "name", "street", "phone", "address", "url", "type", "problem", "directories", "wikidata",
  // Problem groups of b2/b3, stored as pages_<group>, and the legal notice elements listed in missing_elements.
  "too_short", "too_long", "duplicate", "phone_or_email", "responsible",
  // Catalogue source names in evaluator.py's feed table and a field of the collected data.
  "places", "serp", "paid", "assistant_answers",
  // Sources named inside the routes object of a check, shown by routes.ts.
  "chatgpt", "gemini", "perplexity", "wayback",
  // Fields of one unanswered review in g4's unanswered_examples, shown as small cards.
  "date", "stars", "text",
  // A value of comparison_source compared in g_bewertungen.py (`== "maps_result_list":`), labelled in TERMS.
  "maps_result_list",
]);

function scorerSources(): string[] {
  return Object.values(SCORER_SOURCES);
}

function scorerKeys(): Set<string> {
  const keys = new Set<string>();
  for (const source of scorerSources()) {
    for (const match of source.matchAll(/"([a-z][a-z_]+)":/g)) keys.add(match[1] ?? "");
    for (const match of source.matchAll(/not_measurable\(\s*[^,)]+,\s*([a-z_]+)=/g)) keys.add(match[1] ?? "");
    for (const match of source.matchAll(/f"pages_\{key\}"/g)) keys.add(match[0]);
  }
  return keys;
}

function scorerReasons(): Set<string> {
  const reasons = new Set<string>();
  for (const source of scorerSources()) {
    for (const match of source.matchAll(/(?:not_measurable|_not_collected)\(\s*"([a-z_]+)"/g)) reasons.add(match[1] ?? "");
    for (const match of source.matchAll(/"reason": "([a-z_]+)"/g)) reasons.add(match[1] ?? "");
    for (const match of source.matchAll(/^NO_PAID_DATA = "([a-z_]+)"/gm)) reasons.add(match[1] ?? "");
  }
  return reasons;
}

describe("coverage of the scorer", () => {
  it("reads the scorer modules of the areas and the evaluator", () => {
    expect(Object.keys(SCORER_SOURCES).filter((path) => /\/[a-n]_\w+\.py$/.test(path)).length).toBeGreaterThanOrEqual(13);
    expect(Object.keys(SCORER_SOURCES).some((path) => path.endsWith("evaluator.py"))).toBe(true);
  });

  it("has a German label for every measured key the scorer writes", () => {
    const keys = [...scorerKeys()].filter((key) => !NOT_MEASURED.has(key));
    const lengthKeys = ["pages_missing", "pages_too_short", "pages_too_long", "pages_duplicate"];
    const expected = keys.flatMap((key) => (key === 'f"pages_{key}"' ? lengthKeys : [key]));
    expect(expected.filter((key) => !MEASURED_KEYS.includes(key))).toEqual([]);
  });

  it("explains every reason code the scorer writes", () => {
    expect([...scorerReasons()].filter((reason) => !REASON_CODES.includes(reason))).toEqual([]);
  });
});

describe("formatMeasured", () => {
  it("turns page speed values into plain German with units", () => {
    const lines = formatMeasured({ lcp_ms: 3120, inp_ms: 180, cls: 0.14, mobile_performance: 54, lcp_ms_max: 2500 });
    expect(lines.map((line) => [line.label, line.value])).toEqual([
      ["Hauptinhalt sichtbar nach", "3,12 Sekunden"],
      ["Ziel: Hauptinhalt nach höchstens", "2,5 Sekunden"],
      ["Verrutschen beim Laden (CLS)", "0,14"],
      ["Reaktion auf Tippen", "180 Millisekunden"],
      ["Tempo-Wertung Handy", "54 von 100"],
    ]);
  });

  it("writes yes and no, and asks for a manual check where the public data ends", () => {
    const lines = formatMeasured({ uses_https: true, redirects_to_single_host: false, ownership_checked: false });
    expect(lines.map((line) => line.value)).toEqual(["ja", "nein", "nein, bitte von Hand prüfen"]);
  });

  it("shows pages as paths and shortens long lists", () => {
    const [line] = formatMeasured({
      broken_urls: ["https://www.muster.de/", "https://muster.de/alt/", "https://muster.de/a", "https://muster.de/b", "https://muster.de/c"],
    });
    expect(line?.value).toBe("muster.de, /alt, /a, /b und 1 weitere");
  });

  it("translates field names, shares and ratings", () => {
    const lines = formatMeasured({ missing_elements: ["postal_code", "responsible"], reply_rate: 0.425, rating: 4.6 });
    expect(Object.fromEntries(lines.map((line) => [line.key, line.value]))).toEqual({
      missing_elements: "Postleitzahl, Verantwortliche Person",
      reply_rate: "42,5 %",
      rating: "4,6 Sterne",
    });
  });

  it("lists positions per search phrase and marks missing ones", () => {
    const [line] = formatMeasured({ positions: { "Dachdecker Münster": 7, Dachsanierung: null } });
    expect(line?.value).toBe("Dachdecker Münster: Platz 7 · Dachsanierung: nicht gefunden");
  });

  it("leaves out the grid positions, which the map grid already shows", () => {
    expect(formatMeasured({ positions: { r1c1: 3, r1c2: null }, points_total: 2 }).map((line) => line.key)).toEqual(["points_total"]);
  });

  it("describes directory listings with their differences", () => {
    const [line] = formatMeasured({
      listings: { "Bing Places": { found: true, mismatches: ["phone"] }, "Apple Maps": { found: false, mismatches: [] } },
    });
    expect(line?.value).toBe("Bing Places: gefunden, Telefon weicht ab · Apple Maps: nicht gefunden");
  });

  it("formats nested structures of content, schema and access checks", () => {
    const lines = formatMeasured({
      duplicate_pairs: [["https://muster.de/a", "https://muster.de/b", 0.91]],
      top_gap_keywords: [{ phrase: "Flachdach", volume: 320, competitor_domain: "aasee.de", competitor_position: 2 }],
      errors: [{ url: "https://muster.de/", type: "LocalBusiness", problem: "telephone" }],
      totals: { clicks: 120, impressions: 4300, ctr: 0.028, position: 11.4 },
      change_percent: { CALL_CLICKS: 12.5, WEBSITE_CLICKS: -4 },
    });
    expect(Object.fromEntries(lines.map((line) => [line.key, line.value]))).toEqual({
      duplicate_pairs: "/a und /b (91 % gleich)",
      top_gap_keywords: "Flachdach (320 Suchen im Monat, aasee.de auf Platz 2)",
      errors: "muster.de: LocalBusiness ohne Telefon",
      change_percent: "Anrufe: +12,5 % · Website-Klicks: -4 %",
      totals: "Klicks: 120 · Einblendungen: 4.300 · Klickrate: 2,8 % · Platz im Schnitt: 11,4",
    });
  });

  it("puts the reason first and skips unknown keys and empty values", () => {
    const lines = formatMeasured({ sitemap_count: 0, reason: "no_place", internal_debug: 3, start_page_h1: "", city: null });
    expect(lines.map((line) => [line.label, line.value])).toEqual([
      ["Grund", "Kein Google-Unternehmensprofil gefunden."],
      ["Sitemaps", "0"],
    ]);
  });

  it("returns nothing for an empty measurement", () => {
    expect(formatMeasured({})).toEqual([]);
  });
});

describe("photo age, website tools and the Maps comparison", () => {
  it("describes the newest photo of f15 and the manual logo check of f8", () => {
    const lines = formatMeasured({
      newest_photo_date: "2026-03-14",
      newest_photo_days: 224,
      photos_with_date: 18,
      max_age_days: 90,
      logo_and_cover_checked: false,
    });
    expect(lines.map((line) => [line.label, line.value])).toEqual([
      ["Logo und Titelbild geprüft", "nein, bitte von Hand prüfen"],
      ["Neuestes Foto aufgenommen am", "14.03.2026"],
      ["Neuestes Foto ist alt", "224 Tage"],
      ["Fotos mit Aufnahmedatum", "18"],
      ["Ziel: höchstens so alt", "90 Tage"],
    ]);
  });

  it("names the tools found on the start page for n1 to n3", () => {
    const lines = formatMeasured({
      chat_tools: ["Tawk.to"],
      messenger_links: ["WhatsApp"],
      analytics_tools: ["Google Analytics", "Google Tag Manager"],
      tag_ids: ["G-ABC123", "GTM-XYZ"],
      ads_found: true,
      ads_ids: ["AW-123"],
      conversions: [],
      conversion_tracking: false,
      start_page: "https://www.muster.de/",
    });
    expect(lines.map((line) => [line.label, line.value])).toEqual([
      ["Chatfenster", "Tawk.to"],
      ["Links zu Messengern", "WhatsApp"],
      ["Besucherzählung", "Google Analytics, Google Tag Manager"],
      ["Kennungen der Google-Einbindung", "G-ABC123, GTM-XYZ"],
      ["Google-Werbung eingebunden", "ja"],
      ["Kennungen der Google-Werbung", "AW-123"],
      ["Anfragen aus Werbung werden gezählt", "nein"],
      ["Geprüfte Startseite", "muster.de"],
    ]);
  });

  it("explains the Maps result list of g1, given as a number or as a list of businesses", () => {
    const counted = formatMeasured({
      compared_businesses: 7,
      comparison_source: "maps_result_list",
      phrase: "Dachdecker Münster",
      without_review_count: ["Bedachungen Kröger"],
    });
    expect(counted.map((line) => [line.label, line.value])).toEqual([
      ["Verglichene Betriebe", "7"],
      ["Verglichen mit", "allen Betrieben der Kartenliste bei Google Maps"],
      ["Suchbegriff der Kartenliste", "Dachdecker Münster"],
      ["Ohne lesbare Bewertungszahl", "Bedachungen Kröger"],
    ]);
    const [listedLine] = formatMeasured({
      compared_businesses: [
        { name: "Dachtechnik Aasee", review_count: 112 },
        { name: "Kröger", review_count: 1 },
        { name: "Ohne Zahl", review_count: null },
      ],
    });
    expect(listedLine?.value).toBe("Dachtechnik Aasee: 112 Bewertungen · Kröger: 1 Bewertung · Ohne Zahl");
    expect(formatMeasured({ comparison_source: "competitor_profiles" })[0]?.value).toBe(
      "den drei stärksten hinterlegten Wettbewerbern",
    );
  });

  it("explains the new reasons in plain German", () => {
    expect(reasonText("photos_not_read")).toBe("Fotos bei Google Maps nicht gelesen.");
    expect(reasonText("no_dated_photos")).toBe("Kein Foto mit Datum gefunden.");
    expect(reasonText("scripts_not_recorded")).toBe("Startseite wurde vor dieser Prüfung erfasst; neu prüfen.");
    expect(reasonText("no_listed_businesses")).toBe("Keine Betriebe in der Kartenliste gefunden.");
    expect(reasonText("no_review_counts")).toBe("Bewertungszahlen der Betriebe nicht lesbar.");
  });
});

describe("unansweredReviews", () => {
  it("keeps the unanswered examples of g4 out of the lines and returns them as cards", () => {
    const measured = {
      replied: 4,
      unanswered_examples: [
        { date: "2026-10-02", stars: 2, text: " Termin wurde zweimal verschoben. " },
        { date: "vor 3 Wochen", stars: 5, text: "Sauber gearbeitet." },
        { date: null, stars: 1, text: "" },
        { date: null, stars: null, text: "" },
        "kaputt",
      ],
    };
    expect(formatMeasured(measured).map((line) => line.key)).toEqual(["replied"]);
    expect(unansweredReviews(measured)).toEqual([
      { when: "02.10.2026", stars: 2, text: "Termin wurde zweimal verschoben." },
      { when: "vor 3 Wochen", stars: 5, text: "Sauber gearbeitet." },
      { when: null, stars: 1, text: "" },
    ]);
    expect(starsLabel(2)).toBe("2 von 5 Sternen");
    expect(starsLabel(4.5)).toBe("4,5 von 5 Sternen");
  });

  it("keeps an out-of-range star value out", () => {
    expect(unansweredReviews({ unanswered_examples: [{ date: "2026-01-05", stars: 7, text: "Gut" }] })).toEqual([
      { when: "05.01.2026", stars: null, text: "Gut" },
    ]);
  });

  it("returns no cards without examples", () => {
    expect(unansweredReviews({})).toEqual([]);
    expect(unansweredReviews({ unanswered_examples: "nein" })).toEqual([]);
  });
});

describe("daily caps", () => {
  it("names the exhausted sources like the settings page", () => {
    const lines = formatMeasured({ reason: "Tageslimit erreicht", sources: ["places", "google_maps"] });
    expect(lines.map((line) => line.value)).toEqual(["Tageslimit erreicht", "Google Places API, Google Maps"]);
  });
});

describe("reasonText", () => {
  it("keeps German text from the worker and hides unknown codes", () => {
    expect(reasonText("Tageslimit erreicht")).toBe("Tageslimit erreicht");
    expect(reasonText("some_new_code")).toBe("Nicht messbar.");
  });
});

describe("shortUrl", () => {
  it("handles telephone and mail links and bare domains", () => {
    expect(shortUrl("tel:0251%20987650")).toBe("0251 987650");
    expect(shortUrl("mailto:info@muster.de")).toBe("info@muster.de");
    expect(shortUrl("aasee.de")).toBe("aasee.de");
  });
});

describe("joinListed", () => {
  it("returns null for no items and joins short lists completely", () => {
    expect(joinListed([])).toBeNull();
    expect(joinListed(["a", "b"])).toBe("a, b");
  });
});
