// Top of an Auswertung: what the server identified from the website, with a correction that
// starts a new audit of the same website with the values given by hand and the same checks.
import { useLocation } from "preact-iso";
import { useState } from "preact/hooks";
import { identificationSource, startFieldAudit } from "../api/queries";
import type { Audit, Customer } from "../api/types";
import { useIsAdmin } from "../hooks/role";
import { useAction, useAsync } from "../hooks/useAsync";
import { validateCorrection, type CorrectionErrors, type CorrectionForm } from "../lib/fieldAudit";
import { identityFacts, type IdentityFact } from "../lib/identity";
import { INDUSTRY_OPTIONS, resolveIndustry } from "../lib/industries";
import { Field, Select } from "./ui";

const INDUSTRY_CHOICES = [{ value: "", label: "Bitte wählen" }, ...INDUSTRY_OPTIONS];

function initialForm(customer: Customer): CorrectionForm {
  return { name: customer.name, city: customer.city, industry: resolveIndustry(customer.industry)?.id ?? "" };
}

interface CorrectionProps {
  customer: Customer;
  audit: Audit;
  onCancel: () => void;
}

/** Form state of the correction; a valid form starts the new audit and opens its progress. */
function useCorrection(customer: Customer, audit: Audit) {
  const { route } = useLocation();
  const [form, setForm] = useState<CorrectionForm>(() => initialForm(customer));
  const [errors, setErrors] = useState<CorrectionErrors>({});
  const action = useAction();
  const set = (field: keyof CorrectionForm) => (value: string) => {
    setForm((previous) => ({ ...previous, [field]: value }));
  };
  const submit = (event: SubmitEvent) => {
    event.preventDefault();
    if (customer.website === null) return;
    const result = validateCorrection(form, customer.website, audit.disabled_checks ?? []);
    setErrors(result.ok ? {} : result.errors);
    if (!result.ok) return;
    void action.run(async () => {
      const id = await startFieldAudit(result.request);
      route(`/?pruefung=${id}`);
    });
  };
  return { form, errors, set, submit, action };
}

function Correction({ customer, audit, onCancel }: CorrectionProps) {
  const { form, errors, set, submit, action } = useCorrection(customer, audit);
  return (
    <form class="correction" noValidate aria-label="Erkannte Angaben korrigieren" onSubmit={submit}>
      <div class="correction__fields">
        <Field label="Betrieb" value={form.name} onInput={set("name")} autoComplete="organization" error={errors.name} />
        <Field label="Ort" value={form.city} onInput={set("city")} autoComplete="address-level2" error={errors.city} />
        <Select label="Branche" value={form.industry} options={INDUSTRY_CHOICES} onChange={set("industry")} error={errors.industry} />
      </div>
      {action.error !== null && (
        <p class="field__error" role="alert">
          {action.error}
        </p>
      )}
      <div class="cluster">
        <button type="submit" class="btn btn--primary" disabled={action.busy}>
          {action.busy ? "Wird gestartet …" : "Neu prüfen"}
        </button>
        <button type="button" class="btn btn--ghost" onClick={onCancel}>
          Abbrechen
        </button>
      </div>
    </form>
  );
}

function IdentityHead({ canCorrect, editing, onToggle }: { canCorrect: boolean; editing: boolean; onToggle: () => void }) {
  return (
    <div class="identity__head">
      <h2 id="identity-title">Erkannt</h2>
      {canCorrect && (
        <button type="button" class="link-action" aria-expanded={editing} onClick={onToggle}>
          Korrigieren
        </button>
      )}
    </div>
  );
}

function IdentityFacts({ facts }: { facts: readonly IdentityFact[] }) {
  return (
    <dl class="identity__facts">
      {facts.map((fact) => (
        <div key={fact.label}>
          <dt>{fact.label}</dt>
          <dd>{fact.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function IdentityBar({ customer, audit }: { customer: Customer; audit: Audit }) {
  const [editing, setEditing] = useState(false);
  const admin = useIsAdmin();
  const rawPath = audit.raw_data_path ?? null;
  const source = useAsync(async () => (rawPath === null ? null : identificationSource(rawPath)), [rawPath]);
  const facts = identityFacts(customer, source.error ?? source.data ?? null);
  return (
    <section class="panel identity" aria-labelledby="identity-title">
      <IdentityHead
        canCorrect={admin && customer.website !== null}
        editing={editing}
        onToggle={() => {
          setEditing((value) => !value);
        }}
      />
      <IdentityFacts facts={facts} />
      {editing && (
        <Correction
          customer={customer}
          audit={audit}
          onCancel={() => {
            setEditing(false);
          }}
        />
      )}
    </section>
  );
}
