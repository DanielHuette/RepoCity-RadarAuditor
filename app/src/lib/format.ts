const euro = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" });
const date = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
const dateTime = new Intl.DateTimeFormat("de-DE", {
  day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
});

export function formatEuro(amount: number | null): string {
  return amount === null ? "–" : euro.format(amount);
}

export function formatDate(value: string | null): string {
  if (value === null) return "–";
  const parsed = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  return Number.isNaN(parsed.getTime()) ? "–" : date.format(parsed);
}

export function formatDateTime(value: string | null): string {
  if (value === null) return "–";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "–" : dateTime.format(parsed);
}

export function formatScore(score: number | null): string {
  return score === null ? "–" : String(Math.round(score));
}

/** Accepts "1.234,56", "1234.56" and "1234,5" as typed on German phone keyboards. */
export function parseAmount(input: string): number | null {
  const cleaned = input.replace(/[€\s]/g, "");
  if (cleaned === "") return null;
  const normalised = cleaned.includes(",") ? cleaned.replace(/\./g, "").replace(",", ".") : cleaned;
  if (!/^-?\d+(\.\d{1,2})?$/.test(normalised)) return null;
  return Number(normalised);
}

/** The worker validates websites as absolute URLs, so a bare domain gets https in front. */
export function normaliseWebsite(input: string): string | null {
  const trimmed = input.trim();
  if (trimmed === "") return null;
  const withScheme = /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
  try {
    const url = new URL(withScheme);
    return url.hostname.includes(".") ? url.toString() : null;
  } catch {
    return null;
  }
}

/** "locations/<id>" from a pasted location id, location name or account location path; null if none. */
export function hostOf(url: string | null | undefined): string {
  if (url === null || url === undefined || url === "") return "";
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}
