// Reply drafts to unanswered reviews as a PDF (documents row of kind review_replies, migration 016).
import { QueryError } from "../lib/errors";
import { replyRequestData } from "../lib/reviewReplies";
import { supabase } from "./supabase";
import type { Doc } from "./types";

const SIGNED_URL_SECONDS = 60 * 60;

export async function listReplyDocs(customerId: string): Promise<Doc[]> {
  const result = await supabase
    .from("documents")
    .select("*")
    .eq("customer_id", customerId)
    .eq("kind", "review_replies")
    .order("created_at", { ascending: false });
  if (result.error !== null) throw new QueryError("Antwortentwürfe laden", result.error.message, result.error.code);
  return result.data as Doc[];
}

export async function requestReviewReplies(customerId: string, auditId: string): Promise<void> {
  const result = await supabase
    .from("documents")
    .insert({ kind: "review_replies", customer_id: customerId, data: replyRequestData(auditId), file_path: null, number: null });
  if (result.error !== null) throw new QueryError("Antwortentwürfe anfordern", result.error.message, result.error.code);
}

export async function documentUrl(path: string): Promise<string> {
  const { data, error } = await supabase.storage.from("documents").createSignedUrl(path, SIGNED_URL_SECONDS);
  if (error !== null) throw new QueryError("PDF öffnen", error.message, null);
  return data.signedUrl;
}
