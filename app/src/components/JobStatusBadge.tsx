import type { AuditStatus, JobStatus } from "../api/types";
import { AUDIT_STATUS_LABELS, JOB_STATUS_LABELS } from "../lib/labels";

type Status = AuditStatus | JobStatus;

const TONES: Record<Status, string> = {
  requested: "",
  collecting: "tone-busy",
  collected: "tone-busy",
  scored: "tone-busy",
  awaiting_analysis: "tone-warn",
  analysed: "tone-busy",
  reported: "tone-ok",
  failed: "tone-bad",
  queued: "",
  running: "tone-busy",
  done: "tone-ok",
};

function label(status: Status): string {
  return status in AUDIT_STATUS_LABELS
    ? AUDIT_STATUS_LABELS[status as AuditStatus]
    : JOB_STATUS_LABELS[status as JobStatus];
}

export function JobStatusBadge({ status }: { status: Status }) {
  return <span class={`badge ${TONES[status]}`}>{label(status)}</span>;
}
