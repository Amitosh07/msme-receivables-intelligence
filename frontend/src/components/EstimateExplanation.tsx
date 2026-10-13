import type { ExplanationFactor, ImportanceFactor, ModelInsights, PredictionExplanation } from "../lib/types";

const pct = (value: number) => `${Math.round(value * 100)}%`;
const MINOR_SHARE = 0.04;

export function EstimateDrivers({ explanation, limit }: { explanation: PredictionExplanation; limit?: number }) {
  const major = explanation.factors.filter((f) => f.share >= MINOR_SHARE).slice(0, limit ?? explanation.factors.length);
  const minor = explanation.factors.filter((f) => !major.includes(f));
  const minorShare = minor.reduce((sum, f) => sum + f.share, 0);
  return (
    <div className="drivers">
      <p className="drivers-range">
        Starts from <b>{pct(explanation.baseline_risk)}</b>, the model’s average for any invoice. This invoice’s facts move it to{" "}
        <b>{pct(explanation.risk_score)}</b>.
      </p>
      <ul className="driver-list">
        {major.map((factor) => <Driver key={factor.key} factor={factor} />)}
      </ul>
      {minor.length > 0 && (
        <p className="quiet driver-minor">
          {minor.length} smaller factor{minor.length === 1 ? "" : "s"} ({pct(minorShare)} of the effect combined):{" "}
          {minor.map((f) => f.label.toLowerCase()).join(", ")}.
        </p>
      )}
      {!explanation.matches_stored && (
        <p className="driver-warning" role="status">
          Payment history has changed since this estimate was saved ({pct(explanation.stored_risk_score)}). Re-generate the
          prediction to update it.
        </p>
      )}
      {explanation.unknown_categories.length > 0 && (
        <p className="quiet driver-minor">
          The model did not see this {explanation.unknown_categories.join(" or ")} during training, so it treats it as unknown.
        </p>
      )}
    </div>
  );
}

function Driver({ factor }: { factor: ExplanationFactor }) {
  const raises = factor.effect === "raises";
  return (
    <li className="driver">
      <div className="driver-head">
        <strong>{factor.label}</strong>
        <span className={raises ? "driver-effect raises" : "driver-effect lowers"}>
          {raises ? "Raises risk" : "Lowers risk"} · {pct(factor.share)}
        </span>
      </div>
      <div className="driver-bar" aria-hidden="true">
        <i className={raises ? "raises" : "lowers"} style={{ width: `${Math.max(4, factor.share * 100)}%` }} />
      </div>
      <span className="driver-evidence">{factor.evidence}</span>
    </li>
  );
}

export function ModelInsightsDetails({ insights }: { insights: ModelInsights }) {
  return (
    <details className="model-insights">
      <summary>How the model decides overall</summary>
      <div className="insight-columns">
        <ImportanceList title="Late-payment risk relies on" items={insights.risk_drivers} />
        <ImportanceList title="Payment timing relies on" items={insights.timing_drivers} />
      </div>
      <dl className="insight-metrics">
        <div>
          <dt>Ranking accuracy</dt>
          <dd>{pct(insights.roc_auc)}</dd>
          <small>How often a late invoice scored higher than an on-time one (ROC AUC)</small>
        </div>
        <div>
          <dt>Late / on-time calls</dt>
          <dd>{pct(insights.accuracy)}</dd>
          <small>Correct on held-out test invoices</small>
        </div>
        <div>
          <dt>Timing error</dt>
          <dd>{insights.timing_median_error_days.toFixed(1)} days</dd>
          <small>Typical (median); {insights.timing_mae_days.toFixed(1)} days on average</small>
        </div>
      </dl>
      <ul className="insight-limits">
        {insights.limitations.map((line) => <li key={line}>{line}</li>)}
      </ul>
    </details>
  );
}

function ImportanceList({ title, items }: { title: string; items: ImportanceFactor[] }) {
  const max = Math.max(...items.map((i) => i.importance), 0.0001);
  return (
    <div>
      <h4>{title}</h4>
      <ul className="importance-list">
        {items.filter((i) => i.importance >= 0.02).map((item) => (
          <li key={item.key}>
            <span>{item.label}</span>
            <div className="importance-bar" aria-hidden="true"><i style={{ width: `${(item.importance / max) * 100}%` }} /></div>
            <b>{pct(item.importance)}</b>
          </li>
        ))}
      </ul>
    </div>
  );
}
