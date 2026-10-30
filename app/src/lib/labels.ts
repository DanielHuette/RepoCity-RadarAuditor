import type { AuditKind, AuditStatus, CheckOutcome, Finding, JobStatus } from "../api/types";

export const AUDIT_STATUS_LABELS: Record<AuditStatus, string> = {
  requested: "Angefordert",
  collecting: "Sammelt Daten",
  collected: "Daten gesammelt",
  scored: "Bewertet",
  awaiting_analysis: "Wartet auf Auswertung",
  analysed: "Ausgewertet",
  reported: "Bericht fertig",
  failed: "Fehlgeschlagen",
};

export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  queued: "Wartet",
  running: "Läuft",
  done: "Erledigt",
  failed: "Fehlgeschlagen",
};

export const AUDIT_KIND_LABELS: Record<AuditKind, string> = {
  remote: "Fernaudit",
  with_access: "Mit Zugang",
};

export const OUTCOME_LABELS: Record<CheckOutcome, string> = {
  passed: "bestanden",
  partial: "teilweise",
  failed: "nicht bestanden",
  not_measurable: "nicht messbar",
  skipped: "übersprungen",
};

export const OUTCOME_TONES: Record<CheckOutcome, string> = {
  passed: "tone-ok",
  partial: "tone-warn",
  failed: "tone-bad",
  not_measurable: "",
  skipped: "",
};

export const URGENCY: Record<Finding["severity"], { label: string; tone: string }> = {
  3: { label: "Dringend", tone: "tone-bad" },
  2: { label: "Wichtig", tone: "tone-warn" },
  1: { label: "Verbesserung", tone: "" },
};
