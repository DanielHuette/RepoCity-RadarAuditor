// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { LocationProvider } from "preact-iso";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ProspectView } from "../api/prospects";
import type { ProspectResult, ProspectSearch } from "../api/types";
import { AdminContext } from "../hooks/role";
import { Kundensuche } from "./Kundensuche";

const requestProspectSearch = vi.fn<(trade: string, city: string) => Promise<string>>();
const addProspectToCrm = vi.fn<(id: string, entry: unknown) => Promise<string>>();
const startFieldAudit = vi.fn<(request: unknown) => Promise<string>>();
let view: ProspectView | undefined;

vi.mock("../api/supabase", () => ({ supabase: {} }));
vi.mock("../api/config", () => ({ crmOrigin: () => "https://crm.example" }));
vi.mock("../api/prospects", () => ({
  requestProspectSearch: (trade: string, city: string) => requestProspectSearch(trade, city),
  addProspectToCrm: (id: string, entry: unknown) => addProspectToCrm(id, entry),
  listRecentProspectSearches: () => Promise.resolve([]),
  loadProspectSearch: () => Promise.resolve(view ?? null),
}));
vi.mock("../api/queries", () => ({
  startFieldAudit: (request: unknown) => startFieldAudit(request),
  getHeartbeat: () => Promise.resolve(null),
}));
vi.mock("../hooks/useLive", () => ({
  useLive: () => ({ data: view, error: null, reload: () => undefined }),
}));

const SEARCH: ProspectSearch = {
  id: "s1",
  trade: "Dachdecker",
  city: "Münster",
  status: "done",
  error: null,
  result_count: 2,
  requested_at: "2026-10-30T08:00:00Z",
  claimed_at: "2026-10-30T08:00:05Z",
  fetched_at: "2026-10-30T08:00:09Z",
};

const WAYS = [
  { channel: "letter", label: "Brief", status: "allowed", note: "Erlaubt, solange kein Widerspruch." },
  { channel: "phone", label: "Anruf", status: "uncertain", note: "Nur mit konkretem Anlass." },
  { channel: "email", label: "E-Mail", status: "forbidden", note: "Nur mit Einwilligung." },
];

function result(changes: Partial<ProspectResult>): ProspectResult {
  return {
    id: "r1",
    search_id: "s1",
    rank: 1,
    place_id: "ChIJ-a",
    name: "Dachdecker Ohne",
    address: "Hiltruper Str. 210, 48165 Münster, Deutschland",
    street: "Hiltruper Str. 210",
    postal_code: "48165",
    city: "Münster",
    phone: "0251 1",
    website: null,
    maps_url: "https://maps.google.com/?cid=1",
    rating: 4.1,
    review_count: 3,
    photo_count: 0,
    has_opening_hours: false,
    score: 7,
    signals: ["no_website"],
    reasons: "Keine Webseite im Profil und keine Fotos.",
    contact_ways: WAYS,
    crm_customer_id: null,
    objected: false,
    ...changes,
  };
}

let root: HTMLDivElement;

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function mount(url: string) {
  window.history.replaceState(null, "", url);
  await act(() => {
    render(
      <AdminContext.Provider value={true}>
        <LocationProvider>
          <Kundensuche />
        </LocationProvider>
      </AdminContext.Provider>,
      root,
    );
  });
  await flush();
}

