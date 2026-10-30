// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { followLive, type LiveSource } from "./followLive";

type Listener = () => void;

const realtime = vi.hoisted(() => ({
  listeners: [] as { filter: unknown; listener: () => void }[],
  status: null as ((status: string) => void) | null,
  names: [] as string[],
  removed: [] as string[],
}));

vi.mock("../api/supabase", () => ({
  supabase: {
    channel: (name: string) => {
      realtime.names.push(name);
      const channel = {
        name,
        on: (_event: string, filter: unknown, listener: Listener) => {
          realtime.listeners.push({ filter, listener });
          return channel;
        },
        subscribe: (callback: (status: string) => void) => {
          realtime.status = callback;
          return channel;
        },
      };
      return channel;
    },
    removeChannel: (channel: { name: string }) => {
      realtime.removed.push(channel.name);
      return Promise.resolve("ok");
    },
  },
}));

function source(overrides: Partial<LiveSource<number>> = {}) {
  const values: number[] = [];
  const errors: unknown[] = [];
  let next = 0;
  const load = vi.fn(() => Promise.resolve((next += 1)));
  return {
    values,
    errors,
    load,
    source: {
      channel: "live-test",
      tables: [{ table: "jobs", filter: "order_id=eq.o1" }, { schema: "crm", table: "quotes" }],
      load,
      waiting: () => true,
      onValue: (value: number) => values.push(value),
      onError: (reason: unknown) => errors.push(reason),
      ...overrides,
    },
  };
}

async function settle() {
  await vi.advanceTimersByTimeAsync(0);
}

beforeEach(() => {
  vi.useFakeTimers();
  realtime.listeners.length = 0;
  realtime.names.length = 0;
  realtime.removed.length = 0;
  realtime.status = null;
});

afterEach(() => {
  vi.useRealTimers();
});

describe("followLive", () => {
  it("loads at once and hands the value over", async () => {
    const live = source();
    followLive(live.source);
    await settle();
    expect(live.values).toEqual([1]);
  });

  it("listens to every table on one channel, in schema public unless named", () => {
    followLive(source().source);
    expect(realtime.names).toEqual(["live-test"]);
    expect(realtime.listeners.map(({ filter }) => filter)).toEqual([
      { event: "*", schema: "public", table: "jobs", filter: "order_id=eq.o1" },
      { event: "*", schema: "crm", table: "quotes" },
    ]);
  });

  it("reloads on a change and once when Realtime connects", async () => {
    const live = source();
    followLive(live.source);
    await settle();
    realtime.listeners[0]?.listener();
    await settle();
    realtime.status?.("SUBSCRIBED");
    await settle();
    realtime.status?.("SUBSCRIBED");
    await settle();
    expect(live.values).toEqual([1, 2, 3]);
  });

  it("polls every five seconds while Realtime is down and the server still has to act", async () => {
    const live = source({ waiting: (value) => value < 3 });
    followLive(live.source);
    await settle();
    await vi.advanceTimersByTimeAsync(5000);
    await vi.advanceTimersByTimeAsync(5000);
    await vi.advanceTimersByTimeAsync(5000);
    expect(live.values).toEqual([1, 2, 3]);
  });

  it("does not poll while Realtime is connected", async () => {
    const live = source();
    followLive(live.source);
    realtime.status?.("SUBSCRIBED");
    await settle();
    await vi.advanceTimersByTimeAsync(15000);
    expect(live.load).toHaveBeenCalledTimes(2);
  });

  it("drops an answer that a newer load overtook", async () => {
    const answers: ((value: number) => void)[] = [];
    const live = source({ load: () => new Promise<number>((resolve) => answers.push(resolve)) });
    const follow = followLive(live.source);
    follow.reload();
    answers[1]?.(2);
    answers[0]?.(1);
    await settle();
    expect(live.values).toEqual([2]);
  });

  it("reports a failed load", async () => {
    const live = source({ load: () => Promise.reject(new Error("weg")) });
    followLive(live.source);
    await settle();
    expect(live.errors).toEqual([new Error("weg")]);
  });

  it("stops for good: no late answer, no polling, channel removed", async () => {
    let answer: (value: number) => void = () => undefined;
    const live = source({ load: () => new Promise<number>((resolve) => (answer = resolve)) });
    followLive(live.source).stop();
    answer(1);
    await vi.advanceTimersByTimeAsync(15000);
    expect(live.values).toEqual([]);
    expect(realtime.removed).toEqual(["live-test"]);
  });
});
