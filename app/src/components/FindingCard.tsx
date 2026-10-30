import type { Finding } from "../api/types";
import { formatFixPayload } from "../lib/clipboard";
import { URGENCY } from "../lib/labels";
import { CopyButton } from "./CopyButton";

/**
 * What was measured, what it costs the customer, what to do and the copyable fix of one finding.
 * A check row shows the measurement itself and passes `withExplanation={false}`.
 */
export function FindingBody({ finding, withExplanation = true }: { finding: Finding; withExplanation?: boolean }) {
  const payload = formatFixPayload(finding.fix_payload);
  return (
    <div class="finding">
      <dl>
        {withExplanation && (
          <div>
            <dt>Was wir gemessen haben</dt>
            <dd>{finding.explanation}</dd>
          </div>
        )}
        <div>
          <dt>Was es den Kunden kostet</dt>
          <dd>{finding.customer_impact}</dd>
        </div>
        <div>
          <dt>Was zu tun ist</dt>
          <dd>{finding.fix}</dd>
        </div>
      </dl>
      {payload !== null && (
        <div class="stack stack--tight">
          <pre>{payload}</pre>
          <CopyButton text={payload} />
        </div>
      )}
    </div>
  );
}

export function FindingCard({ finding }: { finding: Finding }) {
  const urgency = URGENCY[finding.severity];
  return (
    <article class="panel stack stack--tight">
      <div class="finding__head">
        <h3>{finding.title}</h3>
        <span class={`badge ${urgency.tone}`}>{urgency.label}</span>
      </div>
      <FindingBody finding={finding} />
    </article>
  );
}
