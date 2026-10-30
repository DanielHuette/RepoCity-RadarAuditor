// Prüfen: enter an address, choose the checks, start the audit and follow it live. Open requests
// from the website form sit closed below the button and only fill the address field.
import { useLocation } from "preact-iso";
import { useEffect, useId, useRef, useState } from "preact/hooks";
import { startFieldAudit } from "../api/queries";
import { AuditProgress } from "../components/AuditProgress";
import { CheckSelection } from "../components/CheckSelection";
import { Icon } from "../components/Icon";
import { OpenRequests } from "../components/OpenRequests";
import { RadarBackdrop } from "../components/RadarBackdrop";
import { useServerStatus } from "../hooks/serverStatus";
import { useAction } from "../hooks/useAsync";
import { CATALOGUE_AREAS } from "../lib/catalogue";
import { deviceStorage, loadSelection, saveSelection } from "../lib/checkSelection";
import { quickAuditRequest, validateWebsite } from "../lib/fieldAudit";
import { hostOf } from "../lib/format";

interface UrlInputProps {
  value: string;
  errorId: string | null;
  onInput: (value: string) => void;
}

function UrlInput({ value, errorId, onInput }: UrlInputProps) {
  return (
    <label class="url-field">
      <span class="url-field__icon" aria-hidden="true">
        <Icon name="link" />
      </span>
      <input
        class="url-field__input"
        type="text"
        inputMode="url"
        autoCapitalize="off"
        autoComplete="url"
        spellcheck={false}
        placeholder="meisterbetrieb-muster.de"
        aria-label="Website des Betriebs"
        aria-invalid={errorId !== null}
        aria-describedby={errorId ?? undefined}
        value={value}
        onInput={(event) => {
          onInput(event.currentTarget.value);
        }}
      />
    </label>
  );
}

interface AddressStepProps {
  initial: string;
  focusGo: boolean;
  onNext: (website: string, typed: string) => void;
}

function AddressStep({ initial, focusGo, onNext }: AddressStepProps) {
  const [text, setText] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const errorId = useId();
  const go = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (focusGo) go.current?.focus();
  }, [focusGo]);
  const submit = (event: SubmitEvent) => {
    event.preventDefault();
    const result = validateWebsite(text);
    setError(result.ok ? null : result.error);
    if (result.ok) onNext(result.website, text);
  };
  return (
    <form class="start" noValidate onSubmit={submit}>
      <UrlInput value={text} errorId={error === null ? null : errorId} onInput={setText} />
      {error !== null && (
        <p id={errorId} class="start__error field__error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" class="btn btn--primary btn--main start__go" ref={go}>
        Prüfen
      </button>
    </form>
  );
}

/** The selection of the last audit on this device, saved again on every change. */
function useStoredSelection(): [string[], (next: string[]) => void] {
  const [disabled, setDisabled] = useState<string[]>(() => loadSelection(deviceStorage()));
  const change = (next: string[]) => {
    setDisabled(next);
    saveSelection(deviceStorage(), next);
  };
  return [disabled, change];
}

function StartNotes({ blockedReason, error }: { blockedReason: string | null; error: string | null }) {
  return (
    <>
      {blockedReason !== null && (
        <p class="notice notice--bad" role="status">
          Prüfungen gerade nicht möglich: {blockedReason}
        </p>
      )}
      {error !== null && (
        <p class="field__error" role="alert">
          {error}
        </p>
      )}
    </>
  );
}

function SelectStep({ website, onBack, onStarted }: { website: string; onBack: () => void; onStarted: (auditId: string) => void }) {
  const [disabled, setDisabled] = useStoredSelection();
  const action = useAction();
  const blockedReason = useServerStatus()?.blockedReason ?? null;
  const host = hostOf(website).replace(/^www\./, "");
  const submit = (event: SubmitEvent) => {
    event.preventDefault();
    if (blockedReason !== null) return;
    void action.run(async () => {
      onStarted(await startFieldAudit(quickAuditRequest(website, disabled)));
    });
  };
  return (
    <form class="choose" noValidate onSubmit={submit}>
      <button type="button" class="address-label" aria-label={`Adresse ${host} ändern`} onClick={onBack}>
        {host}
      </button>
      <CheckSelection areas={CATALOGUE_AREAS} disabled={disabled} onChange={setDisabled} />
      <StartNotes blockedReason={blockedReason} error={action.error} />
      <div class="choose__go">
        <button type="submit" class="btn btn--primary btn--main btn--long" disabled={action.busy || blockedReason !== null}>
          {action.busy ? "Wird gestartet …" : "Prüfung starten"}
        </button>
      </div>
    </form>
  );
}

function ProgressStep({ auditId, onNew }: { auditId: string; onNew: () => void }) {
  return (
    <div class="progress-page">
      <AuditProgress auditId={auditId} onNew={onNew} />
    </div>
  );
}

/** The typed address; picking an open request replaces it and starts the address step afresh. */
function useTypedAddress() {
  const [typed, setTyped] = useState("");
  const [picked, setPicked] = useState(0);
  const pick = (next: string) => {
    setTyped(next);
    setPicked((count) => count + 1);
  };
  return { typed, setTyped, picked, pick };
}

interface StartStepProps {
  address: ReturnType<typeof useTypedAddress>;
  onNext: (website: string, typed: string) => void;
}

function StartStep({ address, onNext }: StartStepProps) {
  return (
    <>
      <AddressStep key={address.picked} initial={address.typed} focusGo={address.picked > 0} onNext={onNext} />
      <div class="requests-layer">
        <OpenRequests onPick={address.pick} />
      </div>
    </>
  );
}

/** One radar for all steps, so it keeps sweeping from the address to the running audit. */
export function Pruefen() {
  const { query, route } = useLocation();
  const auditId = query.pruefung ?? null;
  const address = useTypedAddress();
  const [website, setWebsite] = useState<string | null>(null);
  const showAudit = (id: string | null) => {
    setWebsite(null);
    route(id === null ? "/" : `/?pruefung=${id}`);
  };
  const enterAddress = (next: string, text: string) => {
    address.setTyped(text);
    setWebsite(next);
  };
  const backToAddress = () => {
    setWebsite(null);
  };

  let step;
  if (auditId !== null) step = <ProgressStep auditId={auditId} onNew={() => { showAudit(null); }} />;
  else if (website === null) step = <StartStep address={address} onNext={enterAddress} />;
  else step = <SelectStep website={website} onBack={backToAddress} onStarted={showAudit} />;
  return (
    <>
      <RadarBackdrop faint={auditId === null && website !== null} />
      {step}
    </>
  );
}
