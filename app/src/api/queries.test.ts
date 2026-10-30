import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryError } from "../lib/errors";
import { IDENTIFICATION_TIMEOUT_MS, identificationSource, listOpenAuditRequests } from "./queries";

type SignedUrlResult = { data: { signedUrl: string }; error: null } | { data: null; error: { status?: number; statusCode?: string; message: string } };

const createSignedUrl = vi.fn<(path: string, seconds: number) => Promise<SignedUrlResult>>();
const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>();
const crmRpc = vi.fn<(schema: string, name: string) => Promise<{ data: unknown; error: { message: string; code: string } | null }>>();

vi.mock("./supabase", () => ({
  supabase: {
    storage: { from: () => ({ createSignedUrl: (path: string, seconds: number) => createSignedUrl(path, seconds) }) },
    schema: (schema: string) => ({ rpc: (name: string) => crmRpc(schema, name) }),
  },
}));

beforeEach(() => {
  createSignedUrl.mockReset();
  fetchMock.mockReset();
  createSignedUrl.mockResolvedValue({ data: { signedUrl: "https://storage.test/raw.json" }, error: null });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("identificationSource", () => {
  it("reads the source from raw.json within the time limit", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ identified: { source: "impressum" } })));
    await expect(identificationSource("a1/raw.json")).resolves.toBe("Impressum");
    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.signal).toBeInstanceOf(AbortSignal);
    expect(IDENTIFICATION_TIMEOUT_MS).toBe(15_000);
  });

  it("has no source when raw.json does not exist", async () => {
    createSignedUrl.mockResolvedValue({ data: null, error: { statusCode: "404", message: "Object not found" } });
    await expect(identificationSource("a1/raw.json")).resolves.toBeNull();
    fetchMock.mockResolvedValue(new Response("", { status: 404 }));
    createSignedUrl.mockResolvedValue({ data: { signedUrl: "https://storage.test/raw.json" }, error: null });
    await expect(identificationSource("a1/raw.json")).resolves.toBeNull();
  });

  it("reports a refused signed address instead of hiding it", async () => {
    createSignedUrl.mockResolvedValue({ data: null, error: { status: 403, statusCode: "403", message: "Access denied" } });
    await expect(identificationSource("a1/raw.json")).rejects.toThrow(QueryError);
  });

  it("reports a server error, broken JSON and a timeout", async () => {
    fetchMock.mockResolvedValueOnce(new Response("", { status: 500 }));
    await expect(identificationSource("a1/raw.json")).rejects.toThrow("HTTP 500");
    fetchMock.mockResolvedValueOnce(new Response("{not json"));
    await expect(identificationSource("a1/raw.json")).rejects.toThrow(QueryError);
    fetchMock.mockRejectedValueOnce(new DOMException("signal timed out", "TimeoutError"));
    await expect(identificationSource("a1/raw.json")).rejects.toThrow("keine Antwort nach 15 Sekunden");
  });

  it("passes a lost connection on", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(identificationSource("a1/raw.json")).rejects.toThrow(TypeError);
  });
});

describe("listOpenAuditRequests", () => {
  it("reads crm.open_audit_requests", async () => {
    crmRpc.mockResolvedValue({ data: [{ id: "r1" }], error: null });
    await expect(listOpenAuditRequests()).resolves.toEqual([{ id: "r1" }]);
    expect(crmRpc).toHaveBeenCalledWith("crm", "open_audit_requests");
  });

  it("reports a refusal of the database", async () => {
    crmRpc.mockResolvedValue({ data: null, error: { message: "nur Administrator", code: "42501" } });
    await expect(listOpenAuditRequests()).rejects.toThrow("Offene Anfragen laden: nur Administrator");
  });
});
