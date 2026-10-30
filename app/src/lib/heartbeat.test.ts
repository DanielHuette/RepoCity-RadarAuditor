import { describe, expect, it } from "vitest";
import { ANALYSIS_UNAVAILABLE, HEARTBEAT_MAX_AGE_MS, MAIL_READING_UNAVAILABLE, parseHeartbeat, serverStatus } from "./heartbeat";

const NOW = new Date("2026-10-23T10:00:00Z");

describe("parseHeartbeat", () => {
  it("reads time, version and capabilities", () => {
    expect(parseHeartbeat({ at: "2026-10-23T09:59:30Z", version: "0.4.0", capabilities: { browser: true } })).toEqual({
      at: new Date("2026-10-23T09:59:30Z"),
      version: "0.4.0",
      capabilities: { browser: true },
    });
  });

  it("accepts a heartbeat without version and capabilities", () => {
    expect(parseHeartbeat({ at: "2026-10-23T09:59:30Z" })).toEqual({
      at: new Date("2026-10-23T09:59:30Z"),
      version: null,
      capabilities: {},
    });
  });

  it.each([null, undefined, "2026-10-23", 42, [], {}, { at: "gestern" }, { at: 17 }])("rejects %j", (value) => {
    expect(parseHeartbeat(value)).toBeNull();
  });
});

describe("serverStatus", () => {
  it("is offline without any heartbeat", () => {
    expect(serverStatus(null, NOW)).toEqual({ online: false, lastSeen: null, blockedReason: null, mailProblem: null });
  });

  it("is online when the last sign of life is younger than two minutes", () => {
    const at = new Date(NOW.getTime() - HEARTBEAT_MAX_AGE_MS + 1000);
    expect(serverStatus({ at, version: null, capabilities: {} }, NOW)).toEqual({ online: true, lastSeen: at, blockedReason: null, mailProblem: null });
  });

  it("is offline once the heartbeat is two minutes old", () => {
    const at = new Date(NOW.getTime() - HEARTBEAT_MAX_AGE_MS);
    expect(serverStatus({ at, version: null, capabilities: {} }, NOW)).toEqual({ online: false, lastSeen: at, blockedReason: null, mailProblem: null });
  });

  it("counts a heartbeat slightly ahead of the phone clock as online", () => {
    const at = new Date(NOW.getTime() + 20_000);
    expect(serverStatus({ at, version: null, capabilities: {} }, NOW).online).toBe(true);
  });
});

describe("blocked audits", () => {
  const fresh = new Date(NOW.getTime() - 30_000);
  const stale = new Date(NOW.getTime() - HEARTBEAT_MAX_AGE_MS - 1000);

  it("blocks with the reason while a fresh heartbeat says the analysis is unavailable", () => {
    const capabilities = { analysis_available: false, analysis_reason: "Claude Code ist nicht angemeldet" };
    expect(serverStatus({ at: fresh, version: null, capabilities }, NOW).blockedReason).toBe("Claude Code ist nicht angemeldet");
  });

  it("names a general reason when the worker sends none", () => {
    const capabilities = { analysis_available: false, analysis_reason: null };
    expect(serverStatus({ at: fresh, version: null, capabilities }, NOW).blockedReason).toBe(ANALYSIS_UNAVAILABLE);
  });

  it("does not block while the analysis is available or not reported", () => {
    expect(serverStatus({ at: fresh, version: null, capabilities: { analysis_available: true } }, NOW).blockedReason).toBeNull();
    expect(serverStatus({ at: fresh, version: null, capabilities: {} }, NOW).blockedReason).toBeNull();
  });

  it("keeps queueing allowed when the heartbeat is stale", () => {
    const capabilities = { analysis_available: false, analysis_reason: "Claude Code ist nicht angemeldet" };
    expect(serverStatus({ at: stale, version: null, capabilities }, NOW)).toEqual({ online: false, lastSeen: stale, blockedReason: null, mailProblem: null });
  });
});

describe("mail reading", () => {
  const fresh = new Date(NOW.getTime() - 30_000);
  const stale = new Date(NOW.getTime() - HEARTBEAT_MAX_AGE_MS - 1000);

  it("names why a fresh heartbeat says mails are not read", () => {
    const capabilities = { mail_reading_available: false, mail_reading_reason: "Die Mails im CRM lassen sich nicht abholen" };
    expect(serverStatus({ at: fresh, version: null, capabilities }, NOW).mailProblem).toBe("Die Mails im CRM lassen sich nicht abholen");
  });

  it("names a general reason when the worker sends none", () => {
    const capabilities = { mail_reading_available: false };
    expect(serverStatus({ at: fresh, version: null, capabilities }, NOW).mailProblem).toBe(MAIL_READING_UNAVAILABLE);
  });

  it("reports nothing while mails are read, not reported or the heartbeat is stale", () => {
    const off = { mail_reading_available: false, mail_reading_reason: "Claude Code ist nicht angemeldet" };
    expect(serverStatus({ at: fresh, version: null, capabilities: { mail_reading_available: true } }, NOW).mailProblem).toBeNull();
    expect(serverStatus({ at: fresh, version: null, capabilities: {} }, NOW).mailProblem).toBeNull();
    expect(serverStatus({ at: stale, version: null, capabilities: off }, NOW).mailProblem).toBeNull();
  });
});
