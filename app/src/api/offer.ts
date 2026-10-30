// Reads and writes of the Angebot page: prices and quotes in schema crm, PDF requests, optimisation drafts.
// The CRM sends every mail: crm.send_quote marks the quote sent and queues the mail with the audit-plus-quote
// PDF attached; it raises when the CRM customer has no e-mail address.
//
// crm.create_quote(p_customer, p_deal) only creates the quote head with default texts; the items are
// rows of crm.quote_items (a trigger keeps net, VAT and gross of the quote current) and the texts are
// set on the quote afterwards. Every crm table and function is admin-only (crm.require_writer, RLS).
import type { PostgrestError } from "@supabase/supabase-js";
import { NO_SINGLE_ROW, NoticeError, QueryError, UNIQUE_VIOLATION } from "../lib/errors";
import { quotePdfData } from "../lib/offerDocs";
import type { CheckOutcomeRow, QuoteItemRow } from "../lib/quote";
import { supabase } from "./supabase";
import type {
  AuditWithCustomer,
  CrmOrder,
  CrmProduct,
  CrmQuote,
  CrmQuoteItem,
  Customer,
  Doc,
  Job,
  OptimizationChange,
} from "./types";

interface Result {
  data: unknown;
  error: PostgrestError | null;
}

function unwrap(result: Result, action: string): unknown {
  if (result.error !== null) throw new QueryError(action, result.error.message, result.error.code);
  return result.data;
}

const crm = () => supabase.schema("crm");

type StoredProduct = Omit<CrmProduct, "radar_areas"> & { radar_areas?: string[] | null };

export interface OfferBasis {
  audit: AuditWithCustomer;
  customer: Customer;
  checks: CheckOutcomeRow[];
  products: CrmProduct[];
  smallBusiness: boolean;
}

export async function loadOfferBasis(auditId: string): Promise<OfferBasis | null> {
  const audit = unwrap(
    await supabase.from("audits").select("*, customers(name)").eq("id", auditId).maybeSingle(),
    "Prüfung laden",
  ) as AuditWithCustomer | null;
  if (audit === null) return null;
  const [customer, checks, products, settings] = await Promise.all([
    supabase.from("customers").select("*").eq("id", audit.customer_id).single(),
    supabase.from("check_results").select("check_id, area, outcome").eq("audit_id", auditId),
    crm().from("products").select("*").eq("active", true).order("name"),
    crm().from("settings").select("small_business").eq("id", 1).maybeSingle(),
  ]);
  const smallBusiness = unwrap(settings, "CRM-Einstellungen laden") as { small_business: boolean } | null;
  return {
    audit,
    customer: unwrap(customer, "Kunde laden") as Customer,
    checks: unwrap(checks, "Prüfergebnisse laden") as CheckOutcomeRow[],
    // radar_areas arrives with migration 006; before that the column is absent.
    products: (unwrap(products, "Preise laden") as StoredProduct[]).map((product) => ({
      ...product,
      radar_areas: product.radar_areas ?? [],
    })),
    smallBusiness: smallBusiness?.small_business ?? false,
  };
}

export interface NewQuote {
  crmCustomerId: string;
  intro: string;
  outro: string;
  items: (quoteId: string) => QuoteItemRow[];
}

/** Creates the quote with texts and items; a quote whose items could not be stored is removed again. */
export async function createQuote({ crmCustomerId, intro, outro, items }: NewQuote): Promise<CrmQuote> {
  const created = unwrap(await crm().rpc("create_quote", { p_customer: crmCustomerId }), "Angebot erstellen") as CrmQuote;
  try {
    unwrap(await crm().from("quote_items").insert(items(created.id)), "Positionen speichern");
    return unwrap(
      await crm().from("quotes").update({ intro, outro }).eq("id", created.id).select().single(),
      "Angebotstexte speichern",
    ) as CrmQuote;
  } catch (reason) {
    await crm().from("quotes").delete().eq("id", created.id);
    throw reason;
  }
}

export interface QuoteView {
  quote: CrmQuote;
  items: CrmQuoteItem[];
  order: CrmOrder | null;
}

export async function getQuoteView(quoteId: string): Promise<QuoteView | null> {
  const [quote, items, order] = await Promise.all([
    crm().from("quotes").select("*").eq("id", quoteId).maybeSingle(),
    crm().from("quote_items").select("*").eq("quote_id", quoteId).order("position"),
    crm().from("orders").select("*").eq("quote_id", quoteId).maybeSingle(),
  ]);
  const head = unwrap(quote, "Angebot laden") as CrmQuote | null;
  if (head === null) return null;
  return {
    quote: head,
    items: unwrap(items, "Positionen laden") as CrmQuoteItem[],
    order: unwrap(order, "Auftrag laden") as CrmOrder | null,
  };
}

