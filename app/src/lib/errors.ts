export class QueryError extends Error {
  readonly code: string | null;
  readonly reason: string;

  constructor(action: string, message: string, code: string | null) {
    super(`${action}: ${message}`);
    this.name = "QueryError";
    this.code = code;
    this.reason = message;
  }
}

/** An error whose message already is the German sentence for the operator. */
export class NoticeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NoticeError";
  }
}

export const UNIQUE_VIOLATION = "23505";
export const NO_SINGLE_ROW = "PGRST116";
const FOREIGN_KEY_VIOLATION = "23503";
const RAISED_EXCEPTION = "P0001";
const NO_DATA_FOUND = "P0002";
const WRONG_STATE = "55000";
const EXPIRED_TOKEN = "PGRST301";
const ROW_SECURITY = "42501";
// Raised by the database and CRM functions with a German message meant for the operator.
const INVALID_PARAMETER = "22023";

const READ_ONLY = "Nur Lesen: Diese Aktion darf nur der Administrator ausführen.";

export function describeError(error: unknown): string {
  if (error instanceof NoticeError) return error.message;
  if (error instanceof QueryError) {
    switch (error.code) {
      case UNIQUE_VIOLATION:
        return "Diesen Eintrag gibt es schon.";
      case FOREIGN_KEY_VIOLATION:
        return "Der Eintrag hängt an Daten, die es nicht mehr gibt. Lade die Seite neu.";
      case ROW_SECURITY:
        return READ_ONLY;
      case RAISED_EXCEPTION:
      case INVALID_PARAMETER:
      case NO_DATA_FOUND:
      case WRONG_STATE:
        return error.reason;
      case EXPIRED_TOKEN:
        return "Deine Anmeldung ist abgelaufen. Melde dich neu an.";
      default:
        return `Das hat nicht geklappt. (${error.message})`;
    }
  }
  if (error instanceof TypeError) return "Keine Verbindung zum Server. Prüfe dein Netz und versuch es noch mal.";
  if (error instanceof Error) return `Das hat nicht geklappt. (${error.message})`;
  return "Das hat nicht geklappt.";
}
