// Reply drafts to unanswered reviews as a PDF for the customer: the app requests a documents row of
// kind review_replies with data {audit_id}; the worker lets Claude Code write the drafts and sets
// file_path, or writes data.error (documents/review_replies.py). Nothing is posted on Google.
import type { Doc } from "../api/types";

export function replyRequestData(auditId: string): Record<string, string> {
  return { audit_id: auditId };
}

/** Newest reply PDF request of an audit. */
export function latestReplyDoc(docs: readonly Doc[], auditId: string): Doc | null {
  return (
    docs
      .filter((doc) => doc.kind === "review_replies" && doc.data.audit_id === auditId)
      .sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null
  );
}

/** How many drafts a finished PDF holds; 0 while it is not finished. */
export function replyCount(doc: Doc): number {
  const replies = doc.data.replies;
  return doc.file_path !== null && Array.isArray(replies) ? replies.length : 0;
}

/** Only an analysed audit has reviews and findings to show. */
export function repliesPossible(status: string): boolean {
  return status === "analysed" || status === "reported";
}
