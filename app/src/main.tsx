// Entry point: checks the build configuration, catches the install prompt and mounts the router.
import { render } from "preact";
import { missingConfig } from "./api/config";
import { Logo } from "./components/Logo";
import { captureInstallPrompt } from "./hooks/install";
import "./theme/tokens.css";
import "./theme/app.css";

const root = document.getElementById("app");
if (root === null) throw new Error("Element #app is missing in index.html");

captureInstallPrompt();

function ConfigMissing({ names }: { names: readonly string[] }) {
  return (
    <main class="signin">
      <div class="signin__card panel">
        <Logo size={48} />
        <h1>App nicht eingerichtet</h1>
        <p class="muted">
          Beim Bauen fehlte {names.join(", ")}. Trag die Werte in Cloudflare Pages unter Einstellungen,
          Umgebungsvariablen ein und starte den Build neu.
        </p>
      </div>
    </main>
  );
}

// The Supabase client throws on import without configuration, so the app loads only once it is known to work.
const missing = missingConfig();
if (missing.length > 0) {
  render(<ConfigMissing names={missing} />, root);
} else {
  void import("./App").then(({ App }) => {
    render(<App />, root);
  });
}
