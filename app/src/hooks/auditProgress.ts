// Live progress of one audit: Supabase Realtime on its audit and job rows, polling while Realtime is not connected.
import { useEffect, useState } from "preact/hooks";
import { getAuditProgress } from "../api/queries";
import type { AuditWithCustomer } from "../api/types";
import { describeError } from "../lib/errors";
import { auditProgress, isFinished, type Progress } from "../lib/progress";
import { followLive } from "./followLive";

export interface AuditProgressState {
  /** `undefined` while loading, `null` when the audit does not exist. */
  audit: AuditWithCustomer | null | undefined;
  progress: Progress | null;
  error: string | null;
}

const LOADING: AuditProgressState = { audit: undefined, progress: null, error: null };

async function loadProgress(auditId: string): Promise<{ audit: AuditWithCustomer | null; progress: Progress | null }> {
  const { audit, jobs } = await getAuditProgress(auditId);
  return { audit, progress: audit === null ? null : auditProgress(audit, jobs) };
}

function settled(progress: Progress | null): boolean {
  return progress === null || (isFinished(progress.step) && !progress.serpRetry);
}

export function useAuditProgress(auditId: string | null): AuditProgressState {
  const [state, setState] = useState<AuditProgressState>(LOADING);

  useEffect(() => {
    setState(LOADING);
    if (auditId === null) return;
    const follow = followLive({
      channel: `pruefung-${auditId}`,
      tables: [
        { table: "audits", filter: `id=eq.${auditId}` },
        { table: "jobs", filter: `audit_id=eq.${auditId}` },
      ],
      load: () => loadProgress(auditId),
      waiting: ({ progress }) => !settled(progress),
      onValue: (loaded) => {
        setState({ ...loaded, error: null });
      },
      onError: (reason) => {
        setState((previous) => ({ ...previous, error: describeError(reason) }));
      },
    });
    return follow.stop;
  }, [auditId]);

  return state;
}
