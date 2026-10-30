import type { ComponentChildren } from "preact";
import { useId } from "preact/hooks";

export function Loading({ label = "Lädt …" }: { label?: string }) {
  return (
    <div class="state" role="status">
      <span class="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function Empty({ children }: { children: ComponentChildren }) {
  return <div class="state">{children}</div>;
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div class="state state--error" role="alert">
      <span>{message}</span>
      {onRetry && (
        <button type="button" class="btn btn--small" onClick={onRetry}>
          Noch mal versuchen
        </button>
      )}
    </div>
  );
}

interface LoadedProps<T> {
  state: { data: T | undefined; error: string | null; loading: boolean; reload: () => void };
  children: (data: T) => ComponentChildren;
  label?: string;
}

export function Loaded<T>({ state, children, label }: LoadedProps<T>) {
  if (state.error !== null) return <ErrorNote message={state.error} onRetry={state.reload} />;
  if (state.data === undefined) return <Loading label={label} />;
  return <>{children(state.data)}</>;
}

export function PageHead({ title, sub, children }: { title: string; sub?: string; children?: ComponentChildren }) {
  return (
    <header class="page-head">
      <div>
        <h1>{title}</h1>
        {sub !== undefined && <p class="muted">{sub}</p>}
      </div>
      {children}
    </header>
  );
}

type InputMode = "text" | "decimal" | "numeric" | "email" | "url" | "tel" | "search";

interface FieldProps {
  label: string;
  value: string;
  onInput: (value: string) => void;
  // No "url": the browser would reject bare domains, which the app normalises itself.
  type?: "text" | "email" | "password" | "tel" | "date" | "search";
  inputMode?: InputMode | undefined;
  autoComplete?: string;
  autoCapitalize?: "off" | "none" | "sentences" | "words";
  spellcheck?: boolean;
  required?: boolean;
  hint?: string;
  error?: string;
  multiline?: boolean;
  placeholder?: string;
}

function describedBy(id: string, hint: string | undefined, error: string | undefined): string | undefined {
  const described = [hint !== undefined ? `${id}-hint` : "", error !== undefined ? `${id}-error` : ""].filter(Boolean).join(" ");
  return described === "" ? undefined : described;
}

function FieldNote({ id, kind, text }: { id: string; kind: "hint" | "error"; text: string | undefined }) {
  if (text === undefined) return null;
  return (
    <span id={`${id}-${kind}`} class={`field__${kind}`}>
      {text}
    </span>
  );
}

function FieldControl({ id, props }: { id: string; props: FieldProps }) {
  const { value, onInput, type = "text", hint, error } = props;
  const shared = {
    value,
    required: props.required,
    placeholder: props.placeholder,
    autoCapitalize: props.autoCapitalize,
    spellcheck: props.spellcheck,
    // The wrapping label also holds hint and error; naming the field by its label text alone keeps it short.
    "aria-labelledby": `${id}-label`,
    "aria-invalid": error !== undefined,
    "aria-describedby": describedBy(id, hint, error),
    onInput: (event: { currentTarget: HTMLInputElement | HTMLTextAreaElement }) => {
      onInput(event.currentTarget.value);
    },
  };
  if (props.multiline === true) return <textarea class="textarea" {...shared} />;
  return <input class="input" type={type} {...shared} inputMode={props.inputMode} autoComplete={props.autoComplete} />;
}

export function Field(props: FieldProps) {
  const id = useId();
  return (
    <label class="field">
      <span id={`${id}-label`} class="field__label">
        {props.label}
      </span>
      <FieldControl id={id} props={props} />
      <FieldNote id={id} kind="hint" text={props.hint} />
      <FieldNote id={id} kind="error" text={props.error} />
    </label>
  );
}

interface SelectProps {
  label: string;
  value: string;
  options: readonly { value: string; label: string }[];
  onChange: (value: string) => void;
  disabled?: boolean;
  error?: string | undefined;
}

export function Select({ label, value, options, onChange, disabled, error }: SelectProps) {
  const id = useId();
  return (
    <label class="field">
      <span id={`${id}-label`} class="field__label">
        {label}
      </span>
      <select
        class="select"
        aria-labelledby={`${id}-label`}
        value={value}
        disabled={disabled}
        aria-invalid={error !== undefined}
        aria-describedby={error === undefined ? undefined : `${id}-error`}
        onChange={(event) => {
          onChange(event.currentTarget.value);
        }}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <FieldNote id={id} kind="error" text={error} />
    </label>
  );
}

export function Toggle({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label class="toggle">
      <span>{label}</span>
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        onChange={(event) => {
          onChange(event.currentTarget.checked);
        }}
      />
    </label>
  );
}

interface ChipGroupProps<T extends string> {
  label: string;
  values: readonly T[];
  selected: T;
  labelOf: (value: T) => string;
  onSelect: (value: T) => void;
}

export function ChipGroup<T extends string>({ label, values, selected, labelOf, onSelect }: ChipGroupProps<T>) {
  return (
    <div class="chips" role="group" aria-label={label}>
      {values.map((value) => (
        <button
          key={value}
          type="button"
          class="chip"
          aria-pressed={value === selected}
          onClick={() => {
            onSelect(value);
          }}
        >
          {labelOf(value)}
        </button>
      ))}
    </div>
  );
}

export function ErrorAlert({ message }: { message: string | null }) {
  if (message === null) return null;
  return (
    <p class="field__error" role="alert">
      {message}
    </p>
  );
}
