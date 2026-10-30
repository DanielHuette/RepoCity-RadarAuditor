// Quote positions from an audit: CRM products whose areas have failed or partial checks, totals as the CRM computes them.
import type { CheckOutcome, CrmProduct, CrmQuoteStatus } from "../api/types";
import { AREA_NAMES } from "./catalogue";

const OPEN_OUTCOMES: ReadonlySet<CheckOutcome> = new Set(["failed", "partial"]);
const MAX_QUANTITY = 999;
const MAX_TITLE_LENGTH = 300;

export interface CheckOutcomeRow {
  check_id: string;
  area: string;
  outcome: CheckOutcome;
}

export interface Position {
  productId: string;
  title: string;
  description: string;
  unit: string;
  unitPriceNet: number;
  vatRate: number;
  billing: CrmProduct["billing"];
  /** Area ids of the audit this product covers, in catalogue order. */
  areas: string[];
  /** Failed and partial checks in those areas. */
  openChecks: number;
  quantity: number;
  enabled: boolean;
}

export interface Totals {
  net: number;
  vat: number;
  gross: number;
  onceNet: number;
  monthlyNet: number;
}

export interface QuoteItemRow {
  quote_id: string;
  position: number;
  product_id: string;
  title: string;
  description: string;
  quantity: number;
  unit: string;
  unit_price_net: number;
  vat_rate: number;
}

export const QUOTE_STATUS_LABELS: Record<CrmQuoteStatus, string> = {
  draft: "Offen",
  sent: "Gesendet",
  accepted: "Angenommen",
  declined: "Abgelehnt",
  expired: "Abgelaufen",
};

export const QUOTE_STATUS_TONES: Record<CrmQuoteStatus, string> = {
  draft: "",
  sent: "tone-busy",
  accepted: "tone-ok",
  declined: "tone-bad",
  expired: "tone-warn",
};

const AREA_ORDER = Object.keys(AREA_NAMES);

function areaRank(area: string): number {
  const index = AREA_ORDER.indexOf(area);
  return index === -1 ? AREA_ORDER.length : index;
}

/** Open checks per area id; areas without failed or partial checks are absent. */
export function openChecksByArea(checks: readonly CheckOutcomeRow[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const check of checks) {
    if (OPEN_OUTCOMES.has(check.outcome)) counts.set(check.area, (counts.get(check.area) ?? 0) + 1);
  }
  return counts;
}

/** One position per active product that covers at least one area with open checks; one-off before monthly. */
export function proposePositions(checks: readonly CheckOutcomeRow[], products: readonly CrmProduct[]): Position[] {
  const open = openChecksByArea(checks);
  const positions: Position[] = [];
  const seen = new Set<string>();
  for (const product of products) {
    if (!product.active || seen.has(product.id)) continue;
    const areas = [...new Set(product.radar_areas)].filter((area) => open.has(area)).sort((a, b) => areaRank(a) - areaRank(b));
    if (areas.length === 0) continue;
    seen.add(product.id);
    positions.push({
      productId: product.id,
      title: product.name,
      description: product.description,
      unit: product.unit,
      unitPriceNet: product.price_net,
      vatRate: product.vat_rate,
      billing: product.billing,
      areas,
      openChecks: areas.reduce((sum, area) => sum + (open.get(area) ?? 0), 0),
      quantity: 1,
      enabled: true,
    });
  }
  return positions.sort(
    (a, b) => Number(a.billing === "monthly") - Number(b.billing === "monthly") || a.title.localeCompare(b.title, "de"),
  );
}

/** Whole quantity between 1 and 999 from a typed value; null when it is none. */
export function parseQuantity(input: string): number | null {
  const text = input.trim();
  if (!/^\d{1,3}$/.test(text)) return null;
  const quantity = Number(text);
  return quantity >= 1 && quantity <= MAX_QUANTITY ? quantity : null;
}

function toCents(amount: number): number {
  return Math.round(amount * 100);
}

