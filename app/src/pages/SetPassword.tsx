import { useState } from "preact/hooks";
import { supabase } from "../api/supabase";
import { Logo } from "../components/Logo";
import { ErrorAlert, Field } from "../components/ui";
import { useAuthRequest } from "../hooks/authRequest";

const MIN_LENGTH = 10;

function passwordProblem(password: string, repeat: string): string | null {
  if (password.length < MIN_LENGTH) return `Das Passwort braucht mindestens ${String(MIN_LENGTH)} Zeichen.`;
  return password === repeat ? null : "Die beiden Eingaben stimmen nicht überein.";
}

function describeSaveError(message: string): string {
  return `Das Passwort ließ sich nicht speichern. (${message})`;
}

function useNewPassword(onDone: () => void) {
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const request = useAuthRequest(describeSaveError);
  const submit = async (event: Event) => {
    event.preventDefault();
    const problem = passwordProblem(password, repeat);
    if (problem !== null) {
      request.setError(problem);
      return;
    }
    if (await request.send(() => supabase.auth.updateUser({ password }))) onDone();
  };
  return { password, setPassword, repeat, setRepeat, request, submit };
}

export function SetPassword({ onDone }: { onDone: () => void }) {
  const { password, setPassword, repeat, setRepeat, request, submit } = useNewPassword(onDone);

  return (
    <main class="signin">
      <div class="signin__card">
        <Logo size={56} />
        <form class="panel form" onSubmit={(event) => void submit(event)}>
          <div>
            <h1>Neues Passwort</h1>
            <p class="muted">Lege dein Passwort fest. Es gilt für RadarAuditor und das Agentic-Marketing-Cockpit.</p>
          </div>
          <Field label="Neues Passwort" type="password" autoComplete="new-password" value={password} onInput={setPassword} required />
          <Field label="Passwort wiederholen" type="password" autoComplete="new-password" value={repeat} onInput={setRepeat} required />
          <ErrorAlert message={request.error} />
          <button type="submit" class="btn btn--primary btn--main btn--block" disabled={request.busy}>
            {request.busy ? "Wird gespeichert …" : "Passwort speichern"}
          </button>
        </form>
      </div>
    </main>
  );
}
