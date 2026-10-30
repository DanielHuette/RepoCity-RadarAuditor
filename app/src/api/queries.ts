// Typed read and write calls used by the pages; one function per use case, no SQL in components.
//
// Settings the worker reads at the start of every job (table `settings`, written by the app):
// - `sources`: {<source id>: {enabled, daily_cap?, monthly_cost_cap_eur?}}, see lib/sources.ts.
// The checks switched off for an audit travel with it (start_field_audit, p_disabled_checks).
import type { PostgrestError } from "@supabase/supabase-js";
import { QueryError } from "../lib/errors";
import type { FieldAuditRequest } from "../lib/fieldAudit";
import { HEARTBEAT_KEY, parseHeartbeat, type Heartbeat } from "../lib/heartbeat";
import type { ProgressJob } from "../lib/progress";
import { identifiedSource } from "../lib/identity";
import { isMissingObject, parseAuditTexts, type AuditTexts } from "../lib/texts";
import { supabase } from "./supabase";
import type {
  Audit,
  AuditWithCustomer,
  CheckResult,
  Customer,
  Finding,
  Keyword,
  OpenAuditRequest,
  Ranking,
  SourceUsage,
} from "./types";

const SIGNED_URL_SECONDS = 60 * 60;
const RECENT_AUDITS = 10;

interface Result {
  data: unknown;
  error: PostgrestError | null;
}

function unwrap(result: Result, action: string): unknown {
  if (result.error !== null) throw new QueryError(action, result.error.message, result.error.code);
  return result.data;
}

export async function getCustomer(id: string): Promise<Customer | null> {
  const result = await supabase.from("customers").select("*").eq("id", id).maybeSingle();
  return unwrap(result, "Kunde laden") as Customer | null;
}

export async function listKeywords(customerId: string): Promise<Keyword[]> {
  const result = await supabase
    .from("keywords")
    .select("*")
    .eq("customer_id", customerId)
    .order("is_primary", { ascending: false })
    .order("phrase");
  return unwrap(result, "Suchbegriffe laden") as Keyword[];
}

export async function listAudits(customerId: string): Promise<Audit[]> {
  const result = await supabase
    .from("audits")
    .select("*")
    .eq("customer_id", customerId)
    .order("requested_at", { ascending: false });
  return unwrap(result, "Audits laden") as Audit[];
}

export async function listRecentAudits(): Promise<AuditWithCustomer[]> {
  const result = await supabase
    .from("audits")
    .select("*, customers(name)")
    .order("requested_at", { ascending: false })
    .limit(RECENT_AUDITS);
  return unwrap(result, "Letzte Prüfungen laden") as AuditWithCustomer[];
}

/** Creates or reuses the customer by its website, then queues audit and collect job with the chosen checks. */
export async function startFieldAudit(request: FieldAuditRequest): Promise<string> {
  const id = unwrap(await supabase.rpc("start_field_audit", request), "Prüfung starten");
  if (typeof id !== "string") throw new QueryError("Prüfung starten", "keine Prüfungsnummer erhalten", null);
  return id;
}

export async function getAuditProgress(auditId: string): Promise<{ audit: AuditWithCustomer | null; jobs: ProgressJob[] }> {
  const [audit, jobs] = await Promise.all([
    supabase.from("audits").select("*, customers(name)").eq("id", auditId).maybeSingle(),
    supabase.from("jobs").select("kind, status, error, created_at, run_after").eq("audit_id", auditId).order("created_at"),
  ]);
  return {
    audit: unwrap(audit, "Prüfung laden") as AuditWithCustomer | null,
    jobs: unwrap(jobs, "Aufträge laden") as ProgressJob[],
  };
}

export async function getHeartbeat(): Promise<Heartbeat | null> {
  const result = await supabase.from("settings").select("value").eq("key", HEARTBEAT_KEY).maybeSingle();
  const row = unwrap(result, "Serverstatus laden") as { value: unknown } | null;
  return row === null ? null : parseHeartbeat(row.value);
}

export async function getAudit(id: string): Promise<AuditWithCustomer | null> {
  const result = await supabase.from("audits").select("*, customers(name)").eq("id", id).maybeSingle();
  return unwrap(result, "Audit laden") as AuditWithCustomer | null;
}

export async function auditResults(auditId: string): Promise<{ checks: CheckResult[]; findings: Finding[] }> {
  const [checks, findings] = await Promise.all([
    supabase.from("check_results").select("*").eq("audit_id", auditId).order("check_id"),
    supabase.from("findings").select("*").eq("audit_id", auditId).order("severity", { ascending: false }),
  ]);
  return {
    checks: unwrap(checks, "Prüfergebnisse laden") as CheckResult[],
    findings: unwrap(findings, "Befunde laden") as Finding[],
  };
}

