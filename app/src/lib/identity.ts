// What the server identified from the website (name, city, industry) and where it found it.
// The values live on the customer row; raw.json may add the record of the identification.
import type { Customer } from "../api/types";
import { industryLabel } from "./industries";

export interface IdentityFact {
  label: string;
  value: string;
}

/** Places the worker reads name, city and industry from, by the code it records. */
const SOURCES: Readonly<Record<string, string>> = {
  json_ld: "unsichtbare Angaben der Website",
  structured_data: "unsichtbare Angaben der Website",
  impressum: "Impressum",
  legal_notice: "Impressum",
  footer: "Fußbereich der Website",
  google_profile: "Google-Unternehmensprofil",
  google_types: "Google-Unternehmensprofil",
  name_words: "Name des Betriebs",
  industry_services: "Leistungen der Branche",
  place: "Google-Unternehmensprofil",
  places: "Google-Unternehmensprofil",
  title: "Titel der Startseite",
  manual: "von Hand eingetragen",
};

const UNSET = "noch nicht erkannt";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function sourceLabel(code: unknown): string | null {
  if (typeof code !== "string" || code.trim() === "") return null;
  const known = SOURCES[code.trim()];
  if (known !== undefined) return known;
  // An unknown code says nothing to the reader; German text from the worker is kept.
  return /^[a-z0-9_]+$/.test(code.trim()) ? null : code.trim();
}

/** The source of the identification in raw.json, as one readable phrase; null when it is not recorded. */
export function identifiedSource(raw: unknown): string | null {
  if (!isRecord(raw)) return null;
  // The worker records a list of { field, value, source } (RawAudit.identified).
  if (Array.isArray(raw.identified)) {
    const listed = raw.identified.map((entry) => (isRecord(entry) ? sourceLabel(entry.source) : null));
    const unique = [...new Set(listed.filter((label): label is string => label !== null))];
    return unique.length === 0 ? null : unique.join(", ");
  }
  const record = raw.identified ?? raw.identification;
  if (!isRecord(record)) return null;
  const single = sourceLabel(record.source);
  if (single !== null) return single;
  const perField = isRecord(record.sources) ? Object.values(record.sources) : Array.isArray(record.sources) ? record.sources : [];
  const labels = [...new Set(perField.map(sourceLabel).filter((label): label is string => label !== null))];
  return labels.length === 0 ? null : labels.join(", ");
}

/** Betrieb, Ort and Branche as the customer row holds them; empty values read "noch nicht erkannt". */
export function identityFacts(customer: Pick<Customer, "name" | "city" | "industry">, source: string | null): IdentityFact[] {
  const facts = [
    { label: "Betrieb", value: customer.name.trim() || UNSET },
    { label: "Ort", value: customer.city.trim() || UNSET },
    { label: "Branche", value: customer.industry.trim() === "" ? UNSET : industryLabel(customer.industry) },
  ];
  return source === null ? facts : [...facts, { label: "Quelle", value: source }];
}
