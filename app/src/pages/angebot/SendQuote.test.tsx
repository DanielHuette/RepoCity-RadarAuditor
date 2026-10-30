// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadCrmCustomerEmail, saveCrmCustomerEmail, sendQuote } from "../../api/offer";
import type { CrmQuote } from "../../api/types";
import type { PdfState } from "../../lib/offerDocs";
import { SendQuote } from "./SendQuote";

vi.mock("../../api/offer", () => ({
  loadCrmCustomerEmail: vi.fn(),
  saveCrmCustomerEmail: vi.fn(),
  sendQuote: vi.fn(),
}));

const QUOTE = { id: "q-1", number: "AN-2026-0004", customer_id: "crm-7", status: "draft" } as CrmQuote;

let root: HTMLDivElement;
let requestPdf: ReturnType<typeof vi.fn<() => Promise<void>>>;
let sent: ReturnType<typeof vi.fn<() => void>>;

beforeEach(() => {
  root = document.createElement("div");
  document.body.append(root);
  requestPdf = vi.fn(() => Promise.resolve());
  sent = vi.fn();
  vi.mocked(loadCrmCustomerEmail).mockResolvedValue("erika@muster.de");
  vi.mocked(saveCrmCustomerEmail).mockResolvedValue();
  vi.mocked(sendQuote).mockResolvedValue();
});

afterEach(() => {
  render(null, root);
  root.remove();
  vi.clearAllMocks();
  vi.useRealTimers();
});

async function mount(pdf: PdfState["kind"]) {
  await act(async () => {
    render(<SendQuote quote={QUOTE} pdf={pdf} onRequestPdf={requestPdf} onSent={sent} />, root);
    await Promise.resolve();
  });
}

function button(text: string): HTMLButtonElement {
  const found = [...root.querySelectorAll("button")].find((item) => item.textContent === text);
  if (found === undefined) throw new Error(`no button ${text}`);
  return found;
}

async function settle() {
  if (vi.isFakeTimers()) await vi.advanceTimersByTimeAsync(0);
  else await new Promise((resolve) => setTimeout(resolve, 0));
}

async function click(text: string) {
  await act(async () => {
    button(text).click();
    await settle();
  });
}

async function startWaiting() {
  await mount("none");
  await click("Angebot abschicken");
  await click("Jetzt abschicken");
}

async function type(value: string) {
  const input = root.querySelector("input");
  if (input === null) throw new Error("no input");
  await act(() => {
    input.value = value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("SendQuote", () => {
  it("names the recipient and sends a quote whose PDF is ready", async () => {
    await mount("ready");
    await click("Angebot abschicken");
    expect(loadCrmCustomerEmail).toHaveBeenCalledWith("crm-7");
    expect(root.textContent).toContain("erika@muster.de");
    await click("Jetzt abschicken");
    expect(sendQuote).toHaveBeenCalledWith("q-1");
    expect(requestPdf).not.toHaveBeenCalled();
    expect(sent).toHaveBeenCalledOnce();
    expect(root.textContent).not.toContain("Jetzt abschicken");
  });

  it("asks for a missing address and stores it in the CRM before sending", async () => {
    vi.mocked(loadCrmCustomerEmail).mockResolvedValue(null);
    await mount("ready");
    await click("Angebot abschicken");
    await type("erika");
    await click("Jetzt abschicken");
    expect(root.textContent).toContain("Bitte eine gültige E-Mail-Adresse eingeben.");
    expect(saveCrmCustomerEmail).not.toHaveBeenCalled();
    await type(" erika@muster.de ");
    await click("Jetzt abschicken");
    expect(saveCrmCustomerEmail).toHaveBeenCalledWith("crm-7", "erika@muster.de");
    expect(sendQuote).toHaveBeenCalledWith("q-1");
  });

  it("requests the PDF first and sends once it is ready", async () => {
    await mount("none");
    await click("Angebot abschicken");
    await click("Jetzt abschicken");
    expect(requestPdf).toHaveBeenCalledOnce();
    expect(sendQuote).not.toHaveBeenCalled();
    expect(root.textContent).toContain("Lass die Seite so lange offen");
    await mount("pending");
    expect(sendQuote).not.toHaveBeenCalled();
    await mount("ready");
    expect(sendQuote).toHaveBeenCalledWith("q-1");
    expect(sent).toHaveBeenCalledOnce();
  });

  it("does not send when the PDF cannot be made", async () => {
    await mount("pending");
    await click("Angebot abschicken");
    await click("Jetzt abschicken");
    expect(requestPdf).not.toHaveBeenCalled();
    await mount("failed");
    expect(sendQuote).not.toHaveBeenCalled();
    expect(root.textContent).toContain("Ohne PDF geht das Angebot nicht raus");
  });

  it("shows the reason when the CRM refuses to send", async () => {
    vi.mocked(sendQuote).mockRejectedValue(new Error("boom"));
    await mount("ready");
    await click("Angebot abschicken");
    await click("Jetzt abschicken");
    expect(root.querySelector("[role=alert]")?.textContent).toContain("Das hat nicht geklappt.");
    expect(sent).not.toHaveBeenCalled();
  });

  it("can be cancelled without sending", async () => {
    await mount("ready");
    await click("Angebot abschicken");
    await click("Abbrechen");
    expect(root.textContent).not.toContain("erika@muster.de");
    expect(sendQuote).not.toHaveBeenCalled();
  });

  it("can stop waiting for the PDF, and then sends nothing", async () => {
    await startWaiting();
    expect(root.textContent).toContain("Verlässt du die Seite, wird nichts verschickt.");
    await click("Abbrechen");
    expect(root.textContent).not.toContain("Lass die Seite so lange offen");
    await mount("ready");
    expect(sendQuote).not.toHaveBeenCalled();
  });

  it("gives up after five minutes without a PDF", async () => {
    vi.useFakeTimers();
    await startWaiting();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * 60 * 1000);
    });
    expect(root.textContent).toContain("Das PDF ist nach fünf Minuten nicht fertig. Das Angebot ist nicht verschickt.");
    await mount("ready");
    expect(sendQuote).not.toHaveBeenCalled();
  });

  it("sends nothing after the page was left", async () => {
    await startWaiting();
    await act(() => {
      render(null, root);
    });
    await mount("ready");
    expect(sendQuote).not.toHaveBeenCalled();
  });

  it("shows a typed address in lower case", async () => {
    vi.mocked(loadCrmCustomerEmail).mockResolvedValue(null);
    await mount("pending");
    await click("Angebot abschicken");
    await type("Erika@Muster.DE");
    await click("Jetzt abschicken");
    expect(saveCrmCustomerEmail).toHaveBeenCalledWith("crm-7", "erika@muster.de");
  });
});
