// Open audit requests from the form on repocity.speedofthespirit.dev/anfrage/, as the Prüfen tab
// lists them. Only Daniel starts checks: a request with a usable website fills the address field,
// nothing more.
import type { OpenAuditRequest } from "../api/types";
import { validateWebsite } from "./fieldAudit";

const MEETING_LABELS: Readonly<Record<string, string>> = { visit: "Vor Ort", call: "Telefon", video: "Video" };

const wish = new Intl.DateTimeFormat("de-DE", {
  timeZone: "Europe/Berlin",
  weekday: "long",
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/** "Vor Ort", "Telefon" or "Video"; an unknown kind as it is. */
export function meetingLabel(kind: string): string {
  return MEETING_LABELS[kind] ?? kind;
}

/** "Dienstag, 03.11.2026, 09:00 Uhr" in German time, whatever the zone of the device. */
export function formatWish(value: string | null): string {
  const time = value === null ? Number.NaN : Date.parse(value);
  return Number.isNaN(time) ? "ohne Wunschtermin" : `${wish.format(time)} Uhr`;
}

/** The address the Prüfen tab would check; null when the request has no usable website. */
export function auditWebsite(request: OpenAuditRequest): string | null {
  if (request.website === null) return null;
  const result = validateWebsite(request.website);
  return result.ok ? result.website : null;
}

function wishTime(request: OpenAuditRequest): number {
  const time = request.wished_at === null ? Number.NaN : Date.parse(request.wished_at);
  return Number.isNaN(time) ? Number.POSITIVE_INFINITY : time;
}

/** Earliest wish first, requests without a wish last, then the older request first. */
export function sortByWish(requests: readonly OpenAuditRequest[]): OpenAuditRequest[] {
  return [...requests].sort((a, b) => wishTime(a) - wishTime(b) || Date.parse(a.created_at) - Date.parse(b.created_at));
}

export function openRequestsLabel(count: number): string {
  return `${String(count)} offene ${count === 1 ? "Anfrage" : "Anfragen"}`;
}
