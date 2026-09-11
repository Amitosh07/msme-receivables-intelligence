import { titleCase } from "../lib/format";
export function StatusBadge({ value }: { value: string }) { const normal = value.toUpperCase();  const kind = normal === "ERROR" || normal === "FAILED" ? "bad"
    : normal === "PROCESSED" || normal === "READY" || normal === "PAID" || normal === "VERIFIED" ? "good"
    : normal === "NEEDS_REVIEW" ? "warning"
    : normal === "PROCESSING" || normal === "PENDING" || normal === "PARTIAL" ? "pending"
    : "neutral"; return <span className={`badge ${kind}`}><i />{titleCase(value)}</span>; }
export function RiskBadge({ tier, score }: { tier: string; score?: number }) { return <span className={`badge risk ${tier.toLowerCase()}`}><i />{titleCase(tier)}{score !== undefined && <> · {Math.round(score * 100)}%</>}</span>; }
