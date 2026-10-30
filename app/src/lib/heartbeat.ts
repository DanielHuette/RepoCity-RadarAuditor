// The worker writes settings key `worker_heartbeat` = {at, version, capabilities} on every poll;
// the app treats the server as online while that time is recent. While capabilities.analysis_available
// is false the worker claims no new audits (src/radarauditor/db/app_settings.py), so a fresh heartbeat
// saying so blocks starting one; a stale heartbeat says nothing about now and queueing stays allowed.
// capabilities.mail_reading_available false means the server does not read the mails the CRM queued; the
// reason is shown next to the server status.

export const HEARTBEAT_KEY = "worker_heartbeat";
export const HEARTBEAT_MAX_AGE_MS = 2 * 60 * 1000;
export const ANALYSIS_UNAVAILABLE = "Die Auswertung auf dem Server ist nicht bereit.";
export const MAIL_READING_UNAVAILABLE = "Der Server liest gerade keine Mails.";

export interface Heartbeat {
  at: Date;
  version: string | null;
  capabilities: Record<string, unknown>;
}

export interface ServerStatus {
  online: boolean;
  lastSeen: Date | null;
  /** Why no audit can be started right now, or null when starting is allowed. */
  blockedReason: string | null;
  /** Why the server reads no mails right now, or null when it does or does not say. */
  mailProblem: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseHeartbeat(value: unknown): Heartbeat | null {
  if (!isRecord(value) || typeof value.at !== "string") return null;
  const at = new Date(value.at);
  if (Number.isNaN(at.getTime())) return null;
  return {
    at,
    version: typeof value.version === "string" ? value.version : null,
    capabilities: isRecord(value.capabilities) ? value.capabilities : {},
  };
}

/** The reason when the capability is reported false, a general text when the reason is missing, else null. */
function unavailable(capabilities: Record<string, unknown>, name: string, fallback: string): string | null {
  if (capabilities[`${name}_available`] !== false) return null;
  const reason = capabilities[`${name}_reason`];
  return typeof reason === "string" && reason.trim() !== "" ? reason.trim() : fallback;
}

export function serverStatus(heartbeat: Heartbeat | null, now: Date): ServerStatus {
  if (heartbeat === null) return { online: false, lastSeen: null, blockedReason: null, mailProblem: null };
  const online = now.getTime() - heartbeat.at.getTime() < HEARTBEAT_MAX_AGE_MS;
  const { capabilities } = heartbeat;
  return {
    online,
    lastSeen: heartbeat.at,
    blockedReason: online ? unavailable(capabilities, "analysis", ANALYSIS_UNAVAILABLE) : null,
    mailProblem: online ? unavailable(capabilities, "mail_reading", MAIL_READING_UNAVAILABLE) : null,
  };
}
