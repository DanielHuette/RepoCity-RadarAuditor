// Tracks whether the person arrived through a password reset link, so the app asks for a new
// password before anything else. The link's marker is read before the Supabase client consumes it.
import { useEffect, useState } from "preact/hooks";
import { recoveryRequested, supabase } from "../api/supabase";

let pending = recoveryRequested;
const listeners = new Set<() => void>();

supabase.auth.onAuthStateChange((event) => {
  if (event === "PASSWORD_RECOVERY") {
    pending = true;
    for (const listener of listeners) listener();
  }
});

export function usePasswordRecovery(): { pending: boolean; finish: () => void } {
  const [state, setState] = useState(pending);
  useEffect(() => {
    const listener = () => {
      setState(pending);
    };
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);
  const finish = () => {
    pending = false;
    for (const listener of listeners) listener();
  };
  return { pending: state, finish };
}
