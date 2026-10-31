// Calls of the Kunden page: ask for a customer search, follow it, take a result into the CRM.
// The worker answers the search (public.claim_prospect_search, migration 016); the app only reads.
import type { PostgrestError } from "@supabase/supabase-js";
import { QueryError } from "../lib/errors";
import type { CrmEntry } from "../lib/prospects";
import { supabase } from "./supabase";
import type { ProspectResult, ProspectSearch } from "./types";

const RECENT_SEARCHES = 8;

interface Result {
  data: unknown;
  error: PostgrestError | null;
}

function unwrap(result: Result, action: string): unknown {
  if (result.error !== null) throw new QueryError(action, result.error.message, result.error.code);
  return result.data;
}

/** Queues a search, or returns the stored one of the same trade and city that is still fresh. */
export async function requestProspectSearch(trade: string, city: string): Promise<string> {
  const action = "Suche starten";
  const id = unwrap(await supabase.rpc("request_prospect_search", { p_trade: trade, p_city: city }), action);
  if (typeof id !== "string") throw new QueryError(action, "keine Suchnummer erhalten", null);
  return id;
}

export interface ProspectView {
  search: ProspectSearch;
  results: ProspectResult[];
  cacheHours: number;
}

export async function loadProspectSearch(id: string): Promise<ProspectView | null> {
  const [search, results, cacheHours] = await Promise.all([
    supabase.from("prospect_searches").select("*").eq("id", id).maybeSingle(),
    supabase.from("prospect_results").select("*").eq("search_id", id).order("rank"),
    supabase.rpc("prospect_cache_hours"),
  ]);
  const row = unwrap(search, "Suche laden") as ProspectSearch | null;
  if (row === null) return null;
  const hours = unwrap(cacheHours, "Speicherdauer laden");
  return {
    search: row,
    results: unwrap(results, "Treffer laden") as ProspectResult[],
    cacheHours: typeof hours === "number" ? hours : 0,
  };
}

export async function listRecentProspectSearches(): Promise<ProspectSearch[]> {
  const result = await supabase
    .from("prospect_searches")
    .select("*")
    .order("requested_at", { ascending: false })
    .limit(RECENT_SEARCHES);
  return unwrap(result, "Letzte Suchen laden") as ProspectSearch[];
}

/** Creates or links the CRM customer with an open deal from what Daniel entered; returns crm.customers.id. */
export async function addProspectToCrm(resultId: string, entry: CrmEntry): Promise<string> {
  const action = "Ins CRM übernehmen";
  const args = {
    p_result: resultId,
    p_name: entry.name,
    p_street: entry.street,
    p_postal_code: entry.postalCode,
    p_city: entry.city,
    p_phone: entry.phone,
  };
  const id = unwrap(await supabase.rpc("add_prospect_to_crm", args), action);
  if (typeof id !== "string") throw new QueryError(action, "keine Kundennummer erhalten", null);
  return id;
}
