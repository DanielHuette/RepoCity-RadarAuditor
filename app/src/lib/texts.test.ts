import { describe, expect, it } from "vitest";
import { TextsFormatError, isMissingObject, parseAuditTexts } from "./texts";

const REPORT_PROSE = {
  introduction: "Einleitung",
  summary: "Zusammenfassung",
  forecast_note: "Schätzung",
  subscription_reason: "Pflege",
  offer_text: "Angebot",
};

describe("parseAuditTexts", () => {
  it("reads posts and review replies and ignores the report prose", () => {
    const content = JSON.stringify({
      ...REPORT_PROSE,
      post_suggestions: ["Neues Dach vor dem Winter?", "Wir sind 24 Stunden erreichbar."],
      review_replies: [{ review_excerpt: "Schnell und sauber", reply: "Vielen Dank für Ihre Bewertung!" }],
    });
    expect(parseAuditTexts(content)).toEqual({
      talk: null,
      posts: ["Neues Dach vor dem Winter?", "Wir sind 24 Stunden erreichbar."],
      replies: [{ reviewExcerpt: "Schnell und sauber", reply: "Vielen Dank für Ihre Bewertung!" }],
    });
  });

  it("defaults missing lists to empty", () => {
    expect(parseAuditTexts(JSON.stringify(REPORT_PROSE))).toEqual({ talk: null, posts: [], replies: [] });
  });

  it("reads the conversation text for the customer, trimmed", () => {
    const talk = " Sie stehen auf der Karte hinter drei Nachbarn. Der größte Hebel sind die Bewertungen. ";
    expect(parseAuditTexts(JSON.stringify({ ...REPORT_PROSE, talk_text: talk })).talk).toBe(
      "Sie stehen auf der Karte hinter drei Nachbarn. Der größte Hebel sind die Bewertungen.",
    );
  });

  it("has no conversation text for older audits or a blank one, and rejects a wrong type", () => {
    expect(parseAuditTexts(JSON.stringify({ ...REPORT_PROSE, talk_text: "  " })).talk).toBeNull();
    expect(parseAuditTexts(JSON.stringify({ ...REPORT_PROSE, talk_text: null })).talk).toBeNull();
    expect(() => parseAuditTexts(JSON.stringify({ talk_text: ["Satz"] }))).toThrow("bei talk_text");
  });

  it("leaves out blank posts", () => {
    expect(parseAuditTexts(JSON.stringify({ post_suggestions: ["  ", "Beitrag"] })).posts).toEqual(["Beitrag"]);
  });

  it("rejects malformed JSON and a non-object root", () => {
    expect(() => parseAuditTexts("{")).toThrow(TextsFormatError);
    expect(() => parseAuditTexts("[]")).toThrow("texte.json ist ungültig bei <root>");
  });

  it("rejects a list field of the wrong type", () => {
    expect(() => parseAuditTexts(JSON.stringify({ post_suggestions: "Beitrag" }))).toThrow("bei post_suggestions");
    expect(() => parseAuditTexts(JSON.stringify({ review_replies: null }))).toThrow("bei review_replies");
    expect(() => parseAuditTexts(JSON.stringify({ post_suggestions: [42] }))).toThrow("bei post_suggestions.0");
  });

  it("names the broken field of a review reply", () => {
    expect(() => parseAuditTexts(JSON.stringify({ review_replies: ["Danke"] }))).toThrow("bei review_replies.0");
    expect(() => parseAuditTexts(JSON.stringify({ review_replies: [{ review_excerpt: "Gut", reply: " " }] }))).toThrow(
      "bei review_replies.0.reply",
    );
    expect(() => parseAuditTexts(JSON.stringify({ review_replies: [{ reply: "Danke" }] }))).toThrow(
      "bei review_replies.0.review_excerpt",
    );
  });
});

describe("isMissingObject", () => {
  it("recognises the not-found answers of the storage service", () => {
    expect(isMissingObject({ status: 400, statusCode: "404" })).toBe(true);
    expect(isMissingObject({ status: 404 })).toBe(true);
    expect(isMissingObject({ code: "NoSuchKey" })).toBe(true);
  });

  it("treats other failures as real errors", () => {
    expect(isMissingObject({ status: 403, statusCode: "403", code: "AccessDenied" })).toBe(false);
    expect(isMissingObject({})).toBe(false);
  });
});
