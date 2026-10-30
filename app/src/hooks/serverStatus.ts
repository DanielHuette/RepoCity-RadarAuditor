// Whether the worker server is up, from its heartbeat in table settings.
import { useEffect, useState, type Dispatch, type StateUpdater } from "preact/hooks";
import { getHeartbeat } from "../api/queries";
import { serverStatus, type Heartbeat, type ServerStatus } from "../lib/heartbeat";

const FETCH_MS = 30_000;
const CLOCK_MS = 15_000;

function pollHeartbeat(setHeartbeat: Dispatch<StateUpdater<Heartbeat | null | undefined>>): () => void {
  let active = true;
  const load = () => {
    getHeartbeat().then(
      (value) => {
        if (active) setHeartbeat(value);
      },
      () => {
        // An unreadable heartbeat says nothing about the server; keep the last answer.
        if (active) setHeartbeat((previous) => previous ?? null);
      },
    );
  };
  load();
  const timer = window.setInterval(load, FETCH_MS);
  return () => {
    active = false;
    window.clearInterval(timer);
  };
}

/** `undefined` until the first heartbeat read has answered. */
export function useServerStatus(): ServerStatus | undefined {
  const [heartbeat, setHeartbeat] = useState<Heartbeat | null | undefined>(undefined);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const stopPolling = pollHeartbeat(setHeartbeat);
    const clockTimer = window.setInterval(() => {
      setNow(new Date());
    }, CLOCK_MS);
    return () => {
      stopPolling();
      window.clearInterval(clockTimer);
    };
  }, []);

  return heartbeat === undefined ? undefined : serverStatus(heartbeat, now);
}
