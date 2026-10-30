// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChipGroup, ErrorAlert } from "./ui";

let root: HTMLDivElement;

beforeEach(() => {
  root = document.createElement("div");
  document.body.append(root);
});

afterEach(() => {
  render(null, root);
  root.remove();
});

describe("ChipGroup", () => {
  const LABELS: Record<string, string> = { organic: "Google-Suche", local: "Karte" };

  it("shows one chip per value and marks the selected one", () => {
    render(<ChipGroup label="Art" values={["organic", "local"]} selected="local" labelOf={(value) => LABELS[value] ?? value} onSelect={() => undefined} />, root);
    const group = root.querySelector('.chips[role="group"]');
    expect(group?.getAttribute("aria-label")).toBe("Art");
    const chips = [...root.querySelectorAll("button.chip")];
    expect(chips.map((chip) => chip.textContent)).toEqual(["Google-Suche", "Karte"]);
    expect(chips.map((chip) => chip.getAttribute("aria-pressed"))).toEqual(["false", "true"]);
    expect(chips.every((chip) => chip.getAttribute("type") === "button")).toBe(true);
  });

  it("reports the tapped value", async () => {
    const onSelect = vi.fn<(value: string) => void>();
    render(<ChipGroup label="Art" values={["organic", "local"]} selected="organic" labelOf={(value) => value} onSelect={onSelect} />, root);
    await act(() => {
      root.querySelectorAll<HTMLButtonElement>("button.chip")[1]?.click();
    });
    expect(onSelect).toHaveBeenCalledWith("local");
  });
});

describe("ErrorAlert", () => {
  it("announces a message", () => {
    render(<ErrorAlert message="Das hat nicht geklappt." />, root);
    expect(root.innerHTML).toBe('<p class="field__error" role="alert">Das hat nicht geklappt.</p>');
  });

  it("renders nothing without a message", () => {
    render(<ErrorAlert message={null} />, root);
    expect(root.innerHTML).toBe("");
  });
});
