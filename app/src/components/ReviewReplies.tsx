// "Lass mich überlegen": five reply drafts to real unanswered reviews of the audited business, as a
// PDF Daniel shows the customer. The server writes them; nothing is posted on Google.
import { documentUrl, listReplyDocs, requestReviewReplies } from "../api/replies";
import { useAction, type Action } from "../hooks/useAsync";
import { useLive } from "../hooks/useLive";
import { pdfState, type PdfState } from "../lib/offerDocs";
import { latestReplyDoc, replyCount, repliesPossible } from "../lib/reviewReplies";
import { openInNewTab } from "./openUrl";
import { ServerStatusChip } from "./ServerStatusChip";
import { ErrorAlert } from "./ui";

interface ReviewRepliesProps {
  auditId: string;
  auditStatus: string;
  customerId: string;
}

function ReplyState({ state }: { state: PdfState }) {
  if (state.kind === "pending") {
    return (
      <>
        <p class="pending" role="status">
          <span class="spinner" aria-hidden="true" />
          Die Antwortentwürfe werden geschrieben …
        </p>
        <ServerStatusChip offlineNote="die Entwürfe entstehen, sobald der Server läuft." />
      </>
    );
  }
  if (state.kind !== "failed") return null;
  return (
    <p class="notice notice--bad" role="alert">
      Keine Entwürfe: {state.error}
    </p>
  );
}

interface ReplyButtonsProps {
  state: PdfState;
  action: Action;
  onRequest: () => void;
}

function ReplyButtons({ state, action, onRequest }: ReplyButtonsProps) {
  const open = (path: string) =>
    void action.run(async () => {
      await openInNewTab(() => documentUrl(path));
    });
  return (
    <div class="cluster">
      {state.kind === "ready" && (
        <button type="button" class="btn btn--primary" disabled={action.busy} onClick={() => { open(state.path); }}>
          {`${String(replyCount(state.doc))} Antwortentwürfe öffnen (PDF)`}
        </button>
      )}
      {state.kind !== "pending" && (
        <button type="button" class={state.kind === "none" ? "btn btn--primary" : "btn btn--ghost"} data-write disabled={action.busy} onClick={onRequest}>
          {state.kind === "none" ? "Antwortentwürfe erstellen (PDF)" : "Neu anfordern"}
        </button>
      )}
    </div>
  );
}

function useReplyDoc(auditId: string, customerId: string, possible: boolean) {
  return useLive(
    possible ? `antworten-${auditId}` : null,
    async () => latestReplyDoc(await listReplyDocs(customerId), auditId),
    [{ table: "documents", filter: `customer_id=eq.${customerId}` }],
    (doc) => pdfState(doc).kind === "pending",
  );
}

export function ReviewReplies({ auditId, auditStatus, customerId }: ReviewRepliesProps) {
  const possible = repliesPossible(auditStatus);
  const live = useReplyDoc(auditId, customerId, possible);
  const action = useAction();
  if (!possible) return null;
  const state = pdfState(live.data ?? null);
  const request = () =>
    void action.run(async () => {
      await requestReviewReplies(customerId, auditId);
      live.reload();
    });
  return (
    <section class="panel stack" aria-labelledby="review-replies">
      <h2 id="review-replies">Antwortentwürfe für den Kunden</h2>
      <p class="muted">
        Bis zu fünf Antworten auf echte Bewertungen ohne Antwort des Inhabers, als PDF zum Zeigen, wenn der Kunde noch überlegen
        möchte. Bei Google wird nichts veröffentlicht.
      </p>
      <ReplyState state={state} />
      <ReplyButtons state={state} action={action} onRequest={request} />
      <ErrorAlert message={action.error ?? live.error} />
    </section>
  );
}
