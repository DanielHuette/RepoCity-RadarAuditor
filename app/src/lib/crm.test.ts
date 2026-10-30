import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { crmCustomerUrl } from "./crm";

describe("crmCustomerUrl", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_CRM_ORIGIN", "https://crm.example.test");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("points to the customer file of the configured CRM", () => {
    expect(crmCustomerUrl("6f1c2a9e-3b7d-4c55-9a10-2f8e4d6b7c01")).toBe(
      "https://crm.example.test/kunde/6f1c2a9e-3b7d-4c55-9a10-2f8e4d6b7c01",
    );
  });

  it("has no address for a customer without CRM link", () => {
    expect(crmCustomerUrl(null)).toBeNull();
    expect(crmCustomerUrl(undefined)).toBeNull();
    expect(crmCustomerUrl(" ")).toBeNull();
  });

  it("has no address when the CRM is not configured", () => {
    vi.stubEnv("VITE_CRM_ORIGIN", "");
    expect(crmCustomerUrl("6f1c2a9e")).toBeNull();
  });
});
