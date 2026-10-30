// One row of table `settings`, changed in place: the page shows a change at once and the writes go
// out one after another, so quick taps cannot overtake each other. A failed write reloads the stored value.
import { useCallback, useRef, useState } from "preact/hooks";
import { getSetting, saveSetting } from "../api/queries";
import { describeError } from "../lib/errors";
import { useAsync, type AsyncState } from "./useAsync";

export type SaveStatus = "idle" | "saving" | "saved" | "failed";

export interface SettingState<T> {
  state: AsyncState<T>;
  save: (next: T) => void;
  status: SaveStatus;
  error: string | null;
}

interface SaveQueue {
  tail: Promise<void>;
  pending: number;
}

/** Runs `write` after the writes before it; `saved` follows only the last write still pending. */
function enqueue(queue: SaveQueue, write: () => Promise<void>, saved: () => void, failed: (reason: unknown) => void): void {
  queue.pending += 1;
  queue.tail = queue.tail.then(write).then(
    () => {
      queue.pending -= 1;
      if (queue.pending === 0) saved();
    },
    (reason: unknown) => {
      queue.pending -= 1;
      failed(reason);
    },
  );
}

export function useSetting<T>(key: string, parse: (value: unknown) => T): SettingState<T> {
  const state = useAsync(async () => parse(await getSetting(key)), [key]);
  const queue = useRef<SaveQueue>({ tail: Promise.resolve(), pending: 0 });
  const [status, setStatus] = useState<SaveStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const { setData, reload } = state;

  const save = useCallback(
    (next: T) => {
      setData(next);
      setStatus("saving");
      setError(null);
      enqueue(
        queue.current,
        () => saveSetting(key, next),
        () => {
          setStatus("saved");
        },
        (reason) => {
          setStatus("failed");
          setError(describeError(reason));
          reload();
        },
      );
    },
    [key, setData, reload],
  );

  return { state, save, status, error };
}
