// Open audit requests below the Prüfen button: closed by default and absent while there are none,
// so the tab stays address field and button. A request with a website fills the address field;
// the check itself still starts only with the Prüfen button.
import { useRef } from "preact/hooks";
import { listOpenAuditRequests } from "../api/queries";
import type { OpenAuditRequest } from "../api/types";
import { useIsAdmin } from "../hooks/role";
import { useAsync } from "../hooks/useAsync";
import { auditWebsite, formatWish, meetingLabel, openRequestsLabel, sortByWish } from "../lib/auditRequests";
import { hostOf } from "../lib/format";

function RequestText({ request, website }: { request: OpenAuditRequest; website: string | null }) {
  return (
    <>
      <span class="request__title">{request.company}</span>
      <span class="request__meta">
        {request.contact} · {meetingLabel(request.kind)}
      </span>
      <span class="request__meta">{formatWish(request.wished_at)}</span>
      <span class={website === null ? "request__meta" : "request__site"}>
        {website === null ? "Keine Webseite angegeben" : hostOf(website).replace(/^www\./, "")}
      </span>
    </>
  );
}

function RequestRow({ request, onPick }: { request: OpenAuditRequest; onPick: (website: string) => void }) {
  const website = auditWebsite(request);
  return (
    <li>
      {website === null ? (
        <div class="request request--still">
          <RequestText request={request} website={null} />
        </div>
      ) : (
        <button type="button" class="request" onClick={() => { onPick(website); }}>
          <RequestText request={request} website={website} />
        </button>
      )}
    </li>
  );
}

function RequestsFailed({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <details class="requests">
      <summary class="requests__summary">Offene Anfragen nicht geladen</summary>
      <div class="requests__body">
        <p class="field__error" role="alert">
          {message}
        </p>
        <button type="button" class="btn btn--small" onClick={onRetry}>
          Noch mal versuchen
        </button>
      </div>
    </details>
  );
}

function RequestList({ onPick }: { onPick: (website: string) => void }) {
  const state = useAsync(listOpenAuditRequests, []);
  const details = useRef<HTMLDetailsElement>(null);
  if (state.loading) return null;
  if (state.error !== null) return <RequestsFailed message={state.error} onRetry={state.reload} />;
  const requests = sortByWish(state.data ?? []);
  if (requests.length === 0) return null;
  const pick = (website: string) => {
    if (details.current !== null) details.current.open = false;
    onPick(website);
  };
  return (
    <details class="requests" ref={details}>
      <summary class="requests__summary">{openRequestsLabel(requests.length)}</summary>
      <ul class="requests__body requests__list">
        {requests.map((request) => (
          <RequestRow key={request.id} request={request} onPick={pick} />
        ))}
      </ul>
    </details>
  );
}

export function OpenRequests({ onPick }: { onPick: (website: string) => void }) {
  return useIsAdmin() ? <RequestList onPick={onPick} /> : null;
}
