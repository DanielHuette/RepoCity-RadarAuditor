import { describe, expect, it } from "vitest";
import { barLength, labelledPositions, positionFromBar, rankingMatrix } from "./rankings";
import { keyword, ranking } from "./testRows";

const keywords = [keyword("k1", "Dachdecker"), keyword("k2", "Dachsanierung"), keyword("k3", "Flachdach")];

describe("rankingMatrix", () => {
  const rankings = [
    ranking({ keyword_id: "k1", subject: "Konkurrenz Eins", organic_position: 2, local_pack_position: 1 }),
    ranking({ keyword_id: "k1", subject: "dachdecker muster", organic_position: 5, local_pack_position: 3 }),
    ranking({ keyword_id: "k2", subject: "Dachdecker Muster", organic_position: 9 }),
    ranking({ keyword_id: "k2", subject: "Dachdecker Muster", organic_position: 4 }),
    ranking({ keyword_id: "k1", subject: "Dachdecker Muster", local_pack_position: 1, grid_point: "r1c1" }),
  ];

  it("puts the customer first and keeps only measured keywords", () => {
    const matrix = rankingMatrix(rankings, keywords, "Dachdecker Muster", "organic");
    expect(matrix.keywords).toEqual(["Dachdecker", "Dachsanierung"]);
    expect(matrix.series.map((series) => series.subject)).toEqual(["dachdecker muster", "Konkurrenz Eins"]);
  });

  it("takes the best organic position and ignores grid rows", () => {
    const matrix = rankingMatrix(rankings, keywords, "Dachdecker Muster", "organic");
    expect(matrix.series[0]?.positions).toEqual([5, 4]);
    expect(matrix.series[1]?.positions).toEqual([2, null]);
  });

  it("switches to local pack positions", () => {
    const matrix = rankingMatrix(rankings, keywords, "Dachdecker Muster", "local");
    expect(matrix.series[0]?.positions).toEqual([3, null]);
  });

  it("is empty without rankings", () => {
    expect(rankingMatrix([], keywords, "Dachdecker Muster", "organic")).toEqual({ keywords: [], series: [] });
  });
});

describe("barLength", () => {
  it("gives the first place the longest bar and hides missing places", () => {
    expect(barLength(1, 20)).toBe(20);
    expect(barLength(20, 20)).toBe(1);
    expect(barLength(21, 20)).toBe(0);
    expect(barLength(null, 20)).toBe(0);
    expect(barLength(0, 20)).toBe(0);
  });

  it("converts back to the position", () => {
    expect(positionFromBar(barLength(7, 20), 20)).toBe(7);
    expect(positionFromBar(0, 20)).toBeNull();
  });
});

describe("labelledPositions", () => {
  it("labels first place and every fifth on a wide chart", () => {
    expect(labelledPositions(20, false)).toEqual([1, 5, 10, 15, 20]);
    expect(labelledPositions(10, false)).toEqual([1, 5, 10]);
  });

  it("labels only first, middle and last place on a narrow chart", () => {
    expect(labelledPositions(20, true)).toEqual([1, 10, 20]);
    expect(labelledPositions(10, true)).toEqual([1, 5, 10]);
  });

  it("does not repeat a position on a very short scale", () => {
    expect(labelledPositions(2, true)).toEqual([1, 2]);
  });
});
