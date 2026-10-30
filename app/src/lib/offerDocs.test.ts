import { describe, expect, it } from "vitest";
import { latestQuotePdf, pdfState, quoteIdOfAudit, quotePdfData, quotePdfName } from "./offerDocs";
import { doc } from "./testRows";

function request(created: string, data: Record<string, unknown>, filePath: string | null = null) {
  return doc({
    kind: "audit_quote",
    number: null,
    net_amount: null,
    vat_rate: null,
    gross_amount: null,
    created_at: created,
    data,
    file_path: filePath,
  });
}

describe("quotePdfData", () => {
  it("names audit, quote and quote number for the worker", () => {
    expect(quotePdfData("a1", "q1", "AN-2026-0003")).toEqual({ audit_id: "a1", quote_id: "q1", quote_number: "AN-2026-0003" });
  });
});

describe("latestQuotePdf and quoteIdOfAudit", () => {
  const older = request("2026-10-20T08:00:00Z", quotePdfData("a1", "q1", "AN-1"), "c/AN-1.pdf");
  const newer = request("2026-10-21T08:00:00Z", quotePdfData("a1", "q2", "AN-2"));
  const other = request("2026-10-22T08:00:00Z", quotePdfData("a2", "q3", "AN-3"));
  const plainQuote = doc({ kind: "quote", created_at: "2026-10-23T08:00:00Z", data: { quote_id: "q2", audit_id: "a2" } });
  const docs = [older, plainQuote, other, newer];

  it("finds the newest request of a quote and ignores other kinds", () => {
    expect(latestQuotePdf(docs, "q1")).toBe(older);
    expect(latestQuotePdf(docs, "q2")).toBe(newer);
    expect(latestQuotePdf(docs, "q9")).toBeNull();
  });

  it("finds the quote of the newest request for an audit", () => {
    expect(quoteIdOfAudit(docs, "a1")).toBe("q2");
    expect(quoteIdOfAudit(docs, "a2")).toBe("q3");
    expect(quoteIdOfAudit(docs, "a9")).toBeNull();
  });
});

describe("pdfState", () => {
  it("tells none, pending, failed and ready apart", () => {
    expect(pdfState(null)).toEqual({ kind: "none" });
    const pending = request("2026-10-21T08:00:00Z", { quote_id: "q1" });
    expect(pdfState(pending)).toEqual({ kind: "pending", doc: pending });
    const failed = request("2026-10-21T08:00:00Z", { quote_id: "q1", error: "Angebot nicht gefunden" });
    expect(pdfState(failed)).toEqual({ kind: "failed", doc: failed, error: "Angebot nicht gefunden" });
    const ready = request("2026-10-21T08:00:00Z", { quote_id: "q1" }, "c/AN-1.pdf");
    expect(pdfState(ready)).toEqual({ kind: "ready", doc: ready, path: "c/AN-1.pdf" });
  });
});

describe("quotePdfName", () => {
  it("builds a plain file name from number and customer", () => {
    expect(quotePdfName("AN-2026-0007", "Dachdeckerei Müller & Söhne GmbH")).toBe(
      "Angebot_AN-2026-0007_Dachdeckerei_Muller_Sohne_GmbH.pdf",
    );
    expect(quotePdfName("AN-1", "Straßenbau")).toBe("Angebot_AN-1_Strassenbau.pdf");
  });

  it("drops empty parts", () => {
    expect(quotePdfName("AN-1", " ")).toBe("Angebot_AN-1.pdf");
  });
});
