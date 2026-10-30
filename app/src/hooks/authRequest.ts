import { useState } from "preact/hooks";

interface AuthAnswer {
  error: { message: string } | null;
}

export interface AuthRequest {
  busy: boolean;
  error: string | null;
  setError: (message: string | null) => void;
  send: (request: () => Promise<AuthAnswer>) => Promise<boolean>;
}

export function useAuthRequest(describe: (message: string) => string): AuthRequest {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const send = async (request: () => Promise<AuthAnswer>) => {
    setBusy(true);
    setError(null);
    const { error: failure } = await request();
    setBusy(false);
    if (failure !== null) setError(describe(failure.message));
    return failure === null;
  };
  return { busy, error, setError, send };
}
