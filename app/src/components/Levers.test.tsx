// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { describe, expect, it } from "vitest";
import { buildRows, topLevers } from "../lib/evaluation";
import { check } from "../lib/testRows";
import { CheckRow } from "./CheckRow";
import { Levers } from "./Levers";

const g4 = {
  ...check("g", "failed", "g4"),
  potential_points: 2.4,
  measured: {
    replied: 4,
    unanswered_examples: [
      { date: "2026-10-02", stars: 2, text: "Termin wurde zweimal verschoben." },
      { date: "vor 3 Wochen", stars: 5, text: "Sauber gearbeitet." },
    ],
  },
};
const g1 = { ...check("g", "partial", "g1"), potential_points: 4.1, measured: { customer_reviews: 12 } };

describe("Auswertung parts", () => {
  it("shows unanswered reviews of g4 as boxes with gold stars, never as a raw line", async () => {
    const root = document.createElement("div");
    const [row] = buildRows([g4], []);
    if (row === undefined) throw new Error("no row");
    await act(() => {
      render(
        <ul>
          <CheckRow row={row} />
        </ul>,
        root,
      );
    });
    expect(root.querySelector(".check__why")?.textContent).not.toBe("");
    expect(root.querySelector(".review-cards__title")?.textContent).toBe("Ohne Antwort, zum Beispiel");
    const cards = [...root.querySelectorAll(".review-card")];
    expect(cards.map((card) => card.querySelector(".review-card__stars")?.textContent)).toEqual(["★★", "★★★★★"]);
    expect(cards[0]?.querySelector(".review-card__stars")?.getAttribute("aria-label")).toBe("2 von 5 Sternen");
    expect(cards.map((card) => card.querySelector(".review-card__meta span:last-child")?.textContent)).toEqual(["02.10.2026", "vor 3 Wochen"]);
    expect(cards[0]?.querySelector(".review-card__text")?.textContent).toBe("„Termin wurde zweimal verschoben.“");
    expect(root.textContent).not.toContain("unanswered_examples");
    expect(root.textContent).not.toContain("[object Object]");
    render(null, root);
  });

  it("numbers the three levers and opens the check a lever points to", async () => {
    const root = document.createElement("div");
    document.body.appendChild(root);
    const rows = buildRows([g1, g4], []);
    await act(() => {
      render(
        <div>
          <Levers levers={topLevers([g1, g4])} />
          <ul>
            {rows.map((row) => (
              <CheckRow key={row.check.id} row={row} />
            ))}
          </ul>
        </div>,
        root,
      );
    });
    const levers = [...root.querySelectorAll(".lever")];
    expect(levers.map((lever) => lever.querySelector(".lever__rank")?.textContent)).toEqual(["1", "2"]);
    expect(levers.map((lever) => lever.querySelector(".lever__points")?.textContent)).toEqual(["+4,1", "+2,4"]);
    expect(levers[0]?.querySelector(".lever__measured")?.textContent).toBe("Eigene Bewertungen: 12");
    const details = root.querySelector<HTMLDetailsElement>("#pruefung-g4 details");
    expect(details?.open).toBe(false);
    await act(() => {
      (levers[1] as HTMLButtonElement).click();
    });
    expect(details?.open).toBe(true);
    render(null, root);
    root.remove();
  });
});
