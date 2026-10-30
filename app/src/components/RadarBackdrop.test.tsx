// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { describe, expect, it, vi } from "vitest";
import { RadarBackdrop, webglAvailable } from "./RadarBackdrop";

describe("RadarBackdrop", () => {
  it("renders nothing and throws nothing where WebGL is missing", async () => {
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const root = document.createElement("div");
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    await act(() => {
      render(<RadarBackdrop />, root);
    });
    expect(webglAvailable()).toBe(false);
    expect(root.innerHTML).toBe("");
    expect(errors).not.toHaveBeenCalled();
    render(null, root);
    getContext.mockRestore();
    errors.mockRestore();
  });

  it("treats a throwing canvas as no WebGL", () => {
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => {
      throw new Error("no canvas");
    });
    expect(webglAvailable()).toBe(false);
    getContext.mockRestore();
  });

  it("gives the probe context back after checking for WebGL", () => {
    const loseContext = vi.fn();
    const context = { getExtension: vi.fn(() => ({ loseContext })) };
    const getContext = vi
      .spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue(context as unknown as WebGL2RenderingContext);
    expect(webglAvailable()).toBe(true);
    expect(context.getExtension).toHaveBeenCalledWith("WEBGL_lose_context");
    expect(loseContext).toHaveBeenCalledOnce();
    getContext.mockRestore();
  });
});