export async function sendQuote(quoteId: string): Promise<void> {
  unwrap(await crm().rpc("send_quote", { p_quote: quoteId }), "Angebot abschicken");
}

/** E-mail address of the CRM customer, or null when none is stored. */
export async function loadCrmCustomerEmail(crmCustomerId: string): Promise<string | null> {
  const result = await crm().from("customers").select("email").eq("id", crmCustomerId).maybeSingle();
  const row = unwrap(result, "E-Mail-Adresse laden") as { email: string | null } | null;
  const email = row?.email?.trim() ?? "";
  return email === "" ? null : email;
}

const EMAIL_TAKEN = "Diese E-Mail-Adresse gehört schon zu einem anderen Kunden im CRM.";
const CUSTOMER_GONE = "Diesen Kunden gibt es im CRM nicht mehr.";

/** Stores the address in lower case; `.single()` confirms that exactly the customer's row changed. */
export async function saveCrmCustomerEmail(crmCustomerId: string, email: string): Promise<void> {
  const result = await crm().from("customers").update({ email: email.toLowerCase() }).eq("id", crmCustomerId).select("id").single();
  if (result.error?.code === UNIQUE_VIOLATION) throw new NoticeError(EMAIL_TAKEN);
  if (result.error?.code === NO_SINGLE_ROW) throw new NoticeError(CUSTOMER_GONE);
  unwrap(result, "E-Mail-Adresse speichern");
}

export async function acceptQuote(quoteId: string): Promise<CrmOrder> {
  return unwrap(await crm().rpc("accept_quote", { p_quote: quoteId }), "Angebot annehmen") as CrmOrder;
}

export async function declineQuote(quoteId: string): Promise<CrmQuote> {
  return unwrap(await crm().rpc("decline_quote", { p_quote: quoteId }), "Angebot ablehnen") as CrmQuote;
}

/** Newest quote of the CRM customer created since the audit, so a reopened page finds it without a PDF request. */
export async function latestQuoteSince(crmCustomerId: string | null, since: string): Promise<string | null> {
  if (crmCustomerId === null) return null;
  const result = await crm()
    .from("quotes")
    .select("id")
    .eq("customer_id", crmCustomerId)
    .gte("created_at", since)
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  const row = unwrap(result, "Angebot suchen") as { id: string } | null;
  return row?.id ?? null;
}

/** Audit-plus-quote PDF requests of a customer, newest first. */
export async function listQuotePdfs(customerId: string): Promise<Doc[]> {
  const result = await supabase
    .from("documents")
    .select("*")
    .eq("customer_id", customerId)
    .eq("kind", "audit_quote")
    .order("created_at", { ascending: false });
  return unwrap(result, "PDF laden") as Doc[];
}

export async function requestQuotePdf(customerId: string, auditId: string, quote: CrmQuote): Promise<Doc> {
  const result = await supabase
    .from("documents")
    .insert({ kind: "audit_quote", customer_id: customerId, data: quotePdfData(auditId, quote.id, quote.number), file_path: null, number: null })
    .select()
    .single();
  return unwrap(result, "PDF anfordern") as Doc;
}

export async function downloadDocument(path: string): Promise<Blob> {
  const { data, error } = await supabase.storage.from("documents").download(path);
  if (error !== null) throw new QueryError("PDF laden", error.message, null);
  return data;
}

export async function startOptimization(orderId: string): Promise<string> {
  const id = unwrap(await supabase.rpc("start_optimization", { p_order_id: orderId }), "Optimierung starten");
  if (typeof id !== "string") throw new QueryError("Optimierung starten", "keine Auftragsnummer erhalten", null);
  return id;
}

export async function decideChange(changeId: string, approve: boolean): Promise<OptimizationChange> {
  const action = approve ? "Änderung freigeben" : "Änderung ablehnen";
  return unwrap(await supabase.rpc("decide_change", { p_change_id: changeId, p_approve: approve }), action) as OptimizationChange;
}

/** Ticks a to-do by hand off (done) or opens it again; admin-only like every write. */
export async function markManualDone(changeId: string, done: boolean): Promise<OptimizationChange> {
  const action = done ? "Als erledigt abhaken" : "Wieder öffnen";
  return unwrap(await supabase.rpc("mark_manual_done", { p_change_id: changeId, p_done: done }), action) as OptimizationChange;
}

export interface OrderWork {
  jobs: Job[];
  changes: OptimizationChange[];
}

export async function loadOrderWork(orderId: string): Promise<OrderWork> {
  const [jobs, changes] = await Promise.all([
    supabase.from("jobs").select("*").eq("order_id", orderId).order("created_at"),
    supabase.from("optimization_changes").select("*").eq("order_id", orderId).order("created_at"),
  ]);
  return {
    jobs: unwrap(jobs, "Optimierung laden") as Job[],
    changes: unwrap(changes, "Änderungen laden") as OptimizationChange[],
  };
}
