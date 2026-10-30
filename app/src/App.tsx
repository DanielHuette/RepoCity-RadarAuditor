// Router and shell: sign-in gate, bottom navigation on phones, side navigation on desktop.
import { useEffect } from "preact/hooks";
import { LocationProvider, Route, Router, lazy, useLocation } from "preact-iso";
import { Logo } from "./components/Logo";
import { Shell } from "./components/Shell";
import { usePasswordRecovery } from "./hooks/recovery";
import { AdminContext, useAdminStatus } from "./hooks/role";
import { useSession } from "./hooks/useSession";
import { NotFound } from "./pages/NotFound";
import { Pruefen } from "./pages/Pruefen";
import { SetPassword } from "./pages/SetPassword";
import { SignIn } from "./pages/SignIn";

const SIGN_IN_PATH = "/anmelden";

// Prüfen is the start page and ships in the main bundle; the rest loads on first visit,
// so phones fetch Chart.js only when an Auswertung is opened.
const Auswertung = lazy(() => import("./pages/Auswertung").then((module) => module.Auswertung));
const Angebot = lazy(() => import("./pages/Angebot").then((module) => module.Angebot));
const Settings = lazy(() => import("./pages/Settings").then((module) => module.Settings));

/** The former Dokumente tab; old bookmarks land on Prüfen. */
function ToStart() {
  const { route } = useLocation();
  useEffect(() => {
    route("/", true);
  }, [route]);
  return null;
}

function Splash() {
  return (
    <div class="splash" role="status" aria-label="App wird geladen">
      <Logo size={56} />
      <span class="spinner" aria-hidden="true" />
    </div>
  );
}

function SignedIn({ admin, email }: { admin: boolean | undefined; email: string }) {
  return (
    <AdminContext.Provider value={admin}>
      <Shell admin={admin} email={email}>
        <Router>
          <Route path="/" component={Pruefen} />
          <Route path="/auswertung" component={Auswertung} />
          <Route path="/auswertung/:auditId" component={Auswertung} />
          <Route path="/angebot/:auditId?" component={Angebot} />
          <Route path="/dokumente" component={ToStart} />
          <Route path="/einstellungen" component={Settings} />
          <Route default component={NotFound} />
        </Router>
      </Shell>
    </AdminContext.Provider>
  );
}

function Gate() {
  const session = useSession();
  const recovery = usePasswordRecovery();
  const admin = useAdminStatus(session?.user.id);
  const { path, route } = useLocation();
  const onSignIn = path === SIGN_IN_PATH;

  useEffect(() => {
    if (session === null && !onSignIn) route(SIGN_IN_PATH, true);
    if (session && onSignIn) route("/", true);
  }, [session, onSignIn, route]);

  if (session === undefined || (session === null && !onSignIn) || (session && onSignIn)) return <Splash />;
  if (session === null) return <SignIn />;
  if (recovery.pending) return <SetPassword onDone={recovery.finish} />;
  return <SignedIn admin={admin} email={session.user.email ?? ""} />;
}

export function App() {
  return (
    <LocationProvider>
      <Gate />
    </LocationProvider>
  );
}
