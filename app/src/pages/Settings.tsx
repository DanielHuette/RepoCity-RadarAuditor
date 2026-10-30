import { useState } from "preact/hooks";
import { sourceUsageSince } from "../api/queries";
import { Loaded, PageHead, Toggle } from "../components/ui";
import { isStandalone, useInstallPrompt } from "../hooks/install";
import { useIsAdmin } from "../hooks/role";
import { useSetting, type SaveStatus } from "../hooks/setting";
import { useAsync } from "../hooks/useAsync";
import {
  SOURCES_KEY,
  SOURCE_IDS,
  SOURCE_INFO,
  costCapRequired,
  monthStart,
  monthUsage,
  parseCostCap,
  parseDailyCap,
  parseSources,
  withSource,
  type LimitInput,
  type SourceId,
  type SourceSettings,
} from "../lib/sources";

function SaveNote({ status, error }: { status: SaveStatus; error: string | null }) {
  if (status === "failed") {
    return (
      <p class="field__error" role="alert">
        Nicht gespeichert: {error}
      </p>
    );
  }
  return (
    <p class="muted small" role="status">
      {status === "saving" ? "Wird gespeichert …" : status === "saved" ? "Gespeichert." : ""}
    </p>
  );
}

interface LimitFieldProps {
  label: string;
  unit: string;
  value: number | undefined;
  parse: (input: string) => LimitInput;
  disabled: boolean;
  onSave: (value: number | undefined) => void;
}

function formatLimit(value: number | undefined): string {
  return value === undefined ? "" : String(value).replace(".", ",");
}

/** Saved when the field loses focus, so typing "120" does not write 1, 12 and 120. */
function useLimitText(value: number | undefined, parse: (input: string) => LimitInput, onSave: (value: number | undefined) => void) {
  const [text, setText] = useState(formatLimit(value));
  const [error, setError] = useState<string | null>(null);
  const commit = (input: string) => {
    const parsed = parse(input);
    if ("error" in parsed) {
      setError(parsed.error);
      return;
    }
    setError(null);
    setText(formatLimit(parsed.value));
    if (parsed.value !== value) onSave(parsed.value);
  };
  return { text, setText, error, commit };
}

function LimitField({ label, unit, value, parse, disabled, onSave }: LimitFieldProps) {
  const { text, setText, error, commit } = useLimitText(value, parse, onSave);
  return (
    <label class="field limit">
      <span class="field__label">
        {label} <span class="muted">({unit})</span>
      </span>
      <input
        class="input num"
        type="text"
        inputMode="decimal"
        placeholder="ohne Limit"
        value={text}
        disabled={disabled}
        aria-invalid={error !== null}
        onInput={(event) => {
          setText(event.currentTarget.value);
        }}
        onChange={(event) => {
          commit(event.currentTarget.value);
        }}
      />
      {error !== null && <span class="field__error">{error}</span>}
    </label>
  );
}

interface SourceCardProps {
  id: SourceId;
  settings: SourceSettings;
  usage: number | undefined;
  readOnly: boolean;
  onChange: (next: SourceSettings) => void;
}

function CostLimit({ id, settings, readOnly, onChange }: Omit<SourceCardProps, "usage">) {
  const cap = settings[id].monthly_cost_cap_eur;
  return (
    <LimitField
      key={`cost-${String(cap)}`}
      label="Monatslimit"
      unit="Euro"
      value={cap}
      parse={(input) => parseCostCap(input, costCapRequired(id))}
      disabled={readOnly}
      onSave={(monthly_cost_cap_eur) => {
        onChange(withSource(settings, id, { monthly_cost_cap_eur }));
      }}
    />
  );
}

