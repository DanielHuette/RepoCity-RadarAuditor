export interface SupabaseConfig {
  url: string;
  anonKey: string;
}

const REQUIRED_VARIABLES = ["VITE_SUPABASE_URL", "VITE_SUPABASE_ANON_KEY", "VITE_CRM_ORIGIN"] as const;

type RequiredVariable = (typeof REQUIRED_VARIABLES)[number];

function readVariable(name: RequiredVariable): string | null {
  const value = import.meta.env[name]?.trim() ?? "";
  return value === "" ? null : value;
}

/** Names of the build variables that are not set, in a fixed order; empty when the build is complete. */
export function missingConfig(): RequiredVariable[] {
  return REQUIRED_VARIABLES.filter((name) => readVariable(name) === null);
}

export function supabaseConfig(): SupabaseConfig | null {
  const url = readVariable("VITE_SUPABASE_URL");
  const anonKey = readVariable("VITE_SUPABASE_ANON_KEY");
  return url === null || anonKey === null ? null : { url, anonKey };
}

export function crmOrigin(): string | null {
  return readVariable("VITE_CRM_ORIGIN")?.replace(/\/+$/, "") ?? null;
}
