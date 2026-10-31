// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Doc } from "../api/types";
import { ReviewReplies } from "./ReviewReplies";

const requestReviewReplies = vi.fn<(customerId: string, auditId: string) => Promise<void>>();
const documentUrl = vi.fn<(path: string) => Promise<string>>();
let current: Doc | null = null;

vi.mock("../api/supabase", () => ({ supabase: {} }));
vi.mock("../api/queries", () => ({ getHeartbeat: () => Promise.resolve(null) }));
vi.mock("../api/replies", () => ({
  requestReviewReplies: (customerId: string, auditId: string) => requestReviewReplies(customerId, auditId),
  documentUrl: (path: string) => documentUrl(path),
  listReplyDocs: () => Promise.resolve([]),
}));
vi.mock("../hooks/useLive", () => ({
  useLive: () => ({ data: current, error: null, reload: () => undefined }),
}));

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

let root: HTMLDivElement;

async function mount(status = "reported") {
  await act(() => {
    render(<ReviewReplies auditId="a1" auditStatus={status} customerId="c1" />, root);
  });
}

function buttons(): string[] {
  return [...root.querySelectorAll("button")].map((button) => button.textContent);
}

beforeEach(() => {
  requestReviewReplies.mockReset();
  documentUrl.mockReset();
  current = null;
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  root.remove();
});

describe("ReviewReplies", () => {
  it("asks the server for the drafts of this audit", async () => {
    requestReviewReplies.mockResolvedValue();
    await mount();
    expect(root.textContent).toContain("Bei Google wird nichts veröffentlicht.");
    await act(() => {
      root.querySelector("button")?.click();
    });
    expect(requestReviewReplies).toHaveBeenCalledWith("c1", "a1");
  });

  it("waits while the server writes", async () => {
    current = doc({});
    await mount();
    expect(root.textContent).toContain("Die Antwortentwürfe werden geschrieben …");
    expect(root.textContent).not.toContain("Claude");
    expect(buttons()).toEqual([]);
  });

  it("names why it failed and offers a new request", async () => {
    current = doc({ data: { audit_id: "a1", error: "Jede gelesene Bewertung ist schon beantwortet." } });
    await mount();
    expect(root.textContent).toContain("Jede gelesene Bewertung ist schon beantwortet.");
    expect(buttons()).toEqual(["Neu anfordern"]);
  });

  it("opens the finished PDF", async () => {
    current = doc({ file_path: "c1/d1/x.pdf", data: { audit_id: "a1", replies: [{}, {}, {}, {}, {}] } });
    await mount();
    expect(buttons()).toEqual(["5 Antwortentwürfe öffnen (PDF)", "Neu anfordern"]);
  });

  it("stays away before the audit is analysed", async () => {
    await mount("collecting");
    expect(root.innerHTML).toBe("");
  });
});
