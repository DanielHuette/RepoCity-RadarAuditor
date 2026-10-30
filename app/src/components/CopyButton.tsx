import { useState } from "preact/hooks";
import { copyText } from "../lib/clipboard";

const COPIED_MS = 2000;

/** `main` makes it the gold, pulsing main action of the page. */
export function CopyButton({ text, label = "Kopieren", main = false }: { text: string; label?: string; main?: boolean }) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");

  const copy = async () => {
    try {
      await copyText(text);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
    window.setTimeout(() => {
      setCopyState("idle");
    }, COPIED_MS);
  };

  return (
    <div class="cluster">
      <button type="button" class={main ? "btn btn--primary btn--main btn--long" : "btn btn--small"} onClick={() => void copy()}>
        {copyState === "copied" ? "Kopiert" : label}
      </button>
      {copyState === "failed" && (
        <span class="field__error" role="alert">
          Kopieren nicht möglich. Markiere den Text und kopiere ihn von Hand.
        </span>
      )}
    </div>
  );
}
