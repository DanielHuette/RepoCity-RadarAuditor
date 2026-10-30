// texte.json is written by Claude Code (`/auswerten`) and uploaded by `jobs push` to bucket raw
// at `<audit_id>/texte.json`; the schema is ReportTexts in src/radarauditor/reporting/texts.py.
// The app shows the conversation text and the two lists meant for the operator; the report prose,
// summary included, stays in the PDF.

export interface ReviewReply {
  reviewExcerpt: string;
  reply: string;
}

export interface AuditTexts {
  /** What the operator says to the customer; null for audits analysed before talk_text existed. */
  talk: string | null;
  posts: string[];
  replies: ReviewReply[];
}

export class TextsFormatError extends Error {
  constructor(location: string) {
    super(`texte.json ist ungültig bei ${location}`);
    this.name = "TextsFormatError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredText(value: unknown, location: string): string {
  if (typeof value !== "string" || value.trim() === "") throw new TextsFormatError(location);
  return value;
}

function optionalList(root: Record<string, unknown>, field: string): unknown[] {
  const value = root[field];
  if (value === undefined) return [];
  if (!Array.isArray(value)) throw new TextsFormatError(field);
  return value as unknown[];
}

function optionalText(value: unknown, location: string): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string") throw new TextsFormatError(location);
  const text = value.trim();
  return text === "" ? null : text;
}

function parsePost(value: unknown, location: string): string {
  if (typeof value !== "string") throw new TextsFormatError(location);
  return value.trim();
}

function parseReply(value: unknown, location: string): ReviewReply {
  if (!isRecord(value)) throw new TextsFormatError(location);
  return {
    reviewExcerpt: requiredText(value.review_excerpt, `${location}.review_excerpt`),
    reply: requiredText(value.reply, `${location}.reply`),
  };
}

/** Read talk text, post suggestions and review replies from texte.json; blank posts are left out. */
export function parseAuditTexts(content: string): AuditTexts {
  let root: unknown;
  try {
    root = JSON.parse(content);
  } catch {
    throw new TextsFormatError("<root>");
  }
  if (!isRecord(root)) throw new TextsFormatError("<root>");
  return {
    talk: optionalText(root.talk_text, "talk_text"),
    posts: optionalList(root, "post_suggestions")
      .map((post, index) => parsePost(post, `post_suggestions.${String(index)}`))
      .filter((post) => post !== ""),
    replies: optionalList(root, "review_replies").map((reply, index) => parseReply(reply, `review_replies.${String(index)}`)),
  };
}

/** Whether a storage error means the object does not exist, as opposed to a failed request. */
export function isMissingObject(error: { status?: number; statusCode?: string; code?: string }): boolean {
  return error.status === 404 || error.statusCode === "404" || error.code === "NoSuchKey" || error.code === "not_found";
}
