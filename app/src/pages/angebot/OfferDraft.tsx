import { useId, useMemo, useState } from "preact/hooks";
import { createQuote, type OfferBasis } from "../../api/offer";
import type { CrmQuote } from "../../api/types";
import { ErrorAlert } from "../../components/ui";
import { useAction } from "../../hooks/useAsync";
import { formatEuro } from "../../lib/format";
import { AREA_NAMES } from "../../lib/catalogue";
import {
  buildQuoteItems,
  computeTotals,
  openChecksByArea,
  parseQuantity,
  proposePositions,
  quoteTexts,
  type Position,
} from "../../lib/quote";
import { Totals } from "./Totals";

const BILLING_LABELS: Record<Position["billing"], string> = { once: "einmalig", monthly: "monatlich" };

function areaList(areas: readonly string[]): string {
  return areas.map((area) => AREA_NAMES[area] ?? area).join(", ");
}

interface PositionRowProps {
  position: Position;
  quantityText: string;
  onToggle: (enabled: boolean) => void;
  onQuantity: (text: string) => void;
}

function PositionPick({ position, onToggle }: { position: Position; onToggle: (enabled: boolean) => void }) {
  return (
    <label class="position__pick">
      <input
        type="checkbox"
        checked={position.enabled}
        onChange={(event) => {
          onToggle(event.currentTarget.checked);
        }}
      />
      <span class="position__main">
        <span class="row__title">{position.title}</span>
        <span class="row__meta">
          {formatEuro(position.unitPriceNet)} je {position.unit} · {BILLING_LABELS[position.billing]}
        </span>
        <span class="row__meta">
          Deckt {position.openChecks} offene {position.openChecks === 1 ? "Punkt" : "Punkte"} ab: {areaList(position.areas)}
        </span>
      </span>
    </label>
  );
}

function PositionAmount({ position, quantityText, invalid, onQuantity }: Omit<PositionRowProps, "onToggle"> & { invalid: boolean }) {
  const id = useId();
  return (
    <div class="position__amount">
      <label class="qty" for={`${id}-qty`}>
        <span class="visually-hidden">Menge {position.title}</span>
        <input
          id={`${id}-qty`}
          class="input qty__input"
          inputMode="numeric"
          value={quantityText}
          disabled={!position.enabled}
          aria-invalid={invalid}
          onInput={(event) => {
            onQuantity(event.currentTarget.value);
          }}
        />
        <span aria-hidden="true">×</span>
      </label>
      <span class="position__sum num">{position.enabled ? formatEuro(position.quantity * position.unitPriceNet) : "–"}</span>
    </div>
  );
}

function PositionRow({ position, quantityText, onToggle, onQuantity }: PositionRowProps) {
  const invalid = parseQuantity(quantityText) === null;
  return (
    <li class="position" data-off={position.enabled ? undefined : ""}>
      <PositionPick position={position} onToggle={onToggle} />
      <PositionAmount position={position} quantityText={quantityText} invalid={invalid} onQuantity={onQuantity} />
      {invalid && position.enabled && <p class="field__error">Menge von 1 bis 999.</p>}
    </li>
  );
}

function EmptyProposal({ basis }: { basis: OfferBasis }) {
  const open = openChecksByArea(basis.checks);
  if (basis.products.length === 0) {
    return (
      <p class="notice" role="status">
        <strong>Im CRM sind noch keine Preise hinterlegt.</strong> Lege im CRM Produkte mit Preis an und ordne ihnen die
        Prüfbereiche zu, die sie abdecken. Dann erscheinen sie hier als Positionen.
      </p>
    );
  }
  if (open.size === 0) {
    return (
      <p class="notice notice--ok" role="status">
        {basis.checks.length === 0
          ? "Für diese Prüfung liegen noch keine Ergebnisse vor. Die Positionen erscheinen, sobald sie fertig ist."
          : "Diese Prüfung hat keine offenen Punkte. Es gibt nichts anzubieten."}
      </p>
    );
  }
  return (
    <p class="notice" role="status">
      Kein Produkt im CRM deckt die Bereiche mit offenen Punkten ab: {areaList([...open.keys()])}. Ordne den Produkten
      im CRM diese Prüfbereiche zu.
    </p>
  );
}

