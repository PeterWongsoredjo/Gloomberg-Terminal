/** IDX trading-session phase, resolved against effective-dated calendar. */
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

/** CT-011 — the only shape a serving payload may take, field names mirror the wire JSON verbatim. */
export interface Envelope<TData> {
  api_version: string;
  served_at: string;
  data_as_of: string;
  freshness_slo_met: boolean;
  market_state: SessionPhase;
  quality_flags: QualityFlag[];
  data: TData;
}

/** What components render, a value never travels without its freshness. */
export interface ViewEnvelope<TData> {
  data: TData;
  asOf: string;
  fresh: boolean;
  marketState: SessionPhase;
  flags: QualityFlag[];
}

/** Raised when a payload is not a valid envelope. */
export class EnvelopeError extends Error {}

/** The single ingress that turns wire envelopes into view envelopes. */
export function unwrapEnvelope<TData>(raw: unknown): ViewEnvelope<TData> {
  if (raw === null || typeof raw !== "object") {
    throw new EnvelopeError("payload is not an object");
  }
  const body = raw as Record<string, unknown>;
  if (
    typeof body.data_as_of !== "string" ||
    typeof body.freshness_slo_met !== "boolean" ||
    typeof body.market_state !== "string" ||
    !Array.isArray(body.quality_flags) ||
    !("data" in body)
  ) {
    throw new EnvelopeError("payload is missing envelope fields");
  }
  return {
    data: body.data as TData,
    asOf: body.data_as_of,
    fresh: body.freshness_slo_met,
    marketState: body.market_state as SessionPhase,
    flags: body.quality_flags as QualityFlag[],
  };
}
