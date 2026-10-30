// Which catalogue checks a new audit runs, chosen on the Prüfen page and sent with start_field_audit
// as p_disabled_checks. The selection is kept per device, so the last choice comes back next time.
import { CATALOGUE_AREAS, type CatalogueArea, type CatalogueCheck } from "./catalogue";

/** Order of the areas on the Prüfen page: Google first, the customer-access area last. */
export const AREA_ORDER: readonly string[] = ["f", "g", "a", "b", "c", "d", "e", "h", "i", "j", "k", "l", "n", "m"];

const NEW_AREAS: ReadonlySet<string> = new Set(["n"]);
const NEW_CHECKS: ReadonlySet<string> = new Set(["f15", "n1", "n2", "n3"]);

export const SELECTION_STORAGE_KEY = "radarauditor.disabledChecks";

export type AreaState = "all" | "none" | "some";

export interface Tag {
  label: string;
  isNew: boolean;
}

type SelectionStorage = Pick<Storage, "getItem" | "setItem">;

/** Areas in AREA_ORDER; areas added to the catalogue later follow in catalogue order. */
export function orderedAreas(areas: readonly CatalogueArea[] = CATALOGUE_AREAS): CatalogueArea[] {
  const rank = (area: CatalogueArea) => {
    const index = AREA_ORDER.indexOf(area.id);
    return index === -1 ? AREA_ORDER.length : index;
  };
  return areas.map((area, index) => ({ area, index })).sort((a, b) => rank(a.area) - rank(b.area) || a.index - b.index).map(({ area }) => area);
}

export function areaTags(area: CatalogueArea): Tag[] {
  const tags: Tag[] = [];
  if (NEW_AREAS.has(area.id)) tags.push({ label: "neu", isNew: true });
  const paid = area.checks.filter((check) => check.requiresPaid).length;
  if (paid > 0) tags.push({ label: `${String(paid)} ${paid === 1 ? "kostet" : "kosten"} Geld`, isNew: false });
  if (area.checks.some((check) => check.requiresAccess)) tags.push({ label: "braucht Kundenzugang", isNew: false });
  return tags;
}

export function checkTags(check: CatalogueCheck): Tag[] {
  const tags: Tag[] = [];
  if (NEW_CHECKS.has(check.id)) tags.push({ label: "neu", isNew: true });
  if (check.requiresPaid) tags.push({ label: "kostet Geld", isNew: false });
  return tags;
}

function allIds(areas: readonly CatalogueArea[]): string[] {
  return areas.flatMap((area) => area.checks.map((check) => check.id));
}

/** Everything on except the checks that need customer access, which a first audit does not have. */
export function defaultDisabled(areas: readonly CatalogueArea[] = CATALOGUE_AREAS): string[] {
  return areas.flatMap((area) => area.checks.filter((check) => check.requiresAccess).map((check) => check.id)).sort();
}

export function setCheck(disabled: readonly string[], checkId: string, enabled: boolean): string[] {
  const next = new Set(disabled);
  if (enabled) next.delete(checkId);
  else next.add(checkId);
  return [...next].sort();
}

export function setArea(disabled: readonly string[], area: CatalogueArea, enabled: boolean): string[] {
  const next = new Set(disabled);
  for (const check of area.checks) {
    if (enabled) next.delete(check.id);
    else next.add(check.id);
  }
  return [...next].sort();
}

export function setAll(areas: readonly CatalogueArea[], enabled: boolean): string[] {
  return enabled ? [] : allIds(areas).sort();
}

export function areaState(disabled: readonly string[], area: CatalogueArea): AreaState {
  const off = area.checks.filter((check) => disabled.includes(check.id)).length;
  if (off === 0) return "all";
  return off === area.checks.length ? "none" : "some";
}

export function enabledCount(disabled: readonly string[], areas: readonly CatalogueArea[]): { enabled: number; total: number } {
  const ids = allIds(areas);
  const off = new Set(disabled);
  return { enabled: ids.filter((id) => !off.has(id)).length, total: ids.length };
}

/** The stored selection, reduced to checks still in the catalogue; the default when nothing usable is stored. */
export function loadSelection(storage: SelectionStorage | null, areas: readonly CatalogueArea[] = CATALOGUE_AREAS): string[] {
  try {
    const text = storage?.getItem(SELECTION_STORAGE_KEY) ?? null;
    if (text === null) return defaultDisabled(areas);
    const parsed: unknown = JSON.parse(text);
    if (!Array.isArray(parsed)) return defaultDisabled(areas);
    const known = new Set(allIds(areas));
    return [...new Set(parsed.filter((item): item is string => typeof item === "string" && known.has(item)))].sort();
  } catch {
    return defaultDisabled(areas);
  }
}

/** Keeps the selection on this device; without storage (private window, blocked) it is simply not kept. */
export function saveSelection(storage: SelectionStorage | null, disabled: readonly string[]): void {
  try {
    storage?.setItem(SELECTION_STORAGE_KEY, JSON.stringify(disabled));
  } catch {
    // Storage full or blocked: the selection still applies to this audit.
  }
}

/** localStorage of the browser, or null where reading it throws. */
export function deviceStorage(): SelectionStorage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}
