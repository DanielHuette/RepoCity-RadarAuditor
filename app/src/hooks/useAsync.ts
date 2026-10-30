import { useCallback, useEffect, useRef, useState } from "preact/hooks";
import { describeError } from "../lib/errors";

export interface AsyncState<T> {
  data: T | undefined;
  error: string | null;
  loading: boolean;
  reload: () => void;
  setData: (next: T) => void;
}

function useGeneration(): [number, () => void] {
  const [generation, setGeneration] = useState(0);
  const raise = useCallback(() => {
    setGeneration((value) => value + 1);
  }, []);
  return [generation, raise];
}

export function useAsync<T>(load: () => Promise<T>, deps: readonly unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [generation, reload] = useGeneration();
  const latest = useRef(0);

  useEffect(() => {
    const call = latest.current + 1;
    latest.current = call;
    setLoading(true);
    setError(null);
    load().then(
      (value) => {
        if (latest.current !== call) return;
        setData(value);
        setLoading(false);
      },
      (reason: unknown) => {
        if (latest.current !== call) return;
        setError(describeError(reason));
        setLoading(false);
      },
    );
  }, [...deps, generation]);

  return { data, error, loading, reload, setData };
}

export interface Action {
  busy: boolean;
  error: string | null;
  run: (task: () => Promise<void>) => Promise<boolean>;
  clear: () => void;
}

export function useAction(): Action {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async (task: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await task();
      return true;
    } catch (reason) {
      setError(describeError(reason));
      return false;
    } finally {
      setBusy(false);
    }
  }, []);
  const clear = useCallback(() => {
    setError(null);
  }, []);
  return { busy, error, run, clear };
}
