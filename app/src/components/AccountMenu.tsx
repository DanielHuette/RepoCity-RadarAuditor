import type { RefObject } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { supabase } from "../api/supabase";
import { useAction, type Action } from "../hooks/useAsync";
import { Icon } from "./Icon";
import { ErrorAlert } from "./ui";

function useDismiss(root: RefObject<HTMLElement>, open: boolean, onDismiss: () => void): void {
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (root.current !== null && !root.current.contains(event.target as Node)) onDismiss();
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onDismiss();
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);
}

function AccountPanel({ email, action }: { email: string; action: Action }) {
  return (
    <div id="account-menu" class="account__menu panel" role="group" aria-label="Konto">
      <p class="small muted">Angemeldet als</p>
      <p class="account__email">{email}</p>
      <button
        type="button"
        class="btn btn--block"
        disabled={action.busy}
        onClick={() =>
          void action.run(async () => {
            const { error } = await supabase.auth.signOut();
            if (error !== null) throw error;
          })
        }
      >
        Abmelden
      </button>
      <ErrorAlert message={action.error} />
    </div>
  );
}

export function AccountMenu({ email }: { email: string }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const action = useAction();
  useDismiss(root, open, () => {
    setOpen(false);
  });

  return (
    <div class="account" ref={root}>
      <button
        type="button"
        class="iconbtn"
        aria-label="Konto"
        aria-expanded={open}
        aria-controls="account-menu"
        onClick={() => {
          setOpen((value) => !value);
        }}
      >
        <Icon name="account" />
      </button>
      {open && <AccountPanel email={email} action={action} />}
    </div>
  );
}
