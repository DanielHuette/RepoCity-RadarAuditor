// Single Supabase client for the app; URL and anon key come from Vite env at build time.
import { createClient } from "@supabase/supabase-js";
import { supabaseConfig } from "./config";

const config = supabaseConfig();
if (config === null) throw new Error("VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY must be set at build time");

// A password reset link marks the address; it is read before the client consumes the address.
export const recoveryRequested = window.location.hash.includes("type=recovery");

export const supabase = createClient(config.url, config.anonKey);
