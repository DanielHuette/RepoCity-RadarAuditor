import { describe, expect, it } from "vitest";
import type { Doc } from "../api/types";
import { latestReplyDoc, replyCount, replyRequestData, repliesPossible } from "./reviewReplies";

function doc(changes: Partial<Doc>): Doc {
  return {
    id: "d1",
    customer_id: "c1",
    kind: "review_replies",
    number: null,
    issued_on: null,
    net_amount: null,
    vat_rate: null,
    gross_amount: null,
    data: { audit_id: "a1" },
    file_path: null,
    created_at: "2026-10-30T08:00:00Z",
    ...changes,
  };
}

describe("review reply PDFs", () => {
  it("asks with the audit only", () => {
    expect(replyRequestData("a1")).toEqual({ audit_id: "a1" });
  });

  it("finds the newest request of the audit and ignores quote PDFs", () => {
    const older = doc({ id: "old" });
    const newer = doc({ id: "new", created_at: "2026-10-31T08:00:00Z" });
    const otherAudit = doc({ id: "other", data: { audit_id: "a2" }, created_at: "2026-11-01T08:00:00Z" });
    const quote = doc({ id: "quote", kind: "audit_quote", created_at: "2026-11-02T08:00:00Z" });

    expect(latestReplyDoc([older, newer, otherAudit, quote], "a1")?.id).toBe("new");
    expect(latestReplyDoc([quote], "a1")).toBeNull();
  });

  it("counts the drafts of a finished PDF", () => {
    expect(replyCount(doc({ file_path: "c1/d1/x.pdf", data: { audit_id: "a1", replies: [{}, {}, {}] } }))).toBe(3);
    expect(replyCount(doc({}))).toBe(0);
  });

  it("is possible once the audit is analysed", () => {
    expect(repliesPossible("reported")).toBe(true);
    expect(repliesPossible("analysed")).toBe(true);
    expect(repliesPossible("awaiting_analysis")).toBe(false);
  });
});
