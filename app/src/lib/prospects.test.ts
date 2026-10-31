import { describe, expect, it } from "vitest";
import type { ProspectResult, ProspectSearch } from "../api/types";
import { formatDateTime } from "./format";
import {
  canAudit,
  validateCrmEntry,
  contactWays,
  isSearchWaiting,
  offeredContactWays,
  searchStatusText,
  validateSearch,
} from "./prospects";

const SEARCH: ProspectSearch = {
  id: "s1",
  trade: "Dachdecker",
  city: "Münster",
  status: "done",
  error: null,
  result_count: 37,
  requested_at: "2026-10-30T08:00:00Z",
  claimed_at: "2026-10-30T08:00:05Z",
  fetched_at: "2026-10-30T08:00:09Z",
};

function result(changes: Partial<ProspectResult> = {}): ProspectResult {
  return {
    id: "r1",
    search_id: "s1",
    rank: 1,
    place_id: "ChIJ-a",
    name: "Dachdeckerei Muster",
    address: "Hiltruper Str. 210, 48165 Münster, Deutschland",
    street: "Hiltruper Str. 210",
    postal_code: "48165",
    city: "Münster",
    phone: "0251 1",
    website: null,
    maps_url: "https://maps.google.com/?cid=1",
    rating: 4.1,
    review_count: 3,
    photo_count: 0,
    has_opening_hours: false,
    score: 7,
    signals: ["no_website", "no_photos"],
    reasons: "Keine Webseite im Profil und keine Fotos.",
    contact_ways: [],
    crm_customer_id: null,
    objected: false,
    ...changes,
  };
}

describe("validateSearch", () => {
  it("trims and collapses spaces", () => {
    expect(validateSearch("  Dach   decker ", " Münster ")).toEqual({ ok: true, trade: "Dach decker", city: "Münster" });
  });

  it("names each missing field", () => {
    expect(validateSearch("D", "")).toEqual({
      ok: false,
      errors: {
        trade: "Bitte ein Gewerk eingeben, zum Beispiel Dachdecker.",
        city: "Bitte die Stadt eingeben, zum Beispiel Münster.",
      },
    });
  });

  it("refuses texts longer than the database allows", () => {
    const result = validateSearch("x".repeat(81), "Münster");
    expect(result.ok).toBe(false);
  });
});

describe("searchStatusText", () => {
  it("says until when the list stays and costs nothing again", () => {
    expect(searchStatusText(SEARCH, 24)).toBe(
      `37 Betriebe gefunden am ${formatDateTime("2026-10-30T08:00:09Z")}. Bis ${formatDateTime("2026-10-31T08:00:09Z")} kostet dieselbe Suche nichts; danach löscht der Server die Liste, weil Google das Speichern nicht länger erlaubt.`,
    );
  });

  it("adds the note of an incomplete list", () => {
    const text = searchStatusText({ ...SEARCH, error: "Nur 1 von bis zu 3 Seiten gelesen." }, 24);
    expect(text.endsWith(" Nur 1 von bis zu 3 Seiten gelesen.")).toBe(true);
  });

  it.each([
    ["queued", "Wartet auf den Server."],
    ["running", "Der Server fragt Google ab …"],
    ["expired", "Die Liste ist gelöscht, weil Google das Speichern nur kurz erlaubt. Bitte neu suchen."],
  ] as const)("names the state %s", (status, text) => {
    expect(searchStatusText({ ...SEARCH, status }, 24)).toBe(text);
  });

  it("gives the reason of a failed search", () => {
    expect(searchStatusText({ ...SEARCH, status: "failed", error: "Tageslimit erreicht." }, 24)).toBe(
      "Suche gescheitert: Tageslimit erreicht.",
    );
  });

  it("waits only while the server works on it", () => {
    expect(isSearchWaiting({ ...SEARCH, status: "queued" })).toBe(true);
    expect(isSearchWaiting({ ...SEARCH, status: "running" })).toBe(true);
    expect(isSearchWaiting(SEARCH)).toBe(false);
  });
});

describe("contact ways", () => {
  const ways = [
    { channel: "letter", label: "Brief", status: "allowed", note: "Erlaubt." },
    { channel: "email", label: "E-Mail", status: "forbidden", note: "Nur mit Einwilligung." },
    { channel: "fax", label: "Fax", status: "maybe", note: "x" },
    "kaputt",
  ];

  it("keeps only well-formed ways", () => {
    expect(contactWays(ways).map((way) => way.channel)).toEqual(["letter", "email"]);
    expect(contactWays(null)).toEqual([]);
  });

  it("offers only lawful or uncertain ways, never the cold mail", () => {
    expect(offeredContactWays(result({ contact_ways: ways })).map((way) => way.label)).toEqual(["Brief"]);
  });

  it("offers nothing to a business that objected to advertising", () => {
    expect(offeredContactWays(result({ contact_ways: ways, objected: true }))).toEqual([]);
  });
});

describe("canAudit", () => {
  it("needs the website the audit starts from", () => {
    expect(canAudit(result())).toBe(false);
    expect(canAudit(result({ website: "https://muster.de/" }))).toBe(true);
  });
});

describe("validateCrmEntry", () => {
  it("needs the name Daniel copies from the public profile", () => {
    expect(validateCrmEntry({ name: " ", street: "", postalCode: "", city: "Münster", phone: "" })).toEqual({
      ok: false,
      error: "Bitte den Namen des Betriebs aus dem Google-Profil eintragen.",
    });
  });

  it("trims every field", () => {
    expect(
      validateCrmEntry({ name: " Bäckerei Korn ", street: " Ludgeristr. 1 ", postalCode: "48143", city: " Münster", phone: " 0251 1 " }),
    ).toEqual({
      ok: true,
      entry: { name: "Bäckerei Korn", street: "Ludgeristr. 1", postalCode: "48143", city: "Münster", phone: "0251 1" },
    });
  });
});
