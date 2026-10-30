// Typed view of src/generated/catalogue.json, generated from config/pruefkatalog.yaml.
import generated from "../generated/catalogue.json";

export interface CatalogueCheck {
  id: string;
  area: string;
  name: string;
  /** One plain-language sentence on why the check matters, shown when choosing checks. */
  why: string;
  source: string;
  /** The check's equal share of its area weight, as in scoring/evaluator.py. */
  weight: number;
  requiresPaid: boolean;
  requiresAccess: boolean;
  customerQuestion: string;
  customerImpact: string;
}

export interface CatalogueArea {
  id: string;
  name: string;
  weight: number;
  checks: readonly CatalogueCheck[];
}

export const CATALOGUE_AREAS: readonly CatalogueArea[] = generated.areas;

export const AREA_NAMES: Readonly<Record<string, string>> = Object.fromEntries(
  CATALOGUE_AREAS.map((area) => [area.id, area.name]),
);

export const AREA_IDS: readonly string[] = CATALOGUE_AREAS.map((area) => area.id);

const CHECKS = new Map(CATALOGUE_AREAS.flatMap((area) => area.checks.map((check) => [check.id, check] as const)));
const CHECK_ORDER = new Map([...CHECKS.keys()].map((id, index) => [id, index]));

// Labels around the radar; the full catalogue names do not fit beside a chart on a phone.
const RADAR_LABELS: Readonly<Record<string, string>> = {
  a: "Technik",
  b: "Struktur",
  c: "Inhalt",
  d: "Firmendaten",
  e: "Strukturdaten",
  f: "Google-Profil",
  g: "Bewertungen",
  h: "Verzeichnisse",
  i: "Links",
  j: "Rankings",
  k: "KI-Assistenten",
  l: "Recht",
  m: "Wirkung",
  n: "Werkzeuge",
};

export function catalogueCheck(checkId: string): CatalogueCheck | undefined {
  return CHECKS.get(checkId);
}

export function checkName(checkId: string): string {
  return CHECKS.get(checkId)?.name ?? `Prüfung ${checkId}`;
}

/** Position in the catalogue; unknown checks sort after every known one, by id. */
export function catalogueOrder(checkId: string): number {
  return CHECK_ORDER.get(checkId) ?? CHECKS.size;
}

export function radarLabel(areaId: string): string {
  return RADAR_LABELS[areaId] ?? AREA_NAMES[areaId] ?? `Bereich ${areaId}`;
}

/** Areas that count towards the total score; the site's radar leaves weight-0 areas out as well. */
export function scoredAreas(): CatalogueArea[] {
  return CATALOGUE_AREAS.filter((area) => area.weight > 0);
}
