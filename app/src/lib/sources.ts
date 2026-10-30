// Data sources the worker may use, with switch and limits. Settings key `sources` holds one entry
// per source: {enabled, daily_cap?, monthly_cost_cap_eur?}. The worker counts each use per day in
// table `source_usage` and stops a source when a limit is reached.
import type { SourceUsage } from "../api/types";
import { parseAmount } from "./format";

export const SOURCES_KEY = "sources";

export const SOURCE_IDS = [
  "google_search", "google_maps", "places", "dataforseo", "chatgpt", "claude", "gemini", "perplexity",
] as const;
export type SourceId = (typeof SOURCE_IDS)[number];

export interface SourceSetting {
  enabled: boolean;
  daily_cap?: number;
  monthly_cost_cap_eur?: number;
}
export type SourceSettings = Record<SourceId, SourceSetting>;

export interface SourceInfo {
  label: string;
  explanation: string;
  paid: boolean;
}

export const SOURCE_INFO: Record<SourceId, SourceInfo> = {
  google_search: {
    label: "Google-Suche",
    explanation: "Platzierungen bei Google, im Browser auf dem Server abgelesen – kostenlos.",
    paid: false,
  },
  google_maps: {
    label: "Google Maps",
    explanation: "Unternehmensprofil, Bewertungen, Kartenplatz und Wettbewerber, im Browser abgelesen – kostenlos.",
    paid: false,
  },
  places: {
    label: "Google Places API",
    explanation:
      "Bezahlte Google-Schnittstelle, springt ein, wenn Google Maps im Browser gesperrt ist: Profil, Wettbewerber und Kartenraster. Kosten pro Abfrage.",
    paid: true,
  },
  dataforseo: {
    label: "DataForSEO",
    explanation: "Bezahlte Google-Daten für Suchnachfrage und Verweise, Kosten pro Abfrage.",
    paid: true,
  },
  chatgpt: {
    label: "ChatGPT",
    explanation: "Fragt chatgpt.com im Browser, ob der Betrieb empfohlen wird – kostenlos.",
    paid: false,
  },
  claude: {
    label: "Claude",
    explanation: "Fragt Claude über das Abo auf dem Server, ob der Betrieb empfohlen wird.",
    paid: false,
  },
  gemini: {
    label: "Gemini",
    explanation: "Fragt gemini.google.com im Browser, ob der Betrieb empfohlen wird – kostenlos.",
    paid: false,
  },
  perplexity: {
    label: "Perplexity",
    explanation: "Fragt perplexity.ai im Browser, ob der Betrieb empfohlen wird – kostenlos.",
    paid: false,
  },
};

// Same values as DEFAULT_SOURCES in src/radarauditor/db/app_settings.py: the worker always enforces a
// daily cap and uses these when the stored entry has none, so the app shows what actually applies.
const DATAFORSEO_MONTHLY_CAP_EUR = 50;

export const DEFAULT_SOURCES: SourceSettings = {
  google_search: { enabled: true, daily_cap: 300 },
  google_maps: { enabled: true, daily_cap: 500 },
  places: { enabled: true, daily_cap: 500 },
  dataforseo: { enabled: true, daily_cap: 1000, monthly_cost_cap_eur: DATAFORSEO_MONTHLY_CAP_EUR },
  chatgpt: { enabled: true, daily_cap: 200 },
  claude: { enabled: true, daily_cap: 200 },
  gemini: { enabled: true, daily_cap: 200 },
  perplexity: { enabled: true, daily_cap: 200 },
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function limit(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;
}

function parseSetting(value: unknown, fallback: SourceSetting): SourceSetting {
  if (!isRecord(value)) return fallback;
  const setting: SourceSetting = { enabled: typeof value.enabled === "boolean" ? value.enabled : fallback.enabled };
  const daily = limit(value.daily_cap) ?? fallback.daily_cap;
  const monthly = "monthly_cost_cap_eur" in value ? limit(value.monthly_cost_cap_eur) : fallback.monthly_cost_cap_eur;
  if (daily !== undefined) setting.daily_cap = daily;
  if (monthly !== undefined) setting.monthly_cost_cap_eur = monthly;
  return setting;
}

/** Stored value merged over the defaults; unknown sources are dropped, broken entries fall back. */
export function parseSources(value: unknown): SourceSettings {
  const stored = isRecord(value) ? value : {};
  return Object.fromEntries(
    SOURCE_IDS.map((id) => [id, parseSetting(stored[id], DEFAULT_SOURCES[id])]),
  ) as SourceSettings;
}

export function withSource(settings: SourceSettings, id: SourceId, change: Partial<SourceSetting>): SourceSettings {
  const next: SourceSetting = { ...settings[id], ...change };
  if (next.daily_cap === undefined) delete next.daily_cap;
  if (next.monthly_cost_cap_eur === undefined) delete next.monthly_cost_cap_eur;
  return { ...settings, [id]: next };
}

export type LimitInput = { value: number | undefined } | { error: string };

/** A daily cap is a whole number of uses and required: the worker enforces one for every source. */
export function parseDailyCap(input: string): LimitInput {
  const text = input.trim();
  if (text === "") return { error: "Bitte ein Tageslimit als ganze Zahl eingeben." };
  return /^\d+$/.test(text) ? { value: Number(text) } : { error: "Bitte ein Tageslimit als ganze Zahl eingeben." };
}

/** A source whose default carries a cost cap must keep one, so a paid source never runs without a ceiling. */
export function costCapRequired(id: SourceId): boolean {
  return DEFAULT_SOURCES[id].monthly_cost_cap_eur !== undefined;
}

export function parseCostCap(input: string, required: boolean): LimitInput {
  if (input.trim() === "") {
    return required ? { error: "Für diese bezahlte Quelle ist ein Monatslimit Pflicht." } : { value: undefined };
  }
  const amount = parseAmount(input);
  return amount === null || amount < 0 ? { error: "Bitte einen Betrag wie 50 oder 49,50 eingeben." } : { value: amount };
}

/** First day of the month of `now` as YYYY-MM-DD, in the calendar of Europe/Berlin like the worker. */
export function monthStart(now: Date): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Berlin", year: "numeric", month: "2-digit" })
    .formatToParts(now);
  const year = parts.find((part) => part.type === "year")?.value ?? "1970";
  const month = parts.find((part) => part.type === "month")?.value ?? "01";
  return `${year}-${month}-01`;
}

/** Uses per source from the first day of the month on. */
export function monthUsage(rows: readonly SourceUsage[], fromDay: string): Partial<Record<string, number>> {
  const totals: Partial<Record<string, number>> = {};
  for (const row of rows) {
    if (row.day < fromDay) continue;
    totals[row.source] = (totals[row.source] ?? 0) + row.count;
  }
  return totals;
}
