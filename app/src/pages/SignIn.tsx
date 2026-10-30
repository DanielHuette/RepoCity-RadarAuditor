import { useState } from "preact/hooks";
import { supabase } from "../api/supabase";
import { Logo } from "../components/Logo";
import { ErrorAlert, Field } from "../components/ui";
import { useAuthRequest, type AuthRequest } from "../hooks/authRequest";

function describeSignInError(message: string): string {
  if (/invalid login credentials/i.test(message)) return "E-Mail oder Passwort stimmt nicht.";
  if (/email not confirmed/i.test(message)) return "Die E-Mail-Adresse ist noch nicht bestätigt.";
  if (/rate limit/i.test(message)) return "Zu viele Versuche. Warte ein paar Minuten und versuch es noch mal.";
  if (/fetch|network/i.test(message)) return "Keine Verbindung zum Server. Prüfe dein Netz.";
  return `Das hat nicht geklappt. (${message})`;
}

function SwitchButton({ label, reset, onShowReset }: { label: string; reset: boolean; onShowReset: (reset: boolean) => void }) {
  return (
    <button
      type="button"
      class="btn btn--ghost btn--block"
      onClick={() => {
        onShowReset(reset);
      }}
    >
      {label}
    </button>
  );
}

interface PasswordFormProps {
  email: string;
  setEmail: (value: string) => void;
  password: string;
  setPassword: (value: string) => void;
  request: AuthRequest;
  onShowReset: (reset: boolean) => void;
}

function PasswordForm({ email, setEmail, password, setPassword, request, onShowReset }: PasswordFormProps) {
  const signIn = (event: Event) => {
    event.preventDefault();
    void request.send(() => supabase.auth.signInWithPassword({ email: email.trim(), password }));
  };
  return (
    <form class="panel form" onSubmit={signIn}>
      <div>
        <h1>Anmelden</h1>
        <p class="muted">RadarAuditor – Sichtbarkeit, Berichte, Angebote.</p>
      </div>
      <Field label="E-Mail" type="email" autoComplete="username" inputMode="email" value={email} onInput={setEmail} required />
      <Field label="Passwort" type="password" autoComplete="current-password" value={password} onInput={setPassword} required />
      <ErrorAlert message={request.error} />
      <button type="submit" class="btn btn--primary btn--main btn--block" disabled={request.busy}>
        {request.busy ? "Wird geprüft …" : "Anmelden"}
      </button>
      <SwitchButton label="Passwort vergessen?" reset onShowReset={onShowReset} />
    </form>
  );
}

interface ResetFormProps {
  email: string;
  setEmail: (value: string) => void;
  sent: boolean;
  request: AuthRequest;
  onSent: (sent: boolean) => void;
  onShowReset: (reset: boolean) => void;
}

function ResetForm({ email, setEmail, sent, request, onSent, onShowReset }: ResetFormProps) {
  const requestReset = async (event: Event) => {
    event.preventDefault();
    const sendLink = () => supabase.auth.resetPasswordForEmail(email.trim(), { redirectTo: `${window.location.origin}/` });
    if (await request.send(sendLink)) onSent(true);
  };
  return (
    <form class="panel form" onSubmit={(event) => void requestReset(event)}>
      <div>
        <h1>Passwort vergessen</h1>
        <p class="muted">Du bekommst eine E-Mail mit einem Link, über den du ein neues Passwort festlegst.</p>
      </div>
      <Field label="E-Mail" type="email" autoComplete="username" inputMode="email" value={email} onInput={setEmail} required />
      {sent && (
        <p class="muted" role="status">
          Wenn zu dieser Adresse ein Konto gehört, ist die E-Mail unterwegs. Schau auch im Spam-Ordner nach.
        </p>
      )}
      <ErrorAlert message={request.error} />
      <button type="submit" class="btn btn--primary btn--main btn--block" disabled={request.busy || sent}>
        {request.busy ? "Wird gesendet …" : "Link senden"}
      </button>
      <SwitchButton label="Zurück zur Anmeldung" reset={false} onShowReset={onShowReset} />
    </form>
  );
}

function useSignIn() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [forgot, setForgot] = useState(false);
  const [sent, setSent] = useState(false);
  const request = useAuthRequest(describeSignInError);
  const showReset = (reset: boolean) => {
    setForgot(reset);
    if (!reset) setSent(false);
    request.setError(null);
  };
  return { email, setEmail, password, setPassword, forgot, sent, setSent, request, showReset };
}

export function SignIn() {
  const { email, setEmail, password, setPassword, forgot, sent, setSent, request, showReset } = useSignIn();
  return (
    <main class="signin">
      <div class="signin__card">
        <Logo size={56} />
        {forgot ? (
          <ResetForm email={email} setEmail={setEmail} sent={sent} request={request} onSent={setSent} onShowReset={showReset} />
        ) : (
          <PasswordForm
            email={email}
            setEmail={setEmail}
            password={password}
            setPassword={setPassword}
            request={request}
            onShowReset={showReset}
          />
        )}
      </div>
    </main>
  );
}
