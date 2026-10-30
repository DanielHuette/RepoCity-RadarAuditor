import { useEffect, useState } from "preact/hooks";
import {
  acceptQuote,
  declineQuote,
  downloadDocument,
  getQuoteView,
  listQuotePdfs,
  requestQuotePdf,
  type QuoteView,
} from "../../api/offer";
import type { CrmQuote, Customer } from "../../api/types";
import { ServerStatusChip } from "../../components/ServerStatusChip";
import { ErrorAlert, ErrorNote, Loaded } from "../../components/ui";
import { useAction, useAsync } from "../../hooks/useAsync";
import { useLive } from "../../hooks/useLive";
import { describeError } from "../../lib/errors";
import { formatDate, formatEuro } from "../../lib/format";
import { latestQuotePdf, pdfState, quotePdfName, type PdfState } from "../../lib/offerDocs";
import { QUOTE_STATUS_LABELS, QUOTE_STATUS_TONES } from "../../lib/quote";
import { OptimizationPanel } from "./OptimizationPanel";
import { SendQuote } from "./SendQuote";
import { Totals } from "./Totals";

function saveFile(file: File) {
  const url = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = url;
  link.download = file.name;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 0);
}

/** Loads the finished PDF ahead of the tap, because the share sheet only opens straight from a tap. */
function usePdfFile(state: PdfState, name: string): { file: File | null; error: string | null } {
  const path = state.kind === "ready" ? state.path : null;
  const [loaded, setLoaded] = useState<{ path: string; file: File } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (path === null || loaded?.path === path) return;
    let active = true;
    setError(null);
    downloadDocument(path).then(
      (blob) => {
        if (active) setLoaded({ path, file: new File([blob], name, { type: "application/pdf" }) });
      },
      (reason: unknown) => {
        if (active) setError(describeError(reason));
      },
    );
    return () => {
      active = false;
    };
  }, [path, name]);
  return { file: loaded !== null && loaded.path === path ? loaded.file : null, error };
}

interface PdfSectionProps {
  view: QuoteView;
  auditId: string;
  customer: Customer;
  onChanged: () => void;
}

function useQuotePdf(quote: CrmQuote, customer: Customer) {
  const pdf = useLive(
    `pdf-${quote.id}`,
    async () => latestQuotePdf(await listQuotePdfs(customer.id), quote.id),
    [{ table: "documents", filter: `customer_id=eq.${customer.id}` }],
    (doc) => pdfState(doc).kind === "pending",
  );
  const state = pdf.data === undefined ? null : pdfState(pdf.data);
  const { file, error: fileError } = usePdfFile(state ?? { kind: "none" }, quotePdfName(quote.number, customer.name));
  return { pdf, state, file, fileError };
}

function usePdfRequest(quote: CrmQuote, auditId: string, customerId: string, onRequested: () => void) {
  const action = useAction();
  const requestPdf = async () => {
    await requestQuotePdf(customerId, auditId, quote);
    onRequested();
  };
  return { action, requestPdf, request: () => void action.run(requestPdf) };
}

function PdfNotes({ state, fileError }: { state: PdfState; fileError: string | null }) {
  return (
    <>
      {state.kind === "pending" && (
        <>
          <p class="pending" role="status">
            <span class="spinner" aria-hidden="true" />
            PDF wird auf dem Server erstellt …
          </p>
          <ServerStatusChip offlineNote="das PDF entsteht, sobald der Server läuft." />
        </>
      )}
      {state.kind === "failed" && (
        <p class="notice notice--bad" role="alert">
          PDF konnte nicht erstellt werden: {state.error}
        </p>
      )}
      {state.kind === "ready" && fileError !== null && <p class="field__error">{fileError}</p>}
    </>
  );
}

interface PdfButtonsProps {
  state: PdfState;
  file: File | null;
  busy: boolean;
  onRequest: () => void;
}

function PdfButtons({ state, file, busy, onRequest }: PdfButtonsProps) {
  return (
    <div class="cluster">
      {(state.kind === "none" || state.kind === "failed") && (
        <button type="button" class="btn btn--ghost" data-write disabled={busy} onClick={onRequest}>
          {state.kind === "failed" ? "PDF neu anfordern" : "PDF erzeugen"}
        </button>
      )}
      {state.kind === "ready" && (
        <button
          type="button"
          class="btn btn--ghost"
          disabled={file === null}
          onClick={() => {
            if (file !== null) saveFile(file);
          }}
        >
          {file === null ? "PDF wird geladen …" : "PDF herunterladen"}
        </button>
      )}
    </div>
  );
}

