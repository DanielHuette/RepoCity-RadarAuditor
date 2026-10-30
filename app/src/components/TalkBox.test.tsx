// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TalkBox } from "./TalkBox";

const TEXT = "Sie stehen auf der Karte hinter drei Nachbarn.";
const copyText = vi.fn<(text: string) => Promise<void>>();

vi.mock("../lib/clipboard", () => ({ copyText: (text: string) => copyText(text) }));

let root: HTMLDivElement;

beforeEach(() => {
  copyText.mockReset();
  root = document.createElement("div");
});

afterEach(() => {
  render(null, root);
});

describe("TalkBox", () => {
  it("shows the conversation text with the gold copy button", async () => {
    await act(() => {
      render(<TalkBox text={TEXT} />, root);
    });
    expect(root.querySelector("h2")?.textContent).toBe("Das sagen Sie dem Kunden");
    expect(root.querySelector(".talk__text")?.textContent).toBe(TEXT);
    const button = root.querySelector("button");
    expect(button?.textContent).toBe("Text kopieren");
    expect(button?.className).toContain("btn--main");
  });

  it("copies exactly the shown text", async () => {
    copyText.mockResolvedValue(undefined);
    await act(() => {
      render(<TalkBox text={TEXT} />, root);
    });
    await act(async () => {
      root.querySelector("button")?.click();
      await Promise.resolve();
    });
    expect(copyText).toHaveBeenCalledWith(TEXT);
    expect(root.querySelector("button")?.textContent).toBe("Kopiert");
  });
});
