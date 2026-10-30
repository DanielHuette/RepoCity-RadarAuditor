// Whether the signed-in person is the administrator. Everyone else reads only; the database
// enforces this, the app only hides what would be refused.
import { createContext } from "preact";
import { useContext, useEffect, useState } from "preact/hooks";
import { isAdmin } from "../api/queries";

/** `undefined` while unknown, then the answer of the database. */
export const AdminContext = createContext<boolean | undefined>(undefined);

export function useIsAdmin(): boolean {
  return useContext(AdminContext) === true;
}

export function useAdminStatus(userId: string | undefined): boolean | undefined {
  const [admin, setAdmin] = useState<boolean | undefined>(undefined);
  useEffect(() => {
    let active = true;
    setAdmin(undefined);
    if (userId === undefined) return;
    isAdmin().then(
      (value) => {
        if (active) setAdmin(value);
      },
      () => {
        if (active) setAdmin(false);
      },
    );
    return () => {
      active = false;
    };
  }, [userId]);
  return admin;
}
