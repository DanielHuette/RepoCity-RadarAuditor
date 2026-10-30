import { describe, expect, it } from "vitest";
import { formatFixPayload } from "./clipboard";

describe("formatFixPayload", () => {
  it("returns null without payload", () => {
    expect(formatFixPayload(null)).toBeNull();
    expect(formatFixPayload({})).toBeNull();
  });

  it("prefers a code field as it is", () => {
    const code = '<script type="application/ld+json">{}</script>';
    expect(formatFixPayload({ code, text: "Erklärung" })).toBe(code);
  });

  it("uses a text field when there is no code", () => {
    expect(formatFixPayload({ text: "Dachdecker in Münster | Muster GmbH", kind: "title" })).toBe("Dachdecker in Münster | Muster GmbH");
  });

  it("pretty-prints an object in the code field", () => {
    expect(formatFixPayload({ code: { "@type": "RoofingContractor" } })).toBe('{\n  "@type": "RoofingContractor"\n}');
  });

  it("falls back to the whole payload as indented JSON", () => {
    expect(formatFixPayload({ title: "A", description: "B" })).toBe('{\n  "title": "A",\n  "description": "B"\n}');
  });

  it("skips blank text fields", () => {
    expect(formatFixPayload({ text: "  ", title: "A" })).toBe('{\n  "text": "  ",\n  "title": "A"\n}');
  });
});
