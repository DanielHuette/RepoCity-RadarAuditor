import { describe, expect, it } from "vitest";
import { cleanEmail, stepOnConfirm, stepWhileWaiting } from "./quoteSending";

describe("stepOnConfirm", () => {
  it("sends at once when the PDF is ready", () => {
    expect(stepOnConfirm("ready")).toBe("send");
  });

  it("waits for a PDF that is being made", () => {
    expect(stepOnConfirm("pending")).toBe("wait_for_pdf");
  });

  it("requests the PDF when there is none or the last one failed", () => {
    expect(stepOnConfirm("none")).toBe("request_pdf");
    expect(stepOnConfirm("failed")).toBe("request_pdf");
  });
});

describe("stepWhileWaiting", () => {
  it("sends as soon as the PDF is ready", () => {
    expect(stepWhileWaiting("ready")).toBe("send");
  });

  it("stops when the PDF could not be made", () => {
    expect(stepWhileWaiting("failed")).toBe("pdf_failed");
  });

  it("keeps waiting otherwise", () => {
    expect(stepWhileWaiting("pending")).toBe("wait_for_pdf");
    expect(stepWhileWaiting("none")).toBe("wait_for_pdf");
  });

  it("ignores the old failed PDF until the new request shows up", () => {
    expect(stepWhileWaiting("failed", true)).toBe("wait_for_pdf");
    expect(stepWhileWaiting("none", true)).toBe("wait_for_pdf");
    expect(stepWhileWaiting("ready", true)).toBe("send");
  });
});

describe("cleanEmail", () => {
  it("accepts an address and trims it", () => {
    expect(cleanEmail("  erika@muster.de ")).toBe("erika@muster.de");
  });

  it("rejects text that is no address", () => {
    expect(cleanEmail("")).toBeNull();
    expect(cleanEmail("erika")).toBeNull();
    expect(cleanEmail("erika@muster")).toBeNull();
    expect(cleanEmail("erika muster@muster.de")).toBeNull();
    expect(cleanEmail("@muster.de")).toBeNull();
  });
});
