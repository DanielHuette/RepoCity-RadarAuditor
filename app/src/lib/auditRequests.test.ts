import { describe, expect, it } from "vitest";
import type { OpenAuditRequest } from "../api/types";
import { auditWebsite, formatWish, meetingLabel, openRequestsLabel, sortByWish } from "./auditRequests";

function request(overrides: Partial<OpenAuditRequest>): OpenAuditRequest {
  return {
    id: "r1",
    customer_id: "c1",
    company: "Dachdeckerei Muster",
    contact: "Erika Muster",
    website: "https://dachdeckerei-muster.de/",
    kind: "visit",
    wished_at: "2026-11-03T08:00:00+00:00",
    status: "open",
    created_at: "2026-10-30T10:00:00+00:00",
    ...overrides,
  };
}

describe("meetingLabel", () => {
  it("names the three kinds of meeting as the form does", () => {
    expect(["visit", "call", "video"].map(meetingLabel)).toEqual(["Vor Ort", "Telefon", "Video"]);
  });

  it("shows an unknown kind as it is", () => {
    expect(meetingLabel("fax")).toBe("fax");
  });
});

describe("formatWish", () => {
  it("writes weekday, date and time in German time", () => {
    expect(formatWish("2026-11-03T08:00:00+00:00")).toBe("Dienstag, 03.11.2026, 09:00 Uhr");
    expect(formatWish("2026-07-01T06:00:00Z")).toBe("Mittwoch, 01.07.2026, 08:00 Uhr");
  });

  it("says when there is no wish or it cannot be read", () => {
    expect(formatWish(null)).toBe("ohne Wunschtermin");
    expect(formatWish("kaputt")).toBe("ohne Wunschtermin");
  });
});

describe("auditWebsite", () => {
  it("gives the address the Prüfen tab can check", () => {
    expect(auditWebsite(request({ website: "dachdeckerei-muster.de" }))).toBe("https://dachdeckerei-muster.de/");
    expect(auditWebsite(request({}))).toBe("https://dachdeckerei-muster.de/");
  });

  it("has none without a usable website", () => {
    expect(auditWebsite(request({ website: null }))).toBeNull();
    expect(auditWebsite(request({ website: "  " }))).toBeNull();
    expect(auditWebsite(request({ website: "muster" }))).toBeNull();
  });
});

describe("sortByWish", () => {
  it("puts the earliest wish first and requests without a wish last", () => {
    const later = request({ id: "later", wished_at: "2026-11-05T08:00:00Z" });
    const sooner = request({ id: "sooner", wished_at: "2026-11-03T08:00:00Z" });
    const none = request({ id: "none", wished_at: null });
    expect(sortByWish([none, later, sooner]).map((entry) => entry.id)).toEqual(["sooner", "later", "none"]);
  });
});

describe("openRequestsLabel", () => {
  it("counts in singular and plural", () => {
    expect(openRequestsLabel(1)).toBe("1 offene Anfrage");
    expect(openRequestsLabel(3)).toBe("3 offene Anfragen");
  });
});
