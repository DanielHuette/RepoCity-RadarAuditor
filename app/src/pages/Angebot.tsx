import { useLocation, useRoute } from "preact-iso";
import { latestQuoteSince, listQuotePdfs, loadOfferBasis } from "../api/offer";
import { listRecentAudits } from "../api/queries";
import { CrmButton } from "../components/CrmButton";
import { RecentAudits } from "../components/RecentAudits";
import { ErrorNote, Loaded, PageHead } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatDateTime, formatScore } from "../lib/format";
import { quoteIdOfAudit } from "../lib/offerDocs";
import { OfferDraft } from "./angebot/OfferDraft";
import { QuotePanel } from "./angebot/QuotePanel";

function AuditChoice() {
  const recent = useAsync(listRecentAudits, []);
  return (
    <>
      <PageHead title="Angebot" sub="Wähle eine Prüfung; die Positionen kommen aus ihren offenen Punkten, die Preise aus dem CRM." />
      <section class="panel" aria-labelledby="offer-choice-title">
        <h2 id="offer-choice-title">Letzte Prüfungen</h2>
        <Loaded state={recent} label="Prüfungen werden geladen …">
          {(audits) => <RecentAudits audits={audits} hrefFor={(audit) => `/angebot/${audit.id}`} />}
        </Loaded>
      </section>
    </>
  );
}

async function loadOffer(auditId: string) {
  const basis = await loadOfferBasis(auditId);
  if (basis === null) return null;
  const [pdfs, latestQuote] = await Promise.all([
    listQuotePdfs(basis.customer.id),
    latestQuoteSince(basis.customer.crm_customer_id, basis.audit.requested_at),
  ]);
  return { basis, pdfs, latestQuote };
}

type LoadedOffer = NonNullable<Awaited<ReturnType<typeof loadOffer>>>;

/**
 * The quote id lives in the address; a reopened page finds it through the PDF request or the
 * newest CRM quote since the audit, so a second visit never creates a duplicate.
 */
function quoteIdOf(loaded: LoadedOffer, auditId: string, inAddress: string | undefined): string | null {
  return inAddress ?? quoteIdOfAudit(loaded.pdfs, auditId) ?? loaded.latestQuote;
}

function OfferView({ auditId, loaded, quoteInAddress }: { auditId: string; loaded: LoadedOffer; quoteInAddress: string | undefined }) {
  const { route } = useLocation();
  const { basis } = loaded;
  const { audit, customer } = basis;
  const quoteId = quoteIdOf(loaded, auditId, quoteInAddress);
  return (
    <>
      <a class="back" href={`/auswertung/${auditId}`}>
        Zur Auswertung
      </a>
      <PageHead
        title={`Angebot für ${audit.customers?.name ?? customer.name}`}
        sub={`Prüfung vom ${formatDateTime(audit.requested_at)} · ${formatScore(audit.total_score)} von 100 Punkten`}
      >
        <CrmButton crmCustomerId={customer.crm_customer_id} />
      </PageHead>
      {quoteId === null ? (
        <OfferDraft
          basis={basis}
          onCreated={(quote) => {
            route(`/angebot/${auditId}?angebot=${quote.id}`);
          }}
        />
      ) : (
        <QuotePanel quoteId={quoteId} auditId={auditId} customer={customer} smallBusiness={basis.smallBusiness} />
      )}
    </>
  );
}

function OfferPage({ auditId }: { auditId: string }) {
  const { query } = useLocation();
  const state = useAsync(() => loadOffer(auditId), [auditId]);
  return (
    <Loaded state={state} label="Angebot wird vorbereitet …">
      {(loaded) =>
        loaded === null ? (
          <ErrorNote message="Diese Prüfung gibt es nicht mehr." />
        ) : (
          <OfferView auditId={auditId} loaded={loaded} quoteInAddress={query.angebot} />
        )
      }
    </Loaded>
  );
}

export function Angebot() {
  const { params } = useRoute();
  const auditId = params.auditId;
  return auditId === undefined || auditId === "" ? <AuditChoice /> : <OfferPage auditId={auditId} />;
}
