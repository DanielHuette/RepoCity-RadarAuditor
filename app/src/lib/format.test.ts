import { describe, expect, it } from "vitest";
import { formatDate, formatEuro, hostOf, normaliseWebsite, parseAmount } from "./format";

describe("parseAmount", () => {
  it("reads German and plain notation", () => {
    expect(parseAmount("1.234,56")).toBe(1234.56);
    expect(parseAmount("490,5")).toBe(490.5);
    expect(parseAmount("490.50")).toBe(490.5);
    expect(parseAmount("149 €")).toBe(149);
  });

  it("rejects text, empty input and more than two decimals", () => {
    expect(parseAmount("")).toBeNull();
    expect(parseAmount("viel")).toBeNull();
    expect(parseAmount("1,234")).toBeNull();
  });
});

describe("normaliseWebsite", () => {
  it("adds https to a bare domain", () => {
    expect(normaliseWebsite("dachdecker-muster.de")).toBe("https://dachdecker-muster.de/");
  });

  it("keeps an explicit scheme", () => {
    expect(normaliseWebsite("http://dachdecker-muster.de/kontakt")).toBe("http://dachdecker-muster.de/kontakt");
  });

  it("rejects empty input and names without a domain", () => {
    expect(normaliseWebsite("  ")).toBeNull();
    expect(normaliseWebsite("dachdecker")).toBeNull();
  });
});

describe("display formats", () => {
  it("formats euros and dates in German", () => {
    expect(formatEuro(1234.5).replace(/\s/g, " ")).toBe("1.234,50 €");
    expect(formatEuro(null)).toBe("–");
    expect(formatDate("2026-10-16")).toBe("16.10.2026");
    expect(formatDate(null)).toBe("–");
  });

  it("extracts the host of a website", () => {
    expect(hostOf("https://dachdecker-muster.de/")).toBe("dachdecker-muster.de");
    expect(hostOf(null)).toBe("");
  });
});
