// "Angebot abschicken": the CRM mails the quote with the audit-plus-quote PDF of the RadarAuditor
// attached (crm.send_quote), so the app sends only once that PDF exists and requests it when missing.
import type { PdfState } from "./offerDocs";

export type SendStep = "request_pdf" | "wait_for_pdf" | "send" | "pdf_failed";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** What confirming the dialog does with the PDF in its current state; a failed PDF is requested anew. */
export function stepOnConfirm(pdf: PdfState["kind"]): SendStep {
  if (pdf === "ready") return "send";
  if (pdf === "pending") return "wait_for_pdf";
  return "request_pdf";
}

/**
 * What a change of the PDF means for a send that waits for it. Right after a new request the list may
 * still show the old failed one, so with `requested` only a ready PDF counts until the request appears.
 */
export function stepWhileWaiting(pdf: PdfState["kind"], requested = false): SendStep {
  if (pdf === "ready") return "send";
  if (pdf === "failed" && !requested) return "pdf_failed";
  return "wait_for_pdf";
}

/** The typed address without surrounding spaces, or null when it is no e-mail address. */
export function cleanEmail(text: string): string | null {
  const trimmed = text.trim();
  return EMAIL.test(trimmed) ? trimmed : null;
}
