import { describe, expect, it } from "vitest";
import { lineDiff } from "./diff";

describe("lineDiff", () => {
  it("shows a replaced single line as removed then added", () => {
    expect(lineDiff("Startseite", "Dachdecker in Münster – Muster GmbH")).toEqual([
      { kind: "removed", text: "Startseite" },
      { kind: "added", text: "Dachdecker in Münster – Muster GmbH" },
    ]);
  });

  it("keeps unchanged lines and marks the changed ones", () => {
    expect(lineDiff("Mo–Fr 8–17\nSa geschlossen\nSo geschlossen", "Mo–Fr 8–17\nSa 9–13\nSo geschlossen")).toEqual([
      { kind: "same", text: "Mo–Fr 8–17" },
      { kind: "removed", text: "Sa geschlossen" },
      { kind: "added", text: "Sa 9–13" },
      { kind: "same", text: "So geschlossen" },
    ]);
  });

  it("treats a missing before as all added", () => {
    expect(lineDiff(null, "Neu\nZeile")).toEqual([
      { kind: "added", text: "Neu" },
      { kind: "added", text: "Zeile" },
    ]);
  });

  it("normalises Windows line ends", () => {
    expect(lineDiff("a\r\nb", "a\nb")).toEqual([
      { kind: "same", text: "a" },
      { kind: "same", text: "b" },
    ]);
  });

  it("handles removed trailing lines", () => {
    expect(lineDiff("a\nb\nc", "a")).toEqual([
      { kind: "same", text: "a" },
      { kind: "removed", text: "b" },
      { kind: "removed", text: "c" },
    ]);
  });
});
