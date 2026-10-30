// Address of a customer's file in the CRM. customers.crm_customer_id holds crm.customers.id
// (migration 006), the id the CRM route /kunde/:id loads.
import { crmOrigin } from "../api/config";

export function crmCustomerUrl(crmCustomerId: string | null | undefined): string | null {
  const origin = crmOrigin();
  const id = crmCustomerId?.trim() ?? "";
  return origin === null || id === "" ? null : `${origin}/kunde/${encodeURIComponent(id)}`;
}
