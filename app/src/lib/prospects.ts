// Customer search: form checks, the state of a search in one sentence and the contact ways the
// server stored with each result (config/kundensuche.yaml, documented in Dokumentation/KONTAKTWEGE.de.md).
import type { ContactStatus, ContactWay, ProspectResult, ProspectSearch } from "../api/types";
import { formatDateTime } from "./format";

const MIN_CHARS = 2;
const MAX_CHARS = 80;
const HOUR_MS = 60 * 60 * 1000;
const STATUSES: readonly ContactStatus[] = ["allowed", "uncertain", "forbidden"];

export const CONTACT_STATUS_LABELS: Readonly<Record<ContactStatus, string>> = {
  allowed: "erlaubt",
  uncertain: "nur mit Anlass",
  forbidden: "nicht erlaubt",
};

export const CONTACT_STATUS_TONES: Readonly<Record<ContactStatus, string>> = {
  allowed: "tone-ok",
  uncertain: "tone-warn",
  forbidden: "tone-bad",
};

export type SearchValidation =
  | { ok: true; trade: string; city: string }
  | { ok: false; errors: { trade?: string; city?: string } };

function tidy(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

function fits(value: string): boolean {
  return value.length >= MIN_CHARS && value.length <= MAX_CHARS;
}

/** Trade and city as request_prospect_search accepts them. */
export function validateSearch(trade: string, city: string): SearchValidation {
  const cleanTrade = tidy(trade);
  const cleanCity = tidy(city);
  const errors: { trade?: string; city?: string } = {};
  if (!fits(cleanTrade)) errors.trade = "Bitte ein Gewerk eingeben, zum Beispiel Dachdecker.";
  if (!fits(cleanCity)) errors.city = "Bitte die Stadt eingeben, zum Beispiel Münster.";
  if (errors.trade !== undefined || errors.city !== undefined) return { ok: false, errors };
  return { ok: true, trade: cleanTrade, city: cleanCity };
}

/** Whether the server still has to act on the search. */
export function isSearchWaiting(search: ProspectSearch): boolean {
  return search.status === "queued" || search.status === "running";
}

function doneText(search: ProspectSearch, cacheHours: number): string {
  const count = search.result_count ?? 0;
  const fetched = search.fetched_at ?? search.requested_at;
  const until = new Date(new Date(fetched).getTime() + cacheHours * HOUR_MS).toISOString();
  const text = `${String(count)} ${count === 1 ? "Betrieb" : "Betriebe"} gefunden am ${formatDateTime(fetched)}. Bis ${formatDateTime(until)} kostet dieselbe Suche nichts; danach löscht der Server die Liste, weil Google das Speichern nicht länger erlaubt.`;
  return search.error === null || search.error.trim() === "" ? text : `${text} ${search.error}`;
}

/** The state of a search in one sentence. */
export function searchStatusText(search: ProspectSearch, cacheHours: number): string {
  switch (search.status) {
    case "queued":
      return "Wartet auf den Server.";
    case "running":
      return "Der Server fragt Google ab …";
    case "done":
      return doneText(search, cacheHours);
    case "failed":
      return `Suche gescheitert: ${search.error ?? "ohne Grund"}`;
    case "expired":
      return "Die Liste ist gelöscht, weil Google das Speichern nur kurz erlaubt. Bitte neu suchen.";
  }
}

function isContactWay(value: unknown): value is ContactWay {
  if (typeof value !== "object" || value === null) return false;
  const way = value as Record<string, unknown>;
  return (
    typeof way.channel === "string" &&
    typeof way.label === "string" &&
    typeof way.note === "string" &&
    STATUSES.includes(way.status as ContactStatus)
  );
}

/** The contact ways stored with a result; malformed entries are left out. */
export function contactWays(raw: unknown): ContactWay[] {
  return Array.isArray(raw) ? raw.filter(isContactWay) : [];
}

/** Ways to approach the business; none after an objection, and never a forbidden one such as the cold mail. */
export function offeredContactWays(result: ProspectResult): ContactWay[] {
  if (result.objected) return [];
  return contactWays(result.contact_ways).filter((way) => way.status !== "forbidden");
}

export interface CrmEntry {
  name: string;
  street: string;
  postalCode: string;
  city: string;
  phone: string;
}

export type CrmEntryValidation = { ok: true; entry: CrmEntry } | { ok: false; error: string };

/**
 * What Daniel copies from the public Google profile for the CRM. The Places fields of the list may not
 * be stored beyond the list (Dokumentation/KONTAKTWEGE.de.md), so the CRM gets only what he enters.
 */
export function validateCrmEntry(form: CrmEntry): CrmEntryValidation {
  const entry: CrmEntry = {
    name: tidy(form.name),
    street: tidy(form.street),
    postalCode: tidy(form.postalCode),
    city: tidy(form.city),
    phone: tidy(form.phone),
  };
  if (entry.name.length < MIN_CHARS) return { ok: false, error: "Bitte den Namen des Betriebs aus dem Google-Profil eintragen." };
  return { ok: true, entry };
}

/** An audit starts from the website, so a business without one cannot be audited from here. */
export function canAudit(result: ProspectResult): boolean {
  return result.website !== null && result.website.trim() !== "";
}
