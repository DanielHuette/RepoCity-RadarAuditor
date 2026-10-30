import { formatEuro } from "../../lib/format";

interface TotalsProps {
  totals: { net: number; vat: number; gross: number; onceNet?: number; monthlyNet?: number };
  smallBusiness: boolean;
}

export function Totals({ totals, smallBusiness }: TotalsProps) {
  const split = totals.onceNet !== undefined && totals.monthlyNet !== undefined && totals.monthlyNet > 0;
  return (
    <div class="totals">
      <dl aria-label="Summen">
        <div>
          <dt>Netto</dt>
          <dd class="num">{formatEuro(totals.net)}</dd>
        </div>
        <div>
          <dt>{smallBusiness ? "Umsatzsteuer (Kleinunternehmer)" : "Umsatzsteuer"}</dt>
          <dd class="num">{formatEuro(totals.vat)}</dd>
        </div>
        <div class="totals__gross">
          <dt>Brutto</dt>
          <dd class="num">{formatEuro(totals.gross)}</dd>
        </div>
      </dl>
      {split && (
        <p class="muted small">
          davon einmalig {formatEuro(totals.onceNet ?? 0)} und monatlich {formatEuro(totals.monthlyNet ?? 0)} netto
        </p>
      )}
    </div>
  );
}
