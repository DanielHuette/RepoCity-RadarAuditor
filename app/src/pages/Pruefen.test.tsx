// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { LocationProvider } from "preact-iso";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OpenAuditRequest } from "../api/types";
import { AdminContext } from "../hooks/role";
import { SELECTION_STORAGE_KEY } from "../lib/checkSelection";
import { Pruefen } from "./Pruefen";

const startFieldAudit = vi.fn<(request: unknown) => Promise<string>>();
const listOpenAuditRequests = vi.fn<() => Promise<OpenAuditRequest[]>>();

vi.mock("../api/supabase", () => ({ supabase: {} }));
vi.mock("../api/queries", () => ({
  startFieldAudit: (request: unknown) => startFieldAudit(request),
  listOpenAuditRequests: () => listOpenAuditRequests(),
  getHeartbeat: () => Promise.resolve(null),
  getAuditProgress: () => new Promise(() => undefined),
}));
vi.mock("../hooks/auditProgress", () => ({ useAuditProgress: () => ({ audit: undefined, progress: null, error: null }) }));

let root: HTMLDivElement;

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function mount(admin?: boolean) {
  await act(() => {
    render(
      <AdminContext.Provider value={admin}>
        <LocationProvider>
          <Pruefen />
        </LocationProvider>
      </AdminContext.Provider>,
      root,
    );
  });
  await flush();
}

