// Line diff of an optimisation draft, so the change reads like a review before it is released.

export type DiffKind = "same" | "removed" | "added";

export interface DiffLine {
  kind: DiffKind;
  text: string;
}

function splitLines(text: string | null): string[] {
  if (text === null || text === "") return [];
  return text.replace(/\r\n?/g, "\n").split("\n");
}

/** Longest-common-subsequence diff; removed lines come before the lines that replace them. */
export function lineDiff(before: string | null, after: string): DiffLine[] {
  const old = splitLines(before);
  const next = splitLines(after);
  const common: number[][] = Array.from({ length: old.length + 1 }, () => new Array<number>(next.length + 1).fill(0));
  for (let i = old.length - 1; i >= 0; i -= 1) {
    for (let j = next.length - 1; j >= 0; j -= 1) {
      const row = common[i] ?? [];
      row[j] = old[i] === next[j] ? (common[i + 1]?.[j + 1] ?? 0) + 1 : Math.max(common[i + 1]?.[j] ?? 0, row[j + 1] ?? 0);
    }
  }
  const lines: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < old.length && j < next.length) {
    if (old[i] === next[j]) {
      lines.push({ kind: "same", text: old[i] ?? "" });
      i += 1;
      j += 1;
    } else if ((common[i + 1]?.[j] ?? 0) >= (common[i]?.[j + 1] ?? 0)) {
      lines.push({ kind: "removed", text: old[i] ?? "" });
      i += 1;
    } else {
      lines.push({ kind: "added", text: next[j] ?? "" });
      j += 1;
    }
  }
  for (; i < old.length; i += 1) lines.push({ kind: "removed", text: old[i] ?? "" });
  for (; j < next.length; j += 1) lines.push({ kind: "added", text: next[j] ?? "" });
  return lines;
}
