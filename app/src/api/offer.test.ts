import { beforeEach, describe, expect, it, vi } from "vitest";
import { describeError } from "../lib/errors";
import { saveCrmCustomerEmail } from "./offer";

interface Answer {
  data: unknown;
  error: { message: string; code: string } | null;
}

const calls: { method: string; args: unknown[] }[] = [];
let answer: Answer = { data: { id: "crm-7" }, error: null };

function builder(): Record<string, unknown> {
  const chain: Record<string, unknown> = {};
  for (const method of ["from", "update", "eq", "select"]) {
    chain[method] = (...args: unknown[]) => {
      calls.push({ method, args });
      return chain;
    };
  }
  chain.single = () => Promise.resolve(answer);
  return chain;
}

vi.mock("./supabase", () => ({ supabase: { schema: () => builder() } }));

beforeEach(() => {
  calls.length = 0;
  answer = { data: { id: "crm-7" }, error: null };
});

describe("saveCrmCustomerEmail", () => {
  it("stores the address in lower case and confirms one updated row", async () => {
    await saveCrmCustomerEmail("crm-7", "Erika@Muster.DE");
    expect(calls).toEqual([
      { method: "from", args: ["customers"] },
      { method: "update", args: [{ email: "erika@muster.de" }] },
      { method: "eq", args: ["id", "crm-7"] },
      { method: "select", args: ["id"] },
    ]);
  });

  it("names an address that belongs to another customer", async () => {
    answer = { data: null, error: { message: "duplicate key value violates unique constraint", code: "23505" } };
    const failure = await saveCrmCustomerEmail("crm-7", "erika@muster.de").catch((reason: unknown) => reason);
    expect(describeError(failure)).toBe("Diese E-Mail-Adresse gehört schon zu einem anderen Kunden im CRM.");
  });

  it("says when the CRM customer no longer exists", async () => {
    answer = { data: null, error: { message: "JSON object requested, multiple (or no) rows returned", code: "PGRST116" } };
    const failure = await saveCrmCustomerEmail("crm-7", "erika@muster.de").catch((reason: unknown) => reason);
    expect(describeError(failure)).toBe("Diesen Kunden gibt es im CRM nicht mehr.");
  });
});