async function type(value: string) {
  const input = root.querySelector<HTMLInputElement>(".url-field__input");
  if (input === null) throw new Error("no address field");
  await act(() => {
    input.value = value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function submit(selector: string) {
  const form = root.querySelector(selector);
  if (form === null) throw new Error(`no ${selector}`);
  await act(() => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  await flush();
}

beforeEach(() => {
  startFieldAudit.mockReset();
  listOpenAuditRequests.mockReset();
  listOpenAuditRequests.mockResolvedValue([]);
  localStorage.clear();
  window.history.replaceState(null, "", "/");
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  root.remove();
  vi.restoreAllMocks();
});

describe("Prüfen, step 1", () => {
  it("shows only the address field and the button", async () => {
    await mount(true);
    expect(root.querySelectorAll("input")).toHaveLength(1);
    expect([...root.querySelectorAll("button")].map((button) => button.textContent)).toEqual(["Prüfen"]);
    expect(root.querySelector("h1, h2")).toBeNull();
  });

  it("names an invalid address and stays on step 1", async () => {
    await mount();
    await type("muster");
    await submit(".start");
    expect(root.querySelector('[role="alert"]')?.textContent).toBe("Bitte eine Adresse wie meisterbetrieb-muster.de eingeben.");
    expect(root.querySelector(".selection")).toBeNull();
  });
});

describe("Prüfen, step 2", () => {
  it("lists the 14 areas with the address and starts with only website and switched-off checks", async () => {
    startFieldAudit.mockResolvedValue("audit-9");
    await mount();
    await type("Dachdeckerei-Muster.de");
    await submit(".start");
    expect(root.querySelector(".address-label")?.textContent).toBe("dachdeckerei-muster.de");
    expect(root.querySelectorAll(".sel-area")).toHaveLength(14);
    expect(root.querySelector(".sel-row--all .sel-row__count")?.textContent).toBe("104 von 108");
    await submit(".choose");
    expect(startFieldAudit).toHaveBeenCalledWith({
      p_website: "https://dachdeckerei-muster.de/",
      p_disabled_checks: ["m1", "m2", "m3", "m4"],
    });
    expect(window.location.search).toBe("?pruefung=audit-9");
  });

  it("remembers the selection on this device", async () => {
    await mount();
    await type("muster.de");
    await submit(".start");
    const google = root.querySelector<HTMLInputElement>(".sel-area input");
    if (google === null) throw new Error("no area checkbox");
    await act(() => {
      google.click();
    });
    const stored = JSON.parse(localStorage.getItem(SELECTION_STORAGE_KEY) ?? "[]") as string[];
    expect(stored.filter((id) => id.startsWith("f"))).toHaveLength(15);
    expect(root.querySelector(".sel-row--all .sel-row__count")?.textContent).toBe("89 von 108");
  });

  it("works with the default selection when the browser refuses storage", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    startFieldAudit.mockResolvedValue("audit-10");
    await mount();
    await type("muster.de");
    await submit(".start");
    expect(root.querySelector(".sel-row--all .sel-row__count")?.textContent).toBe("104 von 108");
    await submit(".choose");
    expect(startFieldAudit).toHaveBeenCalledWith({ p_website: "https://muster.de/", p_disabled_checks: ["m1", "m2", "m3", "m4"] });
  });

  it("opens an area with its chevron and shows each check with its reason", async () => {
    await mount();
    await type("muster.de");
    await submit(".start");
    const chevron = root.querySelector<HTMLButtonElement>(".sel-area:nth-child(13) .sel-row__open");
    if (chevron === null) throw new Error("no chevron");
    await act(() => {
      chevron.click();
    });
    expect(chevron.getAttribute("aria-expanded")).toBe("true");
    const checks = root.querySelectorAll(".sel-area:nth-child(13) .sel-check");
    expect(checks).toHaveLength(3);
    expect(checks[0]?.querySelector(".tag--new")?.textContent).toBe("neu");
    expect(checks[0]?.querySelector(".sel-check__why")?.textContent).not.toBe("");
  });
});

function openRequest(overrides: Partial<OpenAuditRequest>): OpenAuditRequest {
  return {
    id: "r1",
    customer_id: null,
    company: "Dachdeckerei Muster",
    contact: "Erika Muster",
    website: "https://dachdeckerei-muster.de/",
    kind: "visit",
    wished_at: "2026-11-03T08:00:00+00:00",
    status: "open",
    created_at: "2026-10-30T10:00:00+00:00",
    ...overrides,
  };
}

const TWO_REQUESTS = [
  openRequest({ id: "ohne", company: "Malerei Beispiel", contact: "Max Beispiel", website: null, kind: "call", wished_at: "2026-11-04T09:00:00Z" }),
  openRequest({}),
];

describe("Prüfen, open requests", () => {
  it("asks nobody but the administrator for them", async () => {
    await mount(false);
    expect(listOpenAuditRequests).not.toHaveBeenCalled();
    expect(root.querySelector(".requests")).toBeNull();
  });

  it("shows a closed list below the button, earliest wish first", async () => {
    listOpenAuditRequests.mockResolvedValue(TWO_REQUESTS);
    await mount(true);
    const list = root.querySelector<HTMLDetailsElement>("details.requests");
    expect(list?.open).toBe(false);
    expect(list?.querySelector("summary")?.textContent).toBe("2 offene Anfragen");
    const rows = [...root.querySelectorAll(".request")];
    expect(rows.map((row) => row.querySelector(".request__title")?.textContent)).toEqual(["Dachdeckerei Muster", "Malerei Beispiel"]);
    expect(rows[0]?.textContent).toContain("Erika Muster");
    expect(rows[0]?.textContent).toContain("Vor Ort");
    expect(rows[0]?.textContent).toContain("Dienstag, 03.11.2026, 09:00 Uhr");
    expect(rows[0]?.textContent).toContain("dachdeckerei-muster.de");
  });

  it("offers no check for a request without a website", async () => {
    listOpenAuditRequests.mockResolvedValue(TWO_REQUESTS);
    await mount(true);
    const without = [...root.querySelectorAll(".request")][1];
    expect(without?.tagName).not.toBe("BUTTON");
    expect(without?.querySelector("button")).toBeNull();
    expect(without?.textContent).toContain("Keine Webseite angegeben");
  });

  it("puts the website of a tapped request into the address field, and Prüfen goes on as usual", async () => {
    listOpenAuditRequests.mockResolvedValue(TWO_REQUESTS);
    await mount(true);
    const list = root.querySelector<HTMLDetailsElement>("details.requests");
    if (list === null) throw new Error("no request list");
    list.open = true;
    const tap = root.querySelector<HTMLButtonElement>("button.request");
    if (tap === null) throw new Error("no request button");
    await act(() => {
      tap.click();
    });
    await flush();
    expect(root.querySelector<HTMLInputElement>(".url-field__input")?.value).toBe("https://dachdeckerei-muster.de/");
    expect(root.querySelector<HTMLDetailsElement>("details.requests")?.open).toBe(false);
    expect(document.activeElement?.textContent).toBe("Prüfen");
    expect(startFieldAudit).not.toHaveBeenCalled();
    await submit(".start");
    expect(root.querySelector(".address-label")?.textContent).toBe("dachdeckerei-muster.de");
  });

  it("names a failed load inside the closed list instead of hiding it", async () => {
    listOpenAuditRequests.mockRejectedValue(new Error("boom"));
    await mount(true);
    expect(root.querySelector("details.requests summary")?.textContent).toBe("Offene Anfragen nicht geladen");
    expect(root.querySelector("details.requests [role='alert']")?.textContent).toContain("boom");
  });
});
