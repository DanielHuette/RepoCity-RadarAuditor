// Plain German for the target text of an optimisation change. The grammar is defined by the worker in
// src/radarauditor/optimization/targets.py; a text outside it is shown as it is.

const CONTENT_TYPES: Readonly<Record<string, string>> = { page: "Seite", post: "Beitrag" };

const WORDPRESS_FIELDS: Readonly<Record<string, string>> = {
  title: "Titel",
  content: "Inhalt",
  excerpt: "Kurzbeschreibung",
  meta_title: "Meta-Titel",
  meta_description: "Meta-Beschreibung",
  schema: "Strukturierte Daten",
};

const PROFILE_FIELDS: Readonly<Record<string, string>> = {
  name: "Name",
  description: "Beschreibung",
  phone: "Telefonnummer",
  website: "Website",
};

const PROFILE = "Unternehmensprofil";

const WORDPRESS_PATTERN = /^wordpress:(page|post):([1-9][0-9]{0,11}):([a-z_]+)$/;
const PROFILE_PATTERN = /^business_profile:([a-z_]+)$/;
const REVIEW_PATTERN = /^business_profile:review:[A-Za-z0-9_-]{1,200}:reply$/;
const MANUAL_PATTERN = /^manual:(\S.{0,198})$/;

/** "wordpress:page:42:meta_description" → "Seite 42 – Meta-Beschreibung". */
export function formatTarget(target: string): string {
  const wordpress = WORDPRESS_PATTERN.exec(target);
  if (wordpress !== null) {
    const [, type = "", id = "", field = ""] = wordpress;
    const label = WORDPRESS_FIELDS[field];
    return label === undefined ? target : `${CONTENT_TYPES[type] ?? type} ${id} – ${label}`;
  }
  if (REVIEW_PATTERN.test(target)) return `${PROFILE} – Antwort auf eine Bewertung`;
  const profile = PROFILE_PATTERN.exec(target);
  if (profile !== null) {
    const label = PROFILE_FIELDS[profile[1] ?? ""];
    return label === undefined ? target : `${PROFILE} – ${label}`;
  }
  const manual = MANUAL_PATTERN.exec(target);
  return manual === null ? target : (manual[1] ?? target).trim();
}
