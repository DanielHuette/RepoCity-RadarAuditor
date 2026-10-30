import type { CheckRow as Row } from "../lib/evaluation";
import { checkAnchor, formatPotential, formatUplift, isOpen, potentialOf } from "../lib/evaluation";
import { catalogueCheck } from "../lib/catalogue";
import { OUTCOME_LABELS, OUTCOME_TONES, URGENCY } from "../lib/labels";
import { formatMeasured, starsLabel, unansweredReviews, type ReviewExample } from "../lib/measured";
import { paidRouteText } from "../lib/routes";
import { FindingBody } from "./FindingCard";

function Stars({ stars }: { stars: number }) {
  return (
    <span class="review-card__stars" role="img" aria-label={starsLabel(stars)}>
      {"★".repeat(Math.max(0, Math.round(stars)))}
    </span>
  );
}

/** The newest reviews without an owner reply (g4), one small box each. */
function UnansweredReviews({ reviews }: { reviews: readonly ReviewExample[] }) {
  return (
    <div class="review-cards">
      <p class="review-cards__title">Ohne Antwort, zum Beispiel</p>
      <ul class="review-cards__list">
        {reviews.map((review, index) => (
          <li key={index} class="review-card">
            {(review.stars !== null || review.when !== null) && (
              <p class="review-card__meta">
                {review.stars !== null && <Stars stars={review.stars} />}
                {review.when !== null && <span>{review.when}</span>}
              </p>
            )}
            {review.text === "" ? <p class="review-card__text muted">ohne Text</p> : <p class="review-card__text">„{review.text}“</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function CheckSummary({ row }: { row: Row }) {
  const { check, finding } = row;
  const open = isOpen(check);
  const potential = potentialOf(check);
  const uplift = open ? formatUplift(check.uplift_low, check.uplift_high) : null;
  const route = paidRouteText(check.measured);
  return (
    <summary class="check__summary">
      <span class="check__name">{row.name}</span>
      <span class="check__meta">
        <span class={`badge ${OUTCOME_TONES[check.outcome]}`}>{OUTCOME_LABELS[check.outcome]}</span>
        {finding !== null && <span class={`badge ${URGENCY[finding.severity].tone}`}>{URGENCY[finding.severity].label}</span>}
        {open && potential > 0 && <span class="check__points">{formatPotential(potential)}</span>}
      </span>
      {uplift !== null && <span class="check__uplift">{uplift}</span>}
      {route !== null && <span class="check__route">{route}</span>}
    </summary>
  );
}

/** The finding's measured sentence, the measured values and unanswered reviews; nothing when none is there. */
function Measured({ row }: { row: Row }) {
  const explanation = row.finding?.explanation.trim() ?? "";
  const lines = formatMeasured(row.check.measured);
  const reviews = unansweredReviews(row.check.measured);
  if (explanation === "" && lines.length === 0 && reviews.length === 0) return null;
  return (
    <div class="check__measured">
      <p class="check__label">Gemessen</p>
      {explanation !== "" && <p>{row.finding?.explanation}</p>}
      {lines.length > 0 && (
        <dl class="measured">
          {lines.map((line) => (
            <div key={line.key}>
              <dt>{line.label}</dt>
              <dd>{line.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {reviews.length > 0 && <UnansweredReviews reviews={reviews} />}
    </div>
  );
}

function CustomerCost({ impact }: { impact: string }) {
  return (
    <div class="finding">
      <dl>
        <div>
          <dt>Was es den Kunden kostet</dt>
          <dd>{impact}</dd>
        </div>
      </dl>
    </div>
  );
}

/** One check: outcome, urgency and potential at a glance; on tap why it matters, what was measured and the fix. */
export function CheckRow({ row }: { row: Row }) {
  const { check, finding } = row;
  const entry = catalogueCheck(check.check_id);
  return (
    <li class="check" id={checkAnchor(check.check_id)}>
      <details>
        <CheckSummary row={row} />
        <div class="check__body">
          {entry !== undefined && <p class="check__why">{entry.why}</p>}
          <Measured row={row} />
          {finding !== null ? (
            <FindingBody finding={finding} withExplanation={false} />
          ) : (
            isOpen(check) && entry !== undefined && <CustomerCost impact={entry.customerImpact} />
          )}
        </div>
      </details>
    </li>
  );
}
