/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SUPABASE_URL: string | undefined;
  readonly VITE_SUPABASE_ANON_KEY: string | undefined;
  readonly VITE_CRM_ORIGIN: string | undefined;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
