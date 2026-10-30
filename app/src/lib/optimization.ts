// Optimisation of an order: drafts waiting for release, released ones waiting for the apply job, work
// the owner does by hand, and the log. The apply step stores a change without an automatic way as `manual`
// with an error starting "Manuell umsetzen:" (migration 010); before that it stored it as `failed`.
import type { Job, JobKind, OptimizationChange, OptimizationChangeStatus } from "../api/types";

const MANUAL_PREFIX = "Manuell umsetzen:";

export interface ChangeGroups {
  drafts: OptimizationChange[];
  approved: OptimizationChange[];
  /** To-dos by hand, open ones first, each part in creation order. */
  manual: OptimizationChange[];
  /** Applied and failed changes, newest first. */
  log: OptimizationChange[];
  rejected: OptimizationChange[];
}

export const CHANGE_STATUS_LABELS: Record<OptimizationChangeStatus, string> = {
  draft: "Entwurf",
  approved: "Freigegeben",
  rejected: "Abgelehnt",
  applied: "Angewendet",
  failed: "Fehlgeschlagen",
  manual: "Von Hand",
  manual_done: "Erledigt",
};

export const CHANGE_STATUS_TONES: Record<OptimizationChangeStatus, string> = {
  draft: "tone-warn",
  approved: "tone-busy",
  rejected: "",
  applied: "tone-ok",
  failed: "tone-bad",
  manual: "tone-warn",
  manual_done: "tone-ok",
};

function loggedAt(change: OptimizationChange): string {
  return change.applied_at ?? change.decided_at ?? change.created_at;
}

export function isManualChange(change: OptimizationChange): boolean {
  if (change.status === "manual" || change.status === "manual_done") return true;
  return change.status === "failed" && (change.error?.startsWith(MANUAL_PREFIX) ?? false);
}

export function isManualDone(change: OptimizationChange): boolean {
  return change.status === "manual_done";
}

/** What to do by hand, from the error text after "Manuell umsetzen:"; null when it says nothing more. */
export function manualInstruction(change: OptimizationChange): string | null {
  const error = change.error ?? "";
  if (!error.startsWith(MANUAL_PREFIX)) return null;
  const instruction = error.slice(MANUAL_PREFIX.length).trim();
  return instruction === "" ? null : instruction;
}

export function groupChanges(changes: readonly OptimizationChange[]): ChangeGroups {
  const byCreation = [...changes].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const pick = (status: OptimizationChangeStatus) => byCreation.filter((change) => change.status === status);
  const manual = byCreation.filter(isManualChange);
  return {
    drafts: pick("draft"),
    approved: pick("approved"),
    manual: [...manual.filter((change) => !isManualDone(change)), ...manual.filter(isManualDone)],
    log: byCreation
      .filter((change) => (change.status === "applied" || change.status === "failed") && !isManualChange(change))
      .sort((a, b) => loggedAt(b).localeCompare(loggedAt(a))),
    rejected: pick("rejected"),
  };
}

/** Newest job of a kind, or null. */
export function latestJob(jobs: readonly Job[], kind: JobKind): Job | null {
  return jobs.filter((job) => job.kind === kind).reduce<Job | null>((newest, job) => (newest === null || job.created_at > newest.created_at ? job : newest), null);
}

/** Whether the live view still waits for the server: a job runs or a released draft is not applied yet. */
export function isOptimizationBusy(jobs: readonly Job[], changes: readonly OptimizationChange[]): boolean {
  return jobs.some((job) => job.status === "queued" || job.status === "running") || changes.some((change) => change.status === "approved");
}
