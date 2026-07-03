/** IDX trading-session phase (01 §2.3); resolved against effective-dated calendar. */
export type SessionPhase =
  | "PRE_OPENING"
  | "SESSION_1"
  | "SESSION_BREAK"
  | "SESSION_2"
  | "PRE_CLOSING"
  | "RANDOM_CLOSING"
  | "POST_TRADING"
  | "CLOSED";

/** Closed data-quality vocabulary that travels with every record (CT-008). */
export type QualityFlag =
  | "MISSING_UPSTREAM"
  | "STALE"
  | "COVERAGE_GAP"
  | "ADJUSTMENT_PENDING"
  | "FCA_PRICING"
  | "SUSPENDED"
  | "DERIVED_ESTIMATE"
  | "LLM_LOW_CONFIDENCE"
  | "SCHEMA_DRIFT_QUARANTINE";

/** CT-011 — the only shape a serving payload may take (01 §4.10); field names mirror the wire JSON verbatim. */
export interface Envelope<TData> {
  api_version: string;
  served_at: string;
  data_as_of: string;
  freshness_slo_met: boolean;
  market_state: SessionPhase;
  quality_flags: QualityFlag[];
  data: TData;
}