/**
 * Same rounding as crm.document_totals: each line rounded to cents, VAT per rate on the rounded sum
 * of that rate, no VAT for a small business.
 */
export function computeTotals(positions: readonly Position[], smallBusiness: boolean): Totals {
  const byRate = new Map<number, number>();
  let onceCents = 0;
  let monthlyCents = 0;
  for (const position of positions) {
    if (!position.enabled) continue;
    const lineCents = Math.round(position.quantity * toCents(position.unitPriceNet));
    byRate.set(position.vatRate, (byRate.get(position.vatRate) ?? 0) + lineCents);
    if (position.billing === "monthly") monthlyCents += lineCents;
    else onceCents += lineCents;
  }
  const netCents = onceCents + monthlyCents;
  let vatCents = 0;
  if (!smallBusiness) {
    for (const [rate, cents] of byRate) vatCents += Math.round((cents * rate) / 100);
  }
  return {
    net: netCents / 100,
    vat: vatCents / 100,
    gross: (netCents + vatCents) / 100,
    onceNet: onceCents / 100,
    monthlyNet: monthlyCents / 100,
  };
}

function itemTitle(position: Position): string {
  const suffix = position.billing === "monthly" ? ", monatlich" : ", einmalig";
  return `${position.title.slice(0, MAX_TITLE_LENGTH - suffix.length)}${suffix}`;
}

function itemDescription(position: Position): string {
  const covered = `Behebt die offenen Punkte in: ${position.areas.map((area) => AREA_NAMES[area] ?? area).join(", ")}.`;
  return position.description.trim() === "" ? covered : `${position.description.trim()}\n${covered}`;
}

/** Rows for crm.quote_items from the enabled positions, numbered from 1. */
export function buildQuoteItems(quoteId: string, positions: readonly Position[]): QuoteItemRow[] {
  return positions
    .filter((position) => position.enabled)
    .map((position, index) => ({
      quote_id: quoteId,
      position: index + 1,
      product_id: position.productId,
      title: itemTitle(position),
      description: itemDescription(position),
      quantity: position.quantity,
      unit: position.unit,
      unit_price_net: position.unitPriceNet,
      vat_rate: position.vatRate,
    }));
}

export interface QuoteTextInput {
  score: number | null;
  auditDate: string | null;
  areas: readonly string[];
}

function germanDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? ""
    : new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "Europe/Berlin" }).format(parsed);
}

function joinGerman(items: readonly string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} und ${items[items.length - 1] ?? ""}`;
}

/** Intro and closing text of the quote, referring to the audit score and the areas the positions cover. */
export function quoteTexts({ score, auditDate, areas }: QuoteTextInput): { intro: string; outro: string } {
  const date = auditDate === null ? "" : germanDate(auditDate);
  const when = date === "" ? "" : ` vom ${date}`;
  const result = score === null ? "" : ` hat ${String(Math.round(score))} von 100 Punkten ergeben`;
  const names = [...new Set(areas)].sort((a, b) => areaRank(a) - areaRank(b)).map((area) => AREA_NAMES[area] ?? area);
  const first =
    result === ""
      ? `vielen Dank für das Gespräch. Die Prüfung Ihres Online-Auftritts${when} zeigt, wo Kunden Sie heute noch nicht finden.`
      : `vielen Dank für das Gespräch. Die Prüfung Ihres Online-Auftritts${when}${result}.`;
  const second =
    names.length === 0
      ? "Gern biete ich Ihnen folgende Leistungen an:"
      : `Die folgenden Leistungen beheben die offenen Punkte in den Bereichen ${joinGerman(names)}:`;
  return {
    intro: `${first} ${second}`,
    outro:
      "Nach der Umsetzung prüfe ich Ihren Auftritt erneut und zeige Ihnen die Veränderung im Vorher-Nachher-Vergleich. Ich freue mich auf Ihre Rückmeldung.",
  };
}
