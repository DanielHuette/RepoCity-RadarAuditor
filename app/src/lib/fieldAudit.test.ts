import { describe, expect, it } from "vitest";
import { quickAuditRequest, validateCorrection, validateWebsite } from "./fieldAudit";

describe("validateWebsite", () => {
  it("accepts addresses with and without https and normalises them", () => {
    expect(validateWebsite("Dachdeckerei-Muster.de")).toEqual({ ok: true, website: "https://dachdeckerei-muster.de/" });
    expect(validateWebsite(" https://www.muster.de/kontakt ")).toEqual({ ok: true, website: "https://www.muster.de/kontakt" });
    expect(validateWebsite("http://muster.de")).toEqual({ ok: true, website: "http://muster.de/" });
  });

  it("asks for an address when it is empty or not one", () => {
    expect(validateWebsite("  ")).toEqual({ ok: false, error: "Bitte die Website des Betriebs eingeben." });
    expect(validateWebsite("muster")).toEqual({ ok: false, error: "Bitte eine Adresse wie meisterbetrieb-muster.de eingeben." });
  });
});

describe("quickAuditRequest", () => {
  it("sends only the website and the checks switched off", () => {
    const disabled = ["m1", "m2"];
    const request = quickAuditRequest("https://muster.de/", disabled);
    expect(request).toEqual({ p_website: "https://muster.de/", p_disabled_checks: ["m1", "m2"] });
    expect(Object.keys(request)).toEqual(["p_website", "p_disabled_checks"]);
    expect(request.p_disabled_checks).not.toBe(disabled);
  });
});

describe("validateCorrection", () => {
  it("adds trimmed name, city and industry to the same website and selection", () => {
    expect(
      validateCorrection({ name: " Dachdeckerei Muster GmbH ", city: "Münster ", industry: "bauhandwerk" }, "https://muster.de/", ["m1"]),
    ).toEqual({
      ok: true,
      request: {
        p_website: "https://muster.de/",
        p_disabled_checks: ["m1"],
        p_name: "Dachdeckerei Muster GmbH",
        p_city: "Münster",
        p_industry: "bauhandwerk",
      },
    });
  });

  it("names every missing field", () => {
    expect(validateCorrection({ name: " ", city: "", industry: "" }, "https://muster.de/", [])).toEqual({
      ok: false,
      errors: {
        name: "Bitte den Namen des Betriebs eingeben.",
        city: "Bitte den Ort eingeben.",
        industry: "Bitte eine Branche wählen.",
      },
    });
  });
});
