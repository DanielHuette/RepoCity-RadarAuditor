// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useAuthRequest, type AuthRequest } from "./authRequest";

let root: HTMLDivElement;
let request: AuthRequest;

function Probe() {
  request = useAuthRequest((message) => `Fehler: ${message}`);
  return null;
}

beforeEach(async () => {
  root = document.createElement("div");
  await act(() => {
    render(<Probe />, root);
  });
});

afterEach(() => {
  render(null, root);
});

describe("useAuthRequest", () => {
  it("is busy while the request runs and reports success", async () => {
    let answer: (result: { error: null }) => void = () => undefined;
    let done: Promise<boolean> = Promise.resolve(false);
    await act(() => {
      done = request.send(() => new Promise((resolve) => (answer = resolve)));
    });
    expect(request.busy).toBe(true);
    await act(async () => {
      answer({ error: null });
      expect(await done).toBe(true);
    });
    expect(request.busy).toBe(false);
    expect(request.error).toBeNull();
  });

  it("describes a refusal and clears it on the next request", async () => {
    await act(async () => {
      expect(await request.send(() => Promise.resolve({ error: { message: "rate limit" } }))).toBe(false);
    });
    expect(request.error).toBe("Fehler: rate limit");
    let answer: (result: { error: null }) => void = () => undefined;
    await act(() => {
      void request.send(() => new Promise((resolve) => (answer = resolve)));
    });
    expect(request.error).toBeNull();
    await act(() => {
      answer({ error: null });
    });
  });

  it("takes a message set from outside, such as a check before sending", async () => {
    await act(() => {
      request.setError("Zu kurz.");
    });
    expect(request.error).toBe("Zu kurz.");
  });
});