function PdfSection({ view, auditId, customer, onChanged }: PdfSectionProps) {
  const { quote } = view;
  const { pdf, state, file, fileError } = useQuotePdf(quote, customer);
  const { action, requestPdf, request } = usePdfRequest(quote, auditId, customer.id, pdf.reload);
  if (state === null) return pdf.error === null ? <p class="muted small">PDF-Stand wird geladen …</p> : <ErrorNote message={pdf.error} onRetry={pdf.reload} />;
  return (
    <div class="stack stack--tight">
      <h3>Prüfbericht mit Angebot als PDF</h3>
      <PdfNotes state={state} fileError={fileError} />
      <ErrorAlert message={action.error} />
      <PdfButtons state={state} file={file} busy={action.busy} onRequest={request} />
      {quote.status === "draft" && <SendQuote quote={quote} pdf={state.kind} onRequestPdf={requestPdf} onSent={onChanged} />}
    </div>
  );
}

function useQuoteDecision(quote: CrmQuote, onChanged: () => void) {
  const action = useAction();
  const decide = (question: string, record: (quoteId: string) => Promise<unknown>) => {
    if (!window.confirm(question)) return;
    void action.run(async () => {
      await record(quote.id);
      onChanged();
    });
  };
  const accept = () => {
    decide(`Angebot ${quote.number} als angenommen eintragen? Im CRM entsteht dabei der Auftrag.`, acceptQuote);
  };
  const decline = () => {
    decide(`Angebot ${quote.number} als abgelehnt eintragen?`, declineQuote);
  };
  return { action, accept, decline };
}

function DecisionButtons({ view, onChanged }: { view: QuoteView; onChanged: () => void }) {
  const { quote } = view;
  const { action, accept, decline } = useQuoteDecision(quote, onChanged);
  if (quote.status !== "draft" && quote.status !== "sent") return null;
  return (
    <div class="stack stack--tight" data-write>
      <h3>Entscheidung des Kunden</h3>
      <ErrorAlert message={action.error} />
      <div class="cluster">
        <button type="button" class="btn btn--primary" disabled={action.busy} onClick={accept}>
          Angenommen
        </button>
        <button type="button" class="btn btn--ghost" disabled={action.busy} onClick={decline}>
          Abgelehnt
        </button>
      </div>
    </div>
  );
}

function QuoteItems({ items }: { items: QuoteView["items"] }) {
  return (
    <ul class="list">
      {items.map((item) => (
        <li key={item.id} class="row">
          <span class="row__main">
            <span class="row__title">{item.title}</span>
            <span class="row__meta">
              {item.quantity} × {formatEuro(item.unit_price_net)} je {item.unit}
            </span>
          </span>
          <span class="num">{formatEuro(Math.round(item.quantity * item.unit_price_net * 100) / 100)}</span>
        </li>
      ))}
    </ul>
  );
}

function QuoteSummary({ view, smallBusiness }: { view: QuoteView; smallBusiness: boolean }) {
  const { quote, items, order } = view;
  return (
    <>
      <div class="split">
        <div>
          <h2 id="quote-title">Angebot {quote.number}</h2>
          <p class="muted small">gültig bis {formatDate(quote.valid_until)}</p>
        </div>
        <span class={`badge ${QUOTE_STATUS_TONES[quote.status]}`}>{QUOTE_STATUS_LABELS[quote.status]}</span>
      </div>
      <QuoteItems items={items} />
      <Totals totals={quote} smallBusiness={smallBusiness} />
      {order !== null && (
        <p class="notice notice--ok" role="status">
          Auftrag {order.number} ist im CRM angelegt{order.due_date === null ? "." : `, fällig am ${formatDate(order.due_date)}.`}
        </p>
      )}
    </>
  );
}

interface QuotePanelProps {
  quoteId: string;
  auditId: string;
  customer: Customer;
  smallBusiness: boolean;
}

export function QuotePanel({ quoteId, auditId, customer, smallBusiness }: QuotePanelProps) {
  const state = useAsync(() => getQuoteView(quoteId), [quoteId]);
  return (
    <Loaded state={state} label="Angebot wird geladen …">
      {(view) =>
        view === null ? (
          <ErrorNote message="Dieses Angebot gibt es im CRM nicht mehr." />
        ) : (
          <div class="stack">
            <section class="panel stack" aria-labelledby="quote-title">
              <QuoteSummary view={view} smallBusiness={smallBusiness} />
              <PdfSection view={view} auditId={auditId} customer={customer} onChanged={state.reload} />
              <DecisionButtons view={view} onChanged={state.reload} />
            </section>
            {view.order !== null && <OptimizationPanel order={view.order} />}
          </div>
        )
      }
    </Loaded>
  );
}
