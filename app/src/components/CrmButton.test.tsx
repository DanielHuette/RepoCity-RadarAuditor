// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CrmButton } from "./CrmButton";

let root: HTMLDivElement;

beforeEach(() => {
  vi.stubEnv("VITE_CRM_ORIGIN", "https://crm.example.test");
  root = document.createElement("div");
});

afterEach(() => {
  render(null, root);
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

async function mount(crmCustomerId: string | null) {
  await act(() => {
    render(<CrmButton crmCustomerId={crmCustomerId} />, root);
  });
}

describe("CrmButton", () => {
  it("opens the customer file of the CRM in a new tab", async () => {
    const tab = { opener: {}, location: { href: "" }, close: vi.fn() };
    const open = vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    await mount("c-42");
    const button = root.querySelector("button");
    expect(button?.textContent).toBe("Kundenakte im CRM öffnen");
    await act(async () => {
      button?.click();
      await Promise.resolve();
    });
    expect(open).toHaveBeenCalledWith("about:blank", "_blank");
    expect(tab.opener).toBeNull();
    expect(tab.location.href).toBe("https://crm.example.test/kunde/c-42");
  });

  it("shows nothing for a customer not linked to the CRM", async () => {
    await mount(null);
    expect(root.innerHTML).toBe("");
  });

  it("shows nothing when the CRM address is not configured", async () => {
    vi.stubEnv("VITE_CRM_ORIGIN", "");
    await mount("c-42");
    expect(root.innerHTML).toBe("");
  });
});