async function fill(label: string, value: string) {
  const field = [...root.querySelectorAll<HTMLLabelElement>("label.field")].find((item) => item.textContent.startsWith(label));
  const input = field?.querySelector("input");
  if (input === null || input === undefined) throw new Error(`no field ${label}`);
  await act(() => {
    input.value = value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function button(text: string, scope: ParentNode = root): HTMLButtonElement {
  const found = [...scope.querySelectorAll("button")].find((item) => item.textContent === text);
  if (found === undefined) throw new Error(`no button ${text}`);
  return found;
}

async function click(target: HTMLButtonElement) {
  await act(() => {
    target.click();
  });
  await flush();
}

beforeEach(() => {
  requestProspectSearch.mockReset();
  addProspectToCrm.mockReset();
  startFieldAudit.mockReset();
  view = undefined;
  localStorage.clear();
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  root.remove();
  vi.restoreAllMocks();
});

describe("Kunden finden, the form", () => {
  it("names missing trade and city and asks nothing", async () => {
    await mount("/kunden");
    await act(() => {
      root.querySelector("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(root.textContent).toContain("Bitte ein Gewerk eingeben, zum Beispiel Dachdecker.");
    expect(requestProspectSearch).not.toHaveBeenCalled();
  });

  it("asks for the trade in the city and opens the search", async () => {
    requestProspectSearch.mockResolvedValue("s1");
    await mount("/kunden");
    await fill("Gewerk", " Dachdecker ");
    await fill("Stadt", "Münster");
    await act(() => {
      root.querySelector("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await flush();
    expect(requestProspectSearch).toHaveBeenCalledWith("Dachdecker", "Münster");
    expect(window.location.search).toBe("?suche=s1");
  });
});

describe("Kunden finden, the results", () => {
  it("shows each business with its reason and only lawful contact ways", async () => {
    view = { search: SEARCH, results: [result({})], cacheHours: 24 };
    await mount("/kunden?suche=s1");
    const card = root.querySelector(".prospect");
    if (card === null) throw new Error("no result");
    expect(card.textContent).toContain("Keine Webseite im Profil und keine Fotos.");
    expect(card.textContent).toContain("Brief");
    expect(card.textContent).toContain("Anruf");
    expect(card.textContent).not.toContain("E-Mail");
    expect(card.querySelector('a[href^="mailto:"], a[href^="tel:"]')).toBeNull();
    expect(root.textContent).toContain("kostet dieselbe Suche nichts");
  });

  it("takes a business into the CRM with what Daniel copies from the public profile", async () => {
    view = { search: SEARCH, results: [result({})], cacheHours: 24 };
    addProspectToCrm.mockResolvedValue("crm-7");
    await mount("/kunden?suche=s1");
    await click(button("Ins CRM als Interessent"));
    const form = root.querySelector<HTMLFormElement>(".prospect form");
    if (form === null) throw new Error("no CRM form");
    expect(form.querySelector('a[href="https://maps.google.com/?cid=1"]')).not.toBeNull();
    const nameField = [...form.querySelectorAll<HTMLLabelElement>("label.field")].find((item) => item.textContent.startsWith("Name"));
    expect(nameField?.querySelector("input")?.value).toBe("");
    await fill("Name", "Dachdecker Ohne");
    await fill("Telefon", "0251 1");
    await act(() => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await flush();
    expect(addProspectToCrm).toHaveBeenCalledWith("r1", {
      name: "Dachdecker Ohne",
      street: "",
      postalCode: "",
      city: "Münster",
      phone: "0251 1",
    });
    expect(root.textContent).toContain("Im CRM als Interessent");
    expect(button("Kundenakte im CRM öffnen")).toBeTruthy();
  });

  it("refuses an empty name before asking the server", async () => {
    view = { search: SEARCH, results: [result({})], cacheHours: 24 };
    await mount("/kunden?suche=s1");
    await click(button("Ins CRM als Interessent"));
    await act(() => {
      root.querySelector(".prospect form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(root.textContent).toContain("Bitte den Namen des Betriebs aus dem Google-Profil eintragen.");
    expect(addProspectToCrm).not.toHaveBeenCalled();
  });

  it("marks a business that objected and offers neither contact nor buttons", async () => {
    view = { search: SEARCH, results: [result({ objected: true })], cacheHours: 24 };
    await mount("/kunden?suche=s1");
    const card = root.querySelector(".prospect");
    if (card === null) throw new Error("no result");
    expect(card.textContent).toContain("Hat Werbung widersprochen – nicht ansprechen.");
    expect(card.textContent).not.toContain("Brief");
    expect(card.querySelectorAll("button")).toHaveLength(0);
  });

  it("says plainly when the profile gives no way of contact", async () => {
    view = { search: SEARCH, results: [result({ contact_ways: [WAYS[2]] })], cacheHours: 24 };
    await mount("/kunden?suche=s1");
    expect(root.textContent).toContain(
      "Das Google-Profil nennt weder Anschrift noch Telefonnummer, deshalb zeigt die App keinen Kontaktweg.",
    );
  });

  it("starts an audit only from a website", async () => {
    view = {
      search: SEARCH,
      results: [result({}), result({ id: "r2", rank: 2, name: "Mit Seite", website: "https://mit-seite.de/" })],
      cacheHours: 24,
    };
    startFieldAudit.mockResolvedValue("audit-3");
    await mount("/kunden?suche=s1");
    const [without, withSite] = [...root.querySelectorAll<HTMLElement>(".prospect")];
    if (without === undefined || withSite === undefined) throw new Error("two results expected");
    expect(button("Audit starten", without).disabled).toBe(true);
    expect(without.textContent).toContain("Ohne Webseite startet keine Prüfung.");
    await click(button("Audit starten", withSite));
    expect(startFieldAudit).toHaveBeenCalledWith({
      p_website: "https://mit-seite.de/",
      p_disabled_checks: ["m1", "m2", "m3", "m4"],
    });
    expect(window.location.search).toBe("?pruefung=audit-3");
  });
});
