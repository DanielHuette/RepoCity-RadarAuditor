import { useEffect, useState } from "preact/hooks";
import { loadCrmCustomerEmail, saveCrmCustomerEmail, sendQuote } from "../../api/offer";
import type { CrmQuote } from "../../api/types";
import { ErrorAlert, Field } from "../../components/ui";
import { useAction, type Action } from "../../hooks/useAsync";
import type { PdfState } from "../../lib/offerDocs";
import { cleanEmail, stepOnConfirm, stepWhileWaiting, type SendStep } from "../../lib/quoteSending";

const INVALID_EMAIL = "Bitte eine gültige E-Mail-Adresse eingeben.";
/** The app sends only while it is open, so a wait without end would keep the operator on the page. */
const PDF_WAIT_MS = 5 * 60 * 1000;

interface SendQuoteProps {
  quote: CrmQuote;
  pdf: PdfState["kind"];
  onRequestPdf: () => Promise<void>;
  onSent: () => void;
}

/** The customer's address from the CRM; a missing one is typed in and stored in the CRM before sending. */
function useRecipient(crmCustomerId: string) {
  const [known, setKnown] = useState<string | null | undefined>(undefined);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | undefined>(undefined);
  const load = async () => {
    setKnown(await loadCrmCustomerEmail(crmCustomerId));
  };
  const settle = async (): Promise<boolean> => {
    if (known != null) return true;
    const email = cleanEmail(typed)?.toLowerCase() ?? null;
    setError(email === null ? INVALID_EMAIL : undefined);
    if (email === null) return false;
    await saveCrmCustomerEmail(crmCustomerId, email);
    setKnown(email);
    return true;
  };
  return { known, typed, setTyped, error, load, settle, close: () => { setKnown(undefined); } };
}

type WaitEnd = "none" | "pdf_failed" | "timed_out";

/** Ends a wait for the PDF after PDF_WAIT_MS; leaving the page ends it too, and nothing is sent. */
function useWaitLimit(waiting: boolean, end: (reason: WaitEnd) => void) {
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setTimeout(() => {
      end("timed_out");
    }, PDF_WAIT_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [waiting]);
}


/** Hands every change of the PDF during a wait to `onChange` with the step it means. */
function usePdfWatch(waiting: boolean, pdf: PdfState["kind"], requested: boolean, onChange: (step: SendStep) => void) {
  useEffect(() => {
    if (waiting) onChange(stepWhileWaiting(pdf, requested));
  }, [waiting, pdf]);
}

/** Sends at once when the PDF is ready, otherwise requests it and sends as soon as it is. */
function useSendFlow({ quote, pdf, onRequestPdf, onSent }: SendQuoteProps, action: Action) {
  const [waiting, setWaiting] = useState(false);
  const [requested, setRequested] = useState(false);
  const [ended, setEnded] = useState<WaitEnd>("none");
  const stopWaiting = (end: WaitEnd) => {
    setWaiting(false);
    setEnded(end);
  };
  useWaitLimit(waiting, stopWaiting);
  const send = async () => {
    setWaiting(false);
    await sendQuote(quote.id);
    onSent();
  };
  usePdfWatch(waiting, pdf, requested, (step) => {
    if (pdf === "pending") setRequested(false);
    if (step === "send") void action.run(send);
    if (step === "pdf_failed") stopWaiting("pdf_failed");
  });
  const start = async () => {
    setEnded("none");
    const step = stepOnConfirm(pdf);
    if (step === "send") return send();
    if (step === "request_pdf") await onRequestPdf();
    setRequested(step === "request_pdf");
    setWaiting(true);
  };
  return { waiting, ended, start, cancel: () => { stopWaiting("none"); } };
}

function Recipient({ recipient, number }: { recipient: ReturnType<typeof useRecipient>; number: string }) {
  if (recipient.known != null) {
    return (
      <p>
        Angebot {number} geht mit dem Prüfbericht als PDF an <strong>{recipient.known}</strong>. Die Mail verschickt das CRM.
      </p>
    );
  }
  return (
    <Field
      label="E-Mail-Adresse des Kunden"
      type="email"
      autoComplete="email"
      value={recipient.typed}
      onInput={recipient.setTyped}
      hint="Im CRM ist noch keine Adresse hinterlegt. Sie wird dort beim Abschicken gespeichert."
      error={recipient.error}
    />
  );
}

const WAIT_END_NOTES: Record<Exclude<WaitEnd, "none">, string> = {
  pdf_failed: "Das PDF konnte nicht erstellt werden. Ohne PDF geht das Angebot nicht raus – versuch es noch einmal.",
  timed_out: "Das PDF ist nach fünf Minuten nicht fertig. Das Angebot ist nicht verschickt. Versuch es später noch einmal.",
};

function SendNotes({ waiting, ended, onCancel }: { waiting: boolean; ended: WaitEnd; onCancel: () => void }) {
  return (
    <>
      {waiting && (
        <div class="stack stack--tight">
          <p class="pending" role="status">
            <span class="spinner" aria-hidden="true" />
            Das PDF wird erstellt, danach geht das Angebot raus. Lass die Seite so lange offen. Verlässt du die Seite, wird nichts verschickt.
          </p>
          <div class="cluster">
            <button type="button" class="btn btn--ghost" onClick={onCancel}>
              Abbrechen
            </button>
          </div>
        </div>
      )}
      {ended !== "none" && (
        <p class="notice notice--bad" role="alert">
          {WAIT_END_NOTES[ended]}
        </p>
      )}
    </>
  );
}

interface DialogProps {
  quote: CrmQuote;
  recipient: ReturnType<typeof useRecipient>;
  busy: boolean;
  onConfirm: () => void;
}

function SendDialog({ quote, recipient, busy, onConfirm }: DialogProps) {
  return (
    <div class="stack stack--tight" role="group" aria-label="Angebot abschicken">
      <Recipient recipient={recipient} number={quote.number} />
      <div class="cluster">
        <button type="button" class="btn btn--primary" disabled={busy} onClick={onConfirm}>
          Jetzt abschicken
        </button>
        <button type="button" class="btn btn--ghost" disabled={busy} onClick={recipient.close}>
          Abbrechen
        </button>
      </div>
    </div>
  );
}

export function SendQuote(props: SendQuoteProps) {
  const action = useAction();
  const recipient = useRecipient(props.quote.customer_id);
  const sent = () => {
    recipient.close();
    props.onSent();
  };
  const flow = useSendFlow({ ...props, onSent: sent }, action);
  const open = () => void action.run(recipient.load);
  const confirm = () =>
    void action.run(async () => {
      if (await recipient.settle()) await flow.start();
    });
  const dialogOpen = recipient.known !== undefined && !flow.waiting;
  return (
    <div class="stack stack--tight" data-write>
      <SendNotes waiting={flow.waiting} ended={flow.ended} onCancel={flow.cancel} />
      <ErrorAlert message={action.error} />
      {dialogOpen ? (
        <SendDialog quote={props.quote} recipient={recipient} busy={action.busy} onConfirm={confirm} />
      ) : (
        !flow.waiting && (
          <button type="button" class="btn btn--primary" disabled={action.busy} onClick={open}>
            Angebot abschicken
          </button>
        )
      )}
    </div>
  );
}
