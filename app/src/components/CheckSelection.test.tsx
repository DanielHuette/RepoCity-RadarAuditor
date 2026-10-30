// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CatalogueArea } from "../lib/catalogue";
import { catalogueCheck } from "../lib/testRows";
import { CheckSelection } from "./CheckSelection";

const google: CatalogueArea = { id: "g", name: "Bewertungen", weight: 10, checks: [catalogueCheck("g1"), catalogueCheck("g2", { requiresPaid: true })] };
const access: CatalogueArea = { id: "m", name: "Wirkung mit Kundenzugang", weight: 0, checks: [catalogueCheck("m1", { requiresAccess: true })] };
const AREAS = [access, google];

const onChange = vi.fn<(next: string[]) => void>();
let root: HTMLDivElement;

beforeEach(() => {
  onChange.mockReset();
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  root.remove();
});

async function mount(disabled: string[]) {
  await act(() => {
    render(<CheckSelection areas={AREAS} disabled={disabled} onChange={onChange} />, root);
  });
}

function areaRow(name: string): HTMLElement {
  const row = [...root.querySelectorAll<HTMLElement>(".sel-area")].find((element) => element.querySelector(".sel-row__name")?.textContent === name);
  if (row === undefined) throw new Error(`no area ${name}`);
  return row;
}

async function toggle(input: HTMLInputElement | null) {
  if (input === null) throw new Error("no checkbox");
  await act(() => {
    input.click();
  });
}

describe("CheckSelection", () => {
  it("counts the switched-on checks and lists the areas in page order with their tags", async () => {
    await mount(["m1"]);
    expect(root.querySelector(".sel-row--all .sel-row__count")?.textContent).toBe("2 von 3");
    const names = [...root.querySelectorAll(".sel-area .sel-row__name")].map((element) => element.textContent);
    expect(names).toEqual(["Bewertungen", "Wirkung mit Kundenzugang"]);
    expect(areaRow("Bewertungen").querySelector(".tag")?.textContent).toBe("1 kostet Geld");
    expect(areaRow("Wirkung mit Kundenzugang").querySelector<HTMLInputElement>(".cb")?.checked).toBe(false);
  });

  it("marks the whole selection as partly on", async () => {
    await mount(["g2"]);
    const all = root.querySelector<HTMLInputElement>(".sel-row--all .cb");
    expect(all?.indeterminate).toBe(true);
  });

  it("switches everything, one area and one check", async () => {
    await mount(["m1"]);
    await toggle(root.querySelector<HTMLInputElement>(".sel-row--all .cb"));
    expect(onChange).toHaveBeenLastCalledWith([]);

    await toggle(areaRow("Bewertungen").querySelector<HTMLInputElement>(".cb"));
    expect(onChange).toHaveBeenLastCalledWith(["g1", "g2", "m1"]);

    const open = areaRow("Bewertungen").querySelector<HTMLButtonElement>(".sel-row__open");
    expect(open?.getAttribute("aria-expanded")).toBe("false");
    await act(() => {
      open?.click();
    });
    expect(open?.getAttribute("aria-expanded")).toBe("true");
    const lines = [...areaRow("Bewertungen").querySelectorAll<HTMLInputElement>(".sel-check .cb")];
    expect(lines).toHaveLength(2);
    await toggle(lines[1] ?? null);
    expect(onChange).toHaveBeenLastCalledWith(["g2", "m1"]);
  });
});