/** Check results alone, for the previous audit drawn as the second outline of the radar. */
export async function auditChecks(auditId: string): Promise<CheckResult[]> {
  const result = await supabase.from("check_results").select("*").eq("audit_id", auditId).order("check_id");
  return unwrap(result, "Vorherige Prüfergebnisse laden") as CheckResult[];
}

export async function listRankings(auditId: string): Promise<Ranking[]> {
  const result = await supabase.from("rankings").select("*").eq("audit_id", auditId).order("measured_at");
  return unwrap(result, "Platzierungen laden") as Ranking[];
}

async function signedUrl(bucket: string, path: string, action: string): Promise<string> {
  const { data, error } = await supabase.storage.from(bucket).createSignedUrl(path, SIGNED_URL_SECONDS);
  if (error !== null) throw new QueryError(action, error.message, null);
  return data.signedUrl;
}

export async function reportUrl(audit: Audit): Promise<string | null> {
  if (audit.report_path === null) return null;
  return signedUrl("reports", audit.report_path, "Bericht öffnen");
}

export async function auditTexts(auditId: string): Promise<AuditTexts | null> {
  const action = "Textvorschläge laden";
  const { data, error } = await supabase.storage.from("raw").createSignedUrl(`${auditId}/texte.json`, SIGNED_URL_SECONDS);
  if (error !== null) {
    if (isMissingObject(error)) return null;
    throw new QueryError(action, error.message, null);
  }
  const response = await fetch(data.signedUrl);
  if (response.status === 404) return null;
  if (!response.ok) throw new QueryError(action, `HTTP ${String(response.status)}`, null);
  return parseAuditTexts(await response.text());
}

export const IDENTIFICATION_TIMEOUT_MS = 15_000;

async function fetchWithin(url: string, timeoutMs: number, action: string): Promise<Response> {
  try {
    return await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new QueryError(action, `keine Antwort nach ${String(timeoutMs / 1000)} Sekunden`, null);
    }
    throw error;
  }
}

/**
 * Where the worker says it took name, city and industry from; null when raw.json is missing or does not say.
 * Unreadable files, server errors and timeouts reject, so the caller can show that the source is unknown.
 */
export async function identificationSource(rawPath: string): Promise<string | null> {
  const action = "Erkennungsquelle laden";
  const { data, error } = await supabase.storage.from("raw").createSignedUrl(rawPath, SIGNED_URL_SECONDS);
  if (error !== null) {
    if (isMissingObject(error)) return null;
    throw new QueryError(action, error.message, null);
  }
  const response = await fetchWithin(data.signedUrl, IDENTIFICATION_TIMEOUT_MS, action);
  if (response.status === 404) return null;
  if (!response.ok) throw new QueryError(action, `HTTP ${String(response.status)}`, null);
  const body = await response.text();
  let raw: unknown;
  try {
    raw = JSON.parse(body);
  } catch {
    throw new QueryError(action, "raw.json ist kein gültiges JSON", null);
  }
  return identifiedSource(raw);
}

export async function getSetting(key: string): Promise<unknown> {
  const result = await supabase.from("settings").select("value").eq("key", key).maybeSingle();
  const row = unwrap(result, "Einstellungen laden") as { value: unknown } | null;
  return row?.value ?? null;
}

export async function saveSetting(key: string, value: unknown): Promise<void> {
  unwrap(await supabase.from("settings").upsert({ key, value }), "Einstellung speichern");
}

/** Usage rows from `fromDay` on; `null` when the table cannot be read (not migrated or no right to read). */
export async function sourceUsageSince(fromDay: string): Promise<SourceUsage[] | null> {
  const result = await supabase.from("source_usage").select("day, source, count").gte("day", fromDay);
  if (result.error !== null) return null;
  return result.data;
}

/** Requests from the form on repocity.speedofthespirit.dev/anfrage/ (crm.open_audit_requests, administrator only). */
export async function listOpenAuditRequests(): Promise<OpenAuditRequest[]> {
  const rows = unwrap(await supabase.schema("crm").rpc("open_audit_requests"), "Offene Anfragen laden");
  return Array.isArray(rows) ? (rows as OpenAuditRequest[]) : [];
}

export async function isAdmin(): Promise<boolean> {
  return unwrap(await supabase.rpc("is_admin"), "Rolle prüfen") === true;
}
