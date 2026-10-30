// Backdrop of the Prüfen tab: the radar of the website behind address field and button, still
// sweeping while an audit runs. Renders nothing without WebGL; three.js loads only here.
import type { RefObject } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

/** Whether this browser can draw WebGL at all; false where creating a context fails. */
export function webglAvailable(): boolean {
  try {
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("webgl2") ?? canvas.getContext("webgl");
    if (context === null) return false;
    // Give the probe context back at once; browsers allow only a few live WebGL contexts.
    context.getExtension("WEBGL_lose_context")?.loseContext();
    return true;
  } catch {
    return false;
  }
}

function prefersStill(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

/** Starts the radar in `host` once three.js has loaded; false when it cannot be drawn here. */
function useRadar(host: RefObject<HTMLDivElement>): boolean {
  const [usable, setUsable] = useState(true);
  useEffect(() => {
    if (!webglAvailable()) {
      setUsable(false);
      return;
    }
    let stop: (() => void) | null = null;
    let active = true;
    import("./radarScene").then(
      ({ startRadar }) => {
        const element = host.current;
        if (!active || element === null) return;
        try {
          stop = startRadar(element, { still: prefersStill() });
        } catch {
          setUsable(false);
        }
      },
      () => {
        if (active) setUsable(false);
      },
    );
    return () => {
      active = false;
      stop?.();
    };
  }, [host]);
  return usable;
}

/** `faint` while the checks are chosen, so the list in front stays calm. */
export function RadarBackdrop({ faint = false }: { faint?: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const usable = useRadar(host);
  if (!usable) return null;
  return <div ref={host} class="radar-backdrop" data-faint={faint ? "" : undefined} aria-hidden="true" />;
}
