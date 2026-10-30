// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { describe, expect, it, vi } from "vitest";
import { Settings } from "./Settings";

const getSetting = vi.fn<(key: string) => Promise<unknown>>();

vi.mock("../api/supabase", () => ({ supabase: {} }));
vi.mock("../api/queries", () => ({
  getSetting: (key: string) => getSetting(key),
  saveSetting: () => Promise.resolve(),
  sourceUsageSince: () => Promise.resolve([]),
}));

describe("Settings", () => {
  it("keeps sources and cost caps and has no check toggles", async () => {
    getSetting.mockResolvedValue(null);
    const root = document.createElement("div");
    await act(() => {
      render(<Settings />, root);
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(getSetting.mock.calls.map(([key]) => key)).toEqual(["sources"]);
    expect([...root.querySelectorAll("h2")].map((heading) => heading.textContent)).toEqual(["Datenquellen", "App installieren"]);
    expect(root.querySelectorAll('input[type="checkbox"]:not([role="switch"])')).toHaveLength(0);
    expect(root.querySelectorAll('[role="switch"]').length).toBeGreaterThan(0);
    expect(root.textContent).toContain("Monatslimit");
    render(null, root);
  });
});
