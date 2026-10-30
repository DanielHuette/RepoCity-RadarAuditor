import type { Session } from "@supabase/supabase-js";
import { useEffect, useState } from "preact/hooks";
import { supabase } from "../api/supabase";

/** `undefined` while the stored session is still being read, `null` when signed out. */
export function useSession(): Session | null | undefined {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  useEffect(() => {
    let active = true;
    void supabase.auth.getSession().then(({ data }) => {
      if (active) setSession(data.session);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next);
    });
    return () => {
      active = false;
      data.subscription.unsubscribe();
    };
  }, []);
  return session;
}
