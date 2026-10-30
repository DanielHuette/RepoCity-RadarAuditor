// Data that changes on the server while a page is open: reloads on Supabase Realtime changes of the
// given tables and polls while Realtime is not connected and the data still waits for the server.
import { REALTIME_SUBSCRIBE_STATES, type RealtimeChannel } from "@supabase/supabase-js";
import { supabase } from "../api/supabase";

const POLL_MS = 5000;

export interface LiveTable {
  schema?: string;
  table: string;
  /** Realtime filter such as `order_id=eq.<id>`. */
  filter?: string;
}

export interface LiveSource<T> {
  channel: string;
  tables: readonly LiveTable[];
  load: () => Promise<T>;
  /** Whether the server still has to act, so polling is worth it while Realtime is down. */
  waiting: (value: T) => boolean;
  onValue: (value: T) => void;
  onError: (reason: unknown) => void;
}

export interface LiveFollow {
  reload: () => void;
  stop: () => void;
}

interface FollowState {
  active: boolean;
  live: boolean;
  settled: boolean;
  latest: number;
}

function subscribe(name: string, tables: readonly LiveTable[], state: FollowState, reload: () => void): RealtimeChannel {
  let channel = supabase.channel(name);
  for (const { schema = "public", table, filter } of tables) {
    channel = channel.on("postgres_changes", { event: "*", schema, table, ...(filter === undefined ? {} : { filter }) }, reload);
  }
  return channel.subscribe((status) => {
    const connected = status === REALTIME_SUBSCRIBE_STATES.SUBSCRIBED;
    // Changes between the first load and the subscription would otherwise be missed.
    if (connected && !state.live) reload();
    state.live = connected;
  });
}

export function followLive<T>(source: LiveSource<T>): LiveFollow {
  const state: FollowState = { active: true, live: false, settled: false, latest: 0 };
  const run = async () => {
    state.latest += 1;
    const call = state.latest;
    try {
      const value = await source.load();
      if (!state.active || call !== state.latest) return;
      state.settled = !source.waiting(value);
      source.onValue(value);
    } catch (reason) {
      if (state.active && call === state.latest) source.onError(reason);
    }
  };
  const reload = () => void run();
  void run();
  const channel = subscribe(source.channel, source.tables, state, reload);
  const timer = window.setInterval(() => {
    if (!state.live && !state.settled) reload();
  }, POLL_MS);
  const stop = () => {
    state.active = false;
    window.clearInterval(timer);
    void supabase.removeChannel(channel);
  };
  return { reload, stop };
}
