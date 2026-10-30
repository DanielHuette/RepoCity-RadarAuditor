import { useAction } from "../hooks/useAsync";
import { crmCustomerUrl } from "../lib/crm";
import { openInNewTab } from "./openUrl";

/** Opens the customer's file in the CRM in a new tab; nothing for customers not linked to the CRM. */
export function CrmButton({ crmCustomerId }: { crmCustomerId: string | null }) {
  const opening = useAction();
  const url = crmCustomerUrl(crmCustomerId);
  if (url === null) return null;
  return (
    <div class="stack stack--tight">
      <button
        type="button"
        class="btn btn--ghost"
        disabled={opening.busy}
        onClick={() =>
          void opening.run(async () => {
            await openInNewTab(() => Promise.resolve(url));
          })
        }
      >
        Kundenakte im CRM öffnen
      </button>
      {opening.error !== null && <p class="field__error">{opening.error}</p>}
    </div>
  );
}
