// Combined audit-plus-quote PDFs: the app requests a documents row of kind audit_quote with
// data {audit_id, quote_id, quote_number}; the worker renders it and sets file_path, or writes data.error.
import type { Doc } from "../api/types";

export type PdfState =
  | { kind: "none" }
  | { kind: "pending"; doc: Doc }
  | { kind: "ready"; doc: Doc; path: string }
  | { kind: "failed"; doc: Doc; error: string };

export function quotePdfData(auditId: string, quoteId: string, quoteNumber: string): Record<string, string> {
  return { audit_id: auditId, quote_id: quoteId, quote_number: quoteNumber };
}

function dataText(doc: Doc, key: string): string | null {
  const value = doc.data[key];
  return typeof value === "string" ? value : null;
}

function newestFirst(docs: readonly Doc[]): Doc[] {
  return docs.filter((doc) => doc.kind === "audit_quote").sort((a, b) => b.created_at.localeCompare(a.created_at));
}

/** Newest PDF request of a quote. */
export function latestQuotePdf(docs: readonly Doc[], quoteId: string): Doc | null {
  return newestFirst(docs).find((doc) => dataText(doc, "quote_id") === quoteId) ?? null;
}

/** Quote of the newest PDF request made for an audit, so a reopened Angebot finds its quote again. */
export function quoteIdOfAudit(docs: readonly Doc[], auditId: string): string | null {
  const doc = newestFirst(docs).find((item) => dataText(item, "audit_id") === auditId);
  return doc === undefined ? null : dataText(doc, "quote_id");
}

export function pdfState(doc: Doc | null): PdfState {
  if (doc === null) return { kind: "none" };
  if (doc.file_path !== null) return { kind: "ready", doc, path: doc.file_path };
  const error = requestError(doc);
  return error === null ? { kind: "pending", doc } : { kind: "failed", doc, error };
}

/** The worker writes `data.error` into a request it could not issue; issued documents have none. */
function requestError(doc: Doc): string | null {
  if (doc.file_path !== null) return null;
  const error = doc.data.error;
  return typeof error === "string" && error.trim() !== "" ? error : null;
}

/** File name of the combined PDF, safe on every system the share sheet hands it to. */
export function quotePdfName(quoteNumber: string, customerName: string): string {
  const safe = (text: string) =>
    text
      .normalize("NFKD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/ß/g, "ss")
      .replace(/[^A-Za-z0-9-]+/g, "_")
      .replace(/^_+|_+$/g, "");
  const parts = ["Angebot", safe(quoteNumber), safe(customerName)].filter((part) => part !== "");
  return `${parts.join("_")}.pdf`;
}