function SourceLimits({ id, settings, readOnly, onChange }: Omit<SourceCardProps, "usage">) {
  const setting = settings[id];
  return (
    <div class="source__limits">
      <LimitField
        key={`daily-${String(setting.daily_cap)}`}
        label="Tageslimit"
        unit="Abfragen"
        value={setting.daily_cap}
        parse={parseDailyCap}
        disabled={readOnly}
        onSave={(daily_cap) => {
          onChange(withSource(settings, id, { daily_cap }));
        }}
      />
      {SOURCE_INFO[id].paid && <CostLimit id={id} settings={settings} readOnly={readOnly} onChange={onChange} />}
    </div>
  );
}

function SourceCard({ id, settings, usage, readOnly, onChange }: SourceCardProps) {
  const info = SOURCE_INFO[id];
  return (
    <li class="source">
      <Toggle
        label={info.label}
        checked={settings[id].enabled}
        disabled={readOnly}
        onChange={(enabled) => {
          onChange(withSource(settings, id, { enabled }));
        }}
      />
      <p class="muted small">{info.explanation}</p>
      {usage !== undefined && (
        <p class="small num">
          Diesen Monat: <strong>{usage.toLocaleString("de-DE")}</strong> {usage === 1 ? "Abfrage" : "Abfragen"}
        </p>
      )}
      <SourceLimits id={id} settings={settings} readOnly={readOnly} onChange={onChange} />
    </li>
  );
}

function useMonthUsage() {
  return useAsync(async () => {
    const rows = await sourceUsageSince(monthStart(new Date()));
    return rows === null ? null : monthUsage(rows, monthStart(new Date()));
  }, []);
}

function SourcesPanel({ readOnly }: { readOnly: boolean }) {
  const setting = useSetting(SOURCES_KEY, parseSources);
  const usage = useMonthUsage().data;
  const usageOf = (id: SourceId) => (usage === undefined || usage === null ? undefined : (usage[id] ?? 0));
  return (
    <section class="panel stack" aria-labelledby="sources">
      <div>
        <h2 id="sources">Datenquellen</h2>
        <p class="muted small">
          Was der Server abfragen darf. Ist ein Limit erreicht, gilt die betroffene Prüfung als nicht messbar, bis der Tag
          oder Monat vorbei ist.
        </p>
      </div>
      <Loaded state={setting.state} label="Datenquellen laden …">
        {(settings) => (
          <div class="stack stack--tight">
            <ul class="sources">
              {SOURCE_IDS.map((id) => (
                <SourceCard key={id} id={id} settings={settings} usage={usageOf(id)} readOnly={readOnly} onChange={setting.save} />
              ))}
            </ul>
            <SaveNote status={setting.status} error={setting.error} />
          </div>
        )}
      </Loaded>
    </section>
  );
}

function InstallPanel() {
  const { available, install } = useInstallPrompt();
  const [declined, setDeclined] = useState(false);

  if (isStandalone()) return <p class="muted">Die App ist auf diesem Gerät installiert.</p>;
  if (available) {
    return (
      <div class="stack stack--tight">
        <button
          type="button"
          class="btn btn--primary btn--main btn--long"
          onClick={() =>
            void install().then((accepted) => {
              setDeclined(!accepted);
            })
          }
        >
          Auf diesem Gerät installieren
        </button>
        {declined && <p class="muted small">Abgebrochen. Du kannst es jederzeit noch mal versuchen.</p>}
      </div>
    );
  }
  return (
    <p class="muted">
      Auf Android in Chrome im Menü „Zum Startbildschirm“ wählen, auf dem iPhone oder iPad in Safari auf Teilen tippen, dann
      „Zum Home-Bildschirm“. Am PC bietet der Browser die Installation in seinem Menü an.
    </p>
  );
}

export function Settings() {
  const readOnly = !useIsAdmin();
  return (
    <>
      <PageHead title="Einstellungen" sub="Gilt für jede neue Prüfung auf dem Server." />
      <div class="grid-2">
        <SourcesPanel readOnly={readOnly} />
        <section class="panel stack settings-install" aria-labelledby="install">
          <h2 id="install">App installieren</h2>
          <InstallPanel />
        </section>
      </div>
    </>
  );
}
