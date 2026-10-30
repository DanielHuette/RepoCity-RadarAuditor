// Which paid interface delivered the data of a check, when the free browser route was blocked (spec
// "Source order"). The worker records it in the measured JSON of the check as `route` (one route id) or
// `routes` (a list of ids, a list of {route}, or an object per source such as {"chatgpt": "openai"}).
// Free routes (browser, Claude Code) and unknown ids are not shown.

const PAID_ROUTES: Readonly<Record<string, string>> = {
  dataforseo: "DataForSEO",
  places: "Google-Schnittstelle",
  places_api: "Google-Schnittstelle",
  openai: "OpenAI-Schnittstelle",
  openai_api: "OpenAI-Schnittstelle",
  gemini_api: "Gemini-Schnittstelle",
  perplexity_api: "Perplexity-Schnittstelle",
};

function routeIds(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(routeIds);
  if (typeof value !== "object" || value === null) return [];
  if ("route" in value) return routeIds(value.route);
  return Object.values(value).flatMap(routeIds);
}

function joinGerman(parts: readonly string[]): string {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} und ${parts.at(-1) ?? ""}`;
}

/** "über DataForSEO" or "über Google-Schnittstelle und OpenAI-Schnittstelle"; null when only free routes were used. */
export function paidRouteText(measured: Readonly<Record<string, unknown>>): string | null {
  const ids = [...routeIds(measured.route), ...routeIds(measured.routes)];
  const labels = [...new Set(ids.flatMap((id) => PAID_ROUTES[id.trim().toLowerCase()] ?? []))];
  return labels.length === 0 ? null : `über ${joinGerman(labels)}`;
}
