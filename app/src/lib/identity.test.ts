import { describe, expect, it } from "vitest";
import { identifiedSource, identityFacts } from "./identity";

describe("identifiedSource", () => {
  it("reads the list the worker records, one label per source", () => {
    const raw = {
      identified: [
        { field: "name", value: "Malerbetrieb Kröger", source: "impressum" },
        { field: "city", value: "Münster", source: "impressum" },
        { field: "industry", value: "bauhandwerk", source: "google_types" },
        { field: "keywords", value: "Maler", source: "industry_services" },
      ],
    };
    expect(identifiedSource(raw)).toBe("Impressum, Google-Unternehmensprofil, Leistungen der Branche");
  });

  it("gives null for an empty list", () => {
    expect(identifiedSource({ identified: [] })).toBeNull();
  });

  it("names a single recorded source in plain German", () => {
    expect(identifiedSource({ identified: { name: "Muster", source: "impressum" } })).toBe("Impressum");
    expect(identifiedSource({ identification: { source: "google_profile" } })).toBe("Google-Unternehmensprofil");
  });

  it("joins the sources recorded per field without repeating one", () => {
    expect(identifiedSource({ identified: { sources: { name: "json_ld", city: "impressum", industry: "json_ld" } } })).toBe(
      "unsichtbare Angaben der Website, Impressum",
    );
  });

  it("keeps German text and leaves unknown codes out", () => {
    expect(identifiedSource({ identified: { source: "Startseite und Impressum" } })).toBe("Startseite und Impressum");
    expect(identifiedSource({ identified: { source: "some_new_code" } })).toBeNull();
  });

  it("returns null for audits without the record", () => {
    expect(identifiedSource({ website: {} })).toBeNull();
    expect(identifiedSource(null)).toBeNull();
    expect(identifiedSource({ identified: "impressum" })).toBeNull();
  });
});

describe("identityFacts", () => {
  it("lists business, city and industry group, and the source when known", () => {
    expect(identityFacts({ name: "Dachdeckerei Muster", city: "Münster", industry: "Handwerk" }, "Impressum")).toEqual([
      { label: "Betrieb", value: "Dachdeckerei Muster" },
      { label: "Ort", value: "Münster" },
      { label: "Branche", value: "Bauhandwerk" },
      { label: "Quelle", value: "Impressum" },
    ]);
  });

  it("marks what the server has not identified yet", () => {
    expect(identityFacts({ name: "muster.de", city: " ", industry: "" }, null)).toEqual([
      { label: "Betrieb", value: "muster.de" },
      { label: "Ort", value: "noch nicht erkannt" },
      { label: "Branche", value: "noch nicht erkannt" },
    ]);
  });
});
