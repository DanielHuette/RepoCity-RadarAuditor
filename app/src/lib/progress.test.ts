import { describe, expect, it } from "vitest";
import type { AuditStatus, JobKind, JobStatus } from "../api/types";
import { analysisWaitText, auditProgress, formatNextAttempt, isFinished, stepOfAuditStatus, type ProgressJob } from "./progress";

function job(
  kind: JobKind,
  status: JobStatus,
  created_at = "2026-10-23T08:00:00Z",
  error: string | null = null,
  run_after: string | null = null,
): ProgressJob {
  return { kind, status, error, created_at, run_after };
}

describe("stepOfAuditStatus", () => {
  it.each<[AuditStatus, string]>([
    ["requested", "waiting"],
    ["collecting", "collecting"],
    ["collected", "scoring"],
    ["scored", "analysing"],
    ["awaiting_analysis", "analysing"],
    ["analysed", "analysing"],
    ["reported", "done"],
    ["failed", "failed"],
  ])("maps %s to %s", (status, step) => {
    expect(stepOfAuditStatus(status)).toBe(step);
  });
});

describe("auditProgress", () => {
  it("waits while the collect job is queued", () => {
    expect(auditProgress({ status: "requested", error: null }, [job("collect", "queued")])).toEqual({
      step: "waiting",
      serpRetry: false,
      analysisWait: null,
      error: null,
    });
  });

  it("shows collecting as soon as the collect job runs, before the audit row changes", () => {
    expect(auditProgress({ status: "requested", error: null }, [job("collect", "running")]).step).toBe("collecting");
  });

  it("never goes back behind the audit status because of an older job", () => {
    expect(auditProgress({ status: "awaiting_analysis", error: null }, [job("collect", "running")]).step).toBe("analysing");
  });

  it("reports the deferred Google search while a serp_retry job is queued", () => {
    const progress = auditProgress({ status: "awaiting_analysis", error: null }, [job("collect", "done"), job("serp_retry", "queued")]);
    expect(progress.serpRetry).toBe(true);
    expect(progress.step).toBe("analysing");
  });

  it("drops the deferred search note once the retry is done", () => {
    expect(auditProgress({ status: "reported", error: null }, [job("serp_retry", "done")]).serpRetry).toBe(false);
  });

  it("fails with the audit error when the audit failed", () => {
    expect(auditProgress({ status: "failed", error: "Website nicht erreichbar" }, [])).toEqual({
      step: "failed",
      serpRetry: false,
      analysisWait: null,
      error: "Website nicht erreichbar",
    });
  });

  it("fails with the job error when the newest job failed", () => {
    const progress = auditProgress({ status: "collecting", error: null }, [
      job("collect", "failed", "2026-10-23T08:05:00Z", "Browser abgestürzt"),
    ]);
    expect(progress.step).toBe("failed");
    expect(progress.error).toBe("Browser abgestürzt");
  });

  it("ignores a failed job that a newer job of the same kind replaced", () => {
    const progress = auditProgress({ status: "collecting", error: null }, [
      job("collect", "failed", "2026-10-23T08:00:00Z", "Zeitüberschreitung"),
      job("collect", "running", "2026-10-23T08:10:00Z"),
    ]);
    expect(progress.step).toBe("collecting");
  });

  it("does not fail a reported audit because a deferred search failed", () => {
    expect(auditProgress({ status: "reported", error: null }, [job("serp_retry", "failed")]).step).toBe("done");
  });
});

describe("analysis waiting for Claude Code", () => {
  const reason = "Claude Code ist nicht angemeldet";

  it("reports the reason and the next attempt of a re-queued analyse job", () => {
    const progress = auditProgress({ status: "awaiting_analysis", error: null }, [
      job("collect", "done"),
      job("analyse", "queued", "2026-10-23T08:10:00Z", reason, "2026-10-23T08:40:00Z"),
    ]);
    expect(progress.step).toBe("analysing");
    expect(progress.analysisWait).toEqual({ reason, nextAttempt: "2026-10-23T08:40:00Z" });
  });

  it("says nothing while the analyse job runs again or has no reason", () => {
    const running = [job("analyse", "running", "2026-10-23T08:10:00Z", reason, "2026-10-23T08:40:00Z")];
    const fresh = [job("analyse", "queued", "2026-10-23T08:10:00Z")];
    expect(auditProgress({ status: "awaiting_analysis", error: null }, running).analysisWait).toBeNull();
    expect(auditProgress({ status: "awaiting_analysis", error: null }, fresh).analysisWait).toBeNull();
  });

  it("says nothing once the audit moved on", () => {
    const jobs = [job("analyse", "queued", "2026-10-23T08:10:00Z", reason)];
    expect(auditProgress({ status: "analysed", error: null }, jobs).analysisWait).toBeNull();
    expect(auditProgress({ status: "reported", error: null }, jobs).analysisWait).toBeNull();
  });
});

describe("formatNextAttempt", () => {
  const now = new Date(2026, 9, 23, 10, 0);

  it("gives the clock time today and the date on another day", () => {
    expect(formatNextAttempt(new Date(2026, 9, 23, 14, 30).toISOString(), now)).toBe("um 14:30 Uhr");
    expect(formatNextAttempt(new Date(2026, 9, 24, 9, 5).toISOString(), now)).toBe("am 24.10. um 09:05 Uhr");
  });

  it("says gleich when the time has come and nothing without a usable time", () => {
    expect(formatNextAttempt(new Date(2026, 9, 23, 9, 59).toISOString(), now)).toBe("gleich");
    expect(formatNextAttempt(null, now)).toBeNull();
    expect(formatNextAttempt("bald", now)).toBeNull();
  });

  it("builds the waiting line with and without a next attempt", () => {
    const reason = "Claude Code antwortet nicht";
    const later = new Date(2026, 9, 23, 11, 15).toISOString();
    expect(analysisWaitText({ reason, nextAttempt: later }, now)).toBe(
      "Auswertung wartet: Claude Code antwortet nicht – nächster Versuch um 11:15 Uhr",
    );
    expect(analysisWaitText({ reason, nextAttempt: null }, now)).toBe("Auswertung wartet: Claude Code antwortet nicht");
  });
});

describe("isFinished", () => {
  it("is true for done and failed only", () => {
    expect(isFinished("done")).toBe(true);
    expect(isFinished("failed")).toBe(true);
    expect(isFinished("analysing")).toBe(false);
  });
});
