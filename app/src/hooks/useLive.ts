// Data of a page that changes on the server while the page is open; see followLive.
import { useCallback, useEffect, useRef, useState, type MutableRef } from "preact/hooks";
import { describeError } from "../lib/errors";
import { followLive, type LiveTable } from "./followLive";

export interface LiveState<T> {
  data: T | undefined;
  error: string | null;
  reload: () => void;
}

function useLatest<T>(value: T): MutableRef<T> {
  const ref = useRef(value);
  ref.current = value;
  return ref;
}

function useReloadSlot(): [MutableRef<() => void>, () => void] {
  const slot = useRef<() => void>(() => undefined);
  const reload = useCallback(() => {
    slot.current();
  }, []);
  return [slot, reload];
}

function useLiveValue<T>() {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const reset = () => {
    setData(undefined);
    setError(null);
  };
  const take = (value: T) => {
    setData(value);
    setError(null);
  };
  const fail = (reason: unknown) => {
    setError(describeError(reason));
  };
  return { data, error, reset, take, fail };
}

/**
 * @param key Identifies the subscription; a new key starts over.
 * @param waiting Whether the server still has to act, so polling is worth it while Realtime is down.
 */
export function useLive<T>(
  key: string | null,
  load: () => Promise<T>,
  tables: readonly LiveTable[],
  waiting: (data: T) => boolean,
): LiveState<T> {
  const value = useLiveValue<T>();
  const latest = useLatest({ load, waiting });
  const [reloadSlot, reload] = useReloadSlot();

  useEffect(() => {
    value.reset();
    if (key === null) return;
    const follow = followLive({
      channel: `live-${key}`,
      tables,
      load: () => latest.current.load(),
      waiting: (data) => latest.current.waiting(data),
      onValue: value.take,
      onError: value.fail,
    });
    reloadSlot.current = follow.reload;
    return follow.stop;
    // The tables follow from the key; listing them would resubscribe on every render.
  }, [key]);

  return { data: value.data, error: value.error, reload };
}
