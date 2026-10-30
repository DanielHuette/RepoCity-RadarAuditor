// Industry groups from src/generated/industries.json, generated from config/branchen.yaml.
import generated from "../generated/industries.json";

export interface IndustryGroup {
  id: string;
  label: string;
  examples: readonly string[];
  /** What customers of the group type into Google before the city. */
  services: readonly string[];
}

export const INDUSTRY_GROUPS: readonly IndustryGroup[] = generated.groups;

// Values stored before the groups existed, lower case, mapped to a group id.
const LEGACY: Readonly<Record<string, string>> = generated.legacy;

function lookupKey(value: string): string {
  return value.trim().toLocaleLowerCase("de");
}

/** Group of a stored industry: its id, its label or an older value such as "Handwerk". */
export function resolveIndustry(value: string): IndustryGroup | undefined {
  const key = lookupKey(value);
  const id = LEGACY[key] ?? key;
  return INDUSTRY_GROUPS.find((group) => group.id === id || lookupKey(group.label) === id);
}

/** German label of a stored industry; values outside every group are shown as stored. */
export function industryLabel(value: string): string {
  return resolveIndustry(value)?.label ?? value.trim();
}

/** Select option text: the group followed by its examples, so the right one is found at a glance. */
function industryOptionLabel(group: IndustryGroup): string {
  return group.examples.length === 0 ? group.label : `${group.label} (${group.examples.join(", ")})`;
}

export const INDUSTRY_OPTIONS: readonly { value: string; label: string }[] = INDUSTRY_GROUPS.map((group) => ({
  value: group.id,
  label: industryOptionLabel(group),
}));
