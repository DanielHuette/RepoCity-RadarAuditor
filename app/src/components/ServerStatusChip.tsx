import { useServerStatus } from "../hooks/serverStatus";
import { formatDateTime } from "../lib/format";
import type { ServerStatus } from "../lib/heartbeat";

const OFFLINE_NOTE = "Prüfung startet, sobald der Server läuft.";

function OfflineStatus({ lastSeen, offlineNote }: { lastSeen: Date | null; offlineNote: string }) {
  return (
    <p class="server server--offline" role="status">
      <span class="server__dot" aria-hidden="true" />
      <span>
        <strong>Server offline</strong> – {offlineNote}
        <br />
        <span class="muted small">
          {lastSeen === null ? "Noch kein Lebenszeichen vom Server." : `Letztes Lebenszeichen: ${formatDateTime(lastSeen.toISOString())}`}
        </span>
      </span>
    </p>
  );
}

/** `status` is `undefined` while the first read is pending. */
function ServerStatusView({ status, offlineNote }: { status: ServerStatus | undefined; offlineNote: string }) {
  if (status === undefined) {
    return (
      <p class="server" role="status">
        <span class="server__dot" aria-hidden="true" />
        <span class="muted">Serverstatus wird geprüft …</span>
      </p>
    );
  }
  if (!status.online) return <OfflineStatus lastSeen={status.lastSeen} offlineNote={offlineNote} />;
  return (
    <p class="server server--online" role="status">
      <span class="server__dot" aria-hidden="true" />
      <span>
        Server online
        {status.mailProblem !== null && (
          <>
            <br />
            <span class="muted small">Mails lesen steht: {status.mailProblem}</span>
          </>
        )}
      </span>
    </p>
  );
}

export function ServerStatusChip({ offlineNote = OFFLINE_NOTE }: { offlineNote?: string }) {
  return <ServerStatusView status={useServerStatus()} offlineNote={offlineNote} />;
}
