import { describe, expect, it } from "vitest";
import type { Job, OptimizationChange } from "../api/types";
import { groupChanges, isManualChange, isOptimizationBusy, latestJob, manualInstruction } from "./optimization";

function change(id: string, status: OptimizationChange["status"], times: Partial<OptimizationChange> = {}): OptimizationChange {
  return {
    id,
    order_id: "o1",
    audit_id: "a1",
    check_id: "b3",
    target: "WordPress: Startseite, Seitentitel",
    before: "Startseite",
    after: "Dachdecker in Münster",
    status,
    error: null,
    created_at: `2026-10-23T08:0${id}:00Z`,
    decided_at: null,
    applied_at: null,
    ...times,
  };
}

function job(kind: Job["kind"], status: Job["status"], created: string): Job {
  return {
    id: `${kind}-${created}`,
    kind,
    status,
    audit_id: "a1",
    customer_id: "c1",
    attempts: 0,
    error: null,
    created_at: created,
    updated_at: created,
    order_id: "o1",
    run_after: null,
  };
}

describe("groupChanges", () => {
  it("splits drafts, released, log and rejected", () => {
    const groups = groupChanges([
      change("4", "applied", { applied_at: "2026-10-23T09:00:00Z" }),
      change("2", "draft"),
      change("1", "draft"),
      change("3", "approved"),
      change("5", "failed", { applied_at: "2026-10-23T10:00:00Z", error: "WordPress antwortet nicht" }),
      change("6", "rejected"),
    ]);
    expect(groups.drafts.map((item) => item.id)).toEqual(["1", "2"]);
    expect(groups.approved.map((item) => item.id)).toEqual(["3"]);
    expect(groups.log.map((item) => item.id)).toEqual(["5", "4"]);
    expect(groups.rejected.map((item) => item.id)).toEqual(["6"]);
    expect(groups.manual).toEqual([]);
  });

  it("lists work by hand apart from the log, open to-dos first", () => {
    const groups = groupChanges([
      change("1", "manual_done", { error: "Manuell umsetzen: kein automatischer Weg für dieses Ziel" }),
      change("2", "manual", { error: "Manuell umsetzen: strukturierte Daten (JSON-LD) im Theme einbinden" }),
      change("3", "failed", { error: "Manuell umsetzen: kein Suchmaschinen-Plugin aktiv" }),
      change("4", "failed", { error: "WordPress antwortet nicht" }),
    ]);
    expect(groups.manual.map((item) => item.id)).toEqual(["2", "3", "1"]);
    expect(groups.log.map((item) => item.id)).toEqual(["4"]);
  });
});

describe("manual changes", () => {
  it("recognises the manual statuses and failed changes with the manual reason", () => {
    expect(isManualChange(change("1", "manual"))).toBe(true);
    expect(isManualChange(change("1", "manual_done"))).toBe(true);
    expect(isManualChange(change("1", "failed", { error: "Manuell umsetzen: Branchenbuch" }))).toBe(true);
    expect(isManualChange(change("1", "failed", { error: "Kein Zugang zu WordPress hinterlegt" }))).toBe(false);
    expect(isManualChange(change("1", "failed"))).toBe(false);
    expect(isManualChange(change("1", "applied", { error: "Manuell umsetzen: x" }))).toBe(false);
  });

  it("reads the instruction after the prefix", () => {
    expect(manualInstruction(change("1", "manual", { error: "Manuell umsetzen: strukturierte Daten einbinden" }))).toBe(
      "strukturierte Daten einbinden",
    );
    expect(manualInstruction(change("1", "manual", { error: "Manuell umsetzen:" }))).toBeNull();
    expect(manualInstruction(change("1", "manual"))).toBeNull();
    expect(manualInstruction(change("1", "failed", { error: "WordPress antwortet nicht" }))).toBeNull();
  });
});

describe("latestJob", () => {
  it("returns the newest job of the kind", () => {
    const jobs = [job("optimize", "done", "2026-10-20"), job("apply", "queued", "2026-10-23"), job("optimize", "running", "2026-10-22")];
    expect(latestJob(jobs, "optimize")?.status).toBe("running");
    expect(latestJob(jobs, "collect")).toBeNull();
  });
});

describe("isOptimizationBusy", () => {
  it("is busy while a job is open or a released draft waits", () => {
    expect(isOptimizationBusy([job("optimize", "queued", "2026-10-23")], [])).toBe(true);
    expect(isOptimizationBusy([job("optimize", "done", "2026-10-23")], [change("1", "approved")])).toBe(true);
    expect(isOptimizationBusy([job("optimize", "done", "2026-10-23")], [change("1", "draft"), change("2", "applied")])).toBe(false);
  });
});
