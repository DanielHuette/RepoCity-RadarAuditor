import { useEffect, useState } from "preact/hooks";

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

let captured: InstallPromptEvent | null = null;
const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of listeners) listener();
}

// The browser fires this once, often before the settings page mounts, so it is caught at start-up.
export function captureInstallPrompt(): void {
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    captured = event as InstallPromptEvent;
    notify();
  });
  window.addEventListener("appinstalled", () => {
    captured = null;
    notify();
  });
}

export function useInstallPrompt(): { available: boolean; install: () => Promise<boolean> } {
  const [available, setAvailable] = useState(captured !== null);
  useEffect(() => {
    const listener = () => {
      setAvailable(captured !== null);
    };
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);
  const install = async () => {
    if (captured === null) return false;
    const event = captured;
    await event.prompt();
    const choice = await event.userChoice;
    captured = null;
    notify();
    return choice.outcome === "accepted";
  };
  return { available, install };
}

export function isStandalone(): boolean {
  return window.matchMedia("(display-mode: standalone)").matches;
}
