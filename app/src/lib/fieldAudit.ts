// Arguments of start_field_audit from the Prüfen page (address only) and from the correction form
// of the Auswertung (address plus name, city and industry); the server identifies the rest itself.
import { normaliseWebsite } from "./format";

/** Arguments of the database function start_field_audit that the app sends. */
export interface FieldAuditRequest {
  p_website: string;
  p_disabled_checks: string[];
  p_name?: string;
  p_city?: string;
  p_industry?: string;
}

export type WebsiteValidation = { ok: true; website: string } | { ok: false; error: string };

export interface CorrectionForm {
  name: string;
  city: string;
  industry: string;
}

export type CorrectionErrors = Partial<Record<keyof CorrectionForm, string>>;

export type CorrectionValidation = { ok: true; request: FieldAuditRequest } | { ok: false; errors: CorrectionErrors };

/** An address with or without https://, as typed on a phone; normalised to an absolute URL. */
export function validateWebsite(input: string): WebsiteValidation {
  if (input.trim() === "") return { ok: false, error: "Bitte die Website des Betriebs eingeben." };
  const website = normaliseWebsite(input);
  return website === null ? { ok: false, error: "Bitte eine Adresse wie meisterbetrieb-muster.de eingeben." } : { ok: true, website };
}

export function quickAuditRequest(website: string, disabledChecks: readonly string[]): FieldAuditRequest {
  return { p_website: website, p_disabled_checks: [...disabledChecks] };
}

/** A new audit of the same website with name, city and industry given by hand. */
export function validateCorrection(form: CorrectionForm, website: string, disabledChecks: readonly string[]): CorrectionValidation {
  const errors: CorrectionErrors = {};
  if (form.name.trim() === "") errors.name = "Bitte den Namen des Betriebs eingeben.";
  if (form.city.trim() === "") errors.city = "Bitte den Ort eingeben.";
  if (form.industry.trim() === "") errors.industry = "Bitte eine Branche wählen.";
  if (Object.keys(errors).length > 0) return { ok: false, errors };
  return {
    ok: true,
    request: {
      ...quickAuditRequest(website, disabledChecks),
      p_name: form.name.trim(),
      p_city: form.city.trim(),
      p_industry: form.industry.trim(),
    },
  };
}
