import type { Audit, CheckOutcome, CheckResult, Customer, Doc, Finding, Keyword, Ranking } from "../api/types";
import type { CatalogueCheck } from "./catalogue";

let sequence = 0;

function nextId(prefix: string): string {
  sequence += 1;
  return `${prefix}-${String(sequence)}`;
}

export function check(area: string, outcome: CheckOutcome, checkId = `${area}1`): CheckResult {
  return { id: nextId("check"), audit_id: "audit-1", check_id: checkId, area, outcome, measured: {}, weight: 1, potential_points: null, uplift_low: null, uplift_high: null };
}

export function finding(overrides: Partial<Finding>): Finding {
  return {
    id: nextId("finding"),
    audit_id: "audit-1",
    check_id: "a1",
    severity: 1,
    title: "Titel",
    explanation: "Erklärung",
    customer_impact: "Wirkung",
    fix: "Lösung",
    fix_payload: null,
    ...overrides,
  };
}

export function doc(overrides: Partial<Doc> = {}): Doc {
  return {
    id: nextId("doc"),
    customer_id: "customer-1",
    kind: "audit_quote",
    number: null,
    issued_on: "2026-10-01",
    net_amount: null,
    vat_rate: null,
    gross_amount: null,
    data: {},
    file_path: "customer-1/doc/audit_quote_AN-2026-0001.pdf",
    created_at: "2026-10-01T08:00:00Z",
    ...overrides,
  };
}

export function ranking(overrides: Partial<Ranking>): Ranking {
  return {
    id: nextId("ranking"),
    audit_id: "audit-1",
    keyword_id: "keyword-1",
    subject: "Dachdecker Muster",
    organic_position: null,
    local_pack_position: null,
    grid_point: null,
    measured_at: "2026-10-01T08:00:00Z",
    ...overrides,
  };
}

export function keyword(id: string, phrase: string): Keyword {
  return { id, customer_id: "customer-1", phrase, city: "Münster", monthly_volume: null, is_primary: false };
}

export function audit(overrides: Partial<Audit>): Audit {
  return {
    id: nextId("audit"),
    customer_id: "customer-1",
    kind: "remote",
    status: "reported",
    requested_at: "2026-10-01T08:00:00Z",
    finished_at: null,
    total_score: null,
    report_path: null,
    error: null,
    ...overrides,
  };
}

export function customer(overrides: Partial<Customer>): Customer {
  return {
    id: nextId("customer"),
    name: "Dachdecker Muster",
    website: "https://dachdecker-muster.de/",
    industry: "bauhandwerk",
    city: "Münster",
    order_value_eur: null,
    created_at: "2026-10-01T08:00:00Z",
    crm_customer_id: null,
    ...overrides,
  };
}

export function catalogueCheck(id: string, overrides: Partial<CatalogueCheck> = {}): CatalogueCheck {
  return {
    id,
    area: id.slice(0, 1),
    name: `Prüfung ${id}`,
    why: "Weil.",
    source: "crawler",
    weight: 1,
    requiresPaid: false,
    requiresAccess: false,
    customerQuestion: "",
    customerImpact: "",
    ...overrides,
  };
}
