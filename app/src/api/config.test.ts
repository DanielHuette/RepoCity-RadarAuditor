import { afterEach, describe, expect, it, vi } from "vitest";
import { crmOrigin, missingConfig, supabaseConfig } from "./config";

function setEnv(url: string, anonKey: string, crm: string): void {
  vi.stubEnv("VITE_SUPABASE_URL", url);
  vi.stubEnv("VITE_SUPABASE_ANON_KEY", anonKey);
  vi.stubEnv("VITE_CRM_ORIGIN", crm);
}

describe("build configuration", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("is complete when every variable is set", () => {
    setEnv("https://project.supabase.co", "anon", "https://crm.example.test");
    expect(missingConfig()).toEqual([]);
    expect(supabaseConfig()).toEqual({ url: "https://project.supabase.co", anonKey: "anon" });
    expect(crmOrigin()).toBe("https://crm.example.test");
  });

  it("names every variable left empty", () => {
    setEnv("", "anon", "");
    expect(missingConfig()).toEqual(["VITE_SUPABASE_URL", "VITE_CRM_ORIGIN"]);
    expect(supabaseConfig()).toBeNull();
    expect(crmOrigin()).toBeNull();
  });

  it("drops a trailing slash of the CRM address", () => {
    setEnv("https://project.supabase.co", "anon", "https://crm.example.test/");
    expect(crmOrigin()).toBe("https://crm.example.test");
  });
});