function usePositions(basis: OfferBasis) {
  const proposed = useMemo(() => proposePositions(basis.checks, basis.products), [basis]);
  const [positions, setPositions] = useState<Position[]>(proposed);
  const [quantities, setQuantities] = useState<Record<string, string>>(() =>
    Object.fromEntries(proposed.map((position) => [position.productId, String(position.quantity)])),
  );
  const update = (productId: string, change: Partial<Position>) => {
    setPositions((previous) => previous.map((item) => (item.productId === productId ? { ...item, ...change } : item)));
  };
  const setQuantity = (productId: string, text: string) => {
    setQuantities((previous) => ({ ...previous, [productId]: text }));
    const quantity = parseQuantity(text);
    if (quantity !== null) update(productId, { quantity });
  };
  return { positions, quantities, update, setQuantity };
}

function useCreateQuote(basis: OfferBasis, positions: readonly Position[], onCreated: (quote: CrmQuote) => void) {
  const action = useAction();
  const crmCustomerId = basis.customer.crm_customer_id;
  const create = async () => {
    if (crmCustomerId === null) return;
    const texts = quoteTexts({
      score: basis.audit.total_score,
      auditDate: basis.audit.requested_at,
      areas: positions.filter((position) => position.enabled).flatMap((position) => position.areas),
    });
    await action.run(async () => {
      onCreated(await createQuote({ crmCustomerId, ...texts, items: (quoteId) => buildQuoteItems(quoteId, positions) }));
    });
  };
  return { action, create };
}

function PositionList({ draft }: { draft: ReturnType<typeof usePositions> }) {
  return (
    <ul class="list">
      {draft.positions.map((position) => (
        <PositionRow
          key={position.productId}
          position={position}
          quantityText={draft.quantities[position.productId] ?? ""}
          onToggle={(on) => {
            draft.update(position.productId, { enabled: on });
          }}
          onQuantity={(text) => {
            draft.setQuantity(position.productId, text);
          }}
        />
      ))}
    </ul>
  );
}

function NotInCrm() {
  return (
    <p class="notice" role="status">
      Dieser Betrieb ist noch nicht mit einem Kunden im CRM verbunden. Starte eine neue Prüfung über „Prüfen“, dann
      wird er verknüpft.
    </p>
  );
}

function CreateButton({ disabled, busy, onCreate }: { disabled: boolean; busy: boolean; onCreate: () => void }) {
  return (
    <button type="button" class="btn btn--primary btn--main btn--long btn--block" data-write disabled={disabled} onClick={onCreate}>
      {busy ? "Angebot wird erstellt …" : "Angebot erstellen"}
    </button>
  );
}

function draftReady(positions: readonly Position[], quantities: Readonly<Record<string, string>>): boolean {
  const enabled = positions.filter((position) => position.enabled);
  return enabled.length > 0 && enabled.every((position) => parseQuantity(quantities[position.productId] ?? "") !== null);
}

export function OfferDraft({ basis, onCreated }: { basis: OfferBasis; onCreated: (quote: CrmQuote) => void }) {
  const draft = usePositions(basis);
  const { action, create } = useCreateQuote(basis, draft.positions, onCreated);
  const crmCustomerId = basis.customer.crm_customer_id;
  if (draft.positions.length === 0) {
    return (
      <section class="panel stack" aria-labelledby="positions-title">
        <h2 id="positions-title">Positionen</h2>
        <EmptyProposal basis={basis} />
      </section>
    );
  }
  return (
    <section class="panel stack" aria-labelledby="positions-title">
      <div>
        <h2 id="positions-title">Positionen</h2>
        <p class="muted small">Vorschlag aus den offenen Punkten der Prüfung. Abwählen oder Menge ändern, bevor du das Angebot erstellst.</p>
      </div>
      <PositionList draft={draft} />
      <Totals totals={computeTotals(draft.positions, basis.smallBusiness)} smallBusiness={basis.smallBusiness} />
      {crmCustomerId === null && <NotInCrm />}
      <ErrorAlert message={action.error} />
      <CreateButton
        disabled={action.busy || crmCustomerId === null || !draftReady(draft.positions, draft.quantities)}
        busy={action.busy}
        onCreate={() => void create()}
      />
    </section>
  );
}
