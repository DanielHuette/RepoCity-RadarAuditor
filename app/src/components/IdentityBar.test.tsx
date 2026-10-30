// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { LocationProvider } from "preact-iso";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminContext } from "../hooks/role";
import { audit, customer } from "../lib/testRows";
import { IdentityBar } from "./IdentityBar";

const startFieldAudit = vi.fn<(request: unknown) => Promise<string>>();
const identificationSource = vi.fn<(rawPath: string) => Promise<string | null>>();

vi.mock("../api/supabase", () => ({ supabase: {} }));
vi.mock("../api/queries", () => ({
  startFieldAudit: (request: unknown) => startFieldAudit(request),
  identificationSource: (rawPath: string) => identificationSource(rawPath),
}));

let root: HTMLDivElement;

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function mount(admin: boolean) {
  await act(() => {
    render(
      <LocationProvider>
        <AdminContext.Provider value={admin}>
          <IdentityBar
            customer={customer({ name: "Dachdeckerei Muster", city: "", industry: "", website: "https://muster.de/" })}
            audit={audit({ raw_data_path: "a1/raw.json", disabled_checks: ["m1", "m2", "m3", "m4", "i1"] })}
          />
        </AdminContext.Provider>
      </LocationProvider>,
      root,
    );
  });
  await flush();
}

async function fill(label: string, value: string) {
  const field = [...root.querySelectorAll<HTMLInputElement | HTMLSelectElement>("input, select")].find(
    (element) => document.getElementById(element.getAttribute("aria-labelledby") ?? "")?.textContent === label,
  );
  if (field === undefined) throw new Error(`no field ${label}`);
  await act(() => {
    field.value = value;
    field.dispatchEvent(new Event(field instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
  });
}

beforeEach(() => {
  startFieldAudit.mockReset();
  identificationSource.mockReset();
  identificationSource.mockResolvedValue("Impressum");
  window.history.replaceState(null, "", "/auswertung/a1");
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  root.remove();
});

describe("IdentityBar", () => {
  it("shows what the server identified and where it found it", async () => {
    await mount(true);
    const facts = [...root.querySelectorAll(".identity__facts > div")].map((row) => row.textContent);
    expect(facts).toEqual(["BetriebDachdeckerei Muster", "Ortnoch nicht erkannt", "Branchenoch nicht erkannt", "QuelleImpressum"]);
  });

  it("keeps the identified facts and says so when the source cannot be read", async () => {
    identificationSource.mockRejectedValue(new TypeError("Failed to fetch"));
    await mount(true);
    const facts = [...root.querySelectorAll(".identity__facts > div")].map((row) => row.textContent);
    expect(facts.slice(0, 3)).toEqual(["BetriebDachdeckerei Muster", "Ortnoch nicht erkannt", "Branchenoch nicht erkannt"]);
    expect(facts[3]).toBe("QuelleKeine Verbindung zum Server. Prüfe dein Netz und versuch es noch mal.");
  });

  it("starts a new audit with the corrected values and the same switched-off checks", async () => {
    startFieldAudit.mockResolvedValue("audit-2");
    await mount(true);
    const correct = root.querySelector<HTMLButtonElement>(".link-action");
    if (correct === null) throw new Error("no Korrigieren");
    await act(() => {
      correct.click();
    });
    await fill("Ort", "Münster");
    await fill("Branche", "bauhandwerk");
    const form = root.querySelector(".correction");
    await act(() => {
      form?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await flush();
    expect(startFieldAudit).toHaveBeenCalledWith({
      p_website: "https://muster.de/",
      p_disabled_checks: ["m1", "m2", "m3", "m4", "i1"],
      p_name: "Dachdeckerei Muster",
      p_city: "Münster",
      p_industry: "bauhandwerk",
    });
    expect(window.location.search).toBe("?pruefung=audit-2");
  });

  it("names missing fields instead of starting", async () => {
    await mount(true);
    await act(() => {
      root.querySelector<HTMLButtonElement>(".link-action")?.click();
    });
    await act(() => {
      root.querySelector(".correction")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(startFieldAudit).not.toHaveBeenCalled();
    expect(root.textContent).toContain("Bitte den Ort eingeben.");
  });

  it("offers no correction to readers", async () => {
    await mount(false);
    expect(root.querySelector(".link-action")).toBeNull();
  });
});
