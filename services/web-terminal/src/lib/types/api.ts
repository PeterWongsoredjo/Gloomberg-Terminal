import type { QualityFlag } from "./envelope";

/* Serving payload shapes, field names mirror the wire JSON verbatim. */

export type PriceLimit = "NONE" | "ARA" | "ARB";

export type PriceIntegrity = "CLEAN" | "ADJUSTMENT_PENDING";

export type InsightStatus = "OK" | "LOW_CONFIDENCE" | "DEGRADED" | "STALE";

export interface IndexLevel {
  index_code: string;
  level: number;
  change: number | null;
}

export interface MarketState {
  trade_date: string;
  session_phase: string;
  is_trading_day: boolean;
  close_time_utc: string;
  ihsg: IndexLevel | null;
}

export interface LiveTapeRow {
  security_id: number;
  ticker: string;
  board: string;
  is_fca: boolean;
  special_notation: string[];
  last_idr: number;
  prev_idr: number | null;
  change_idr: number | null;
  change_pct: number | null;
  at_price_limit: PriceLimit;
  volume_shares: number | null;
  value_idr: number | null;
  net_foreign_idr: number | null;
  price_series_integrity: PriceIntegrity;
  dq_flags: QualityFlag[];
}

export interface LiveTapePage {
  trade_date: string;
  rows: LiveTapeRow[];
}

export interface NewsSentimentProvenance {
  artifact_id: string;
  run_id: string | null;
  trace_id: string | null;
  provider: string | null;
  model: string | null;
  prompt_version: string | null;
  confidence: number | null;
  generated_at: string | null;
  evidence_item_ids: string[];
}

export interface NewsItem {
  item_id: string;
  trade_date: string;
  source: string;
  lang: string | null;
  title: string;
  summary: string | null;
  url: string;
  published_at: string;
  tickers: string[];
  sentiment_score: number | null;
  sentiment_label: string | null;
  sentiment_provenance: NewsSentimentProvenance | null;
}

export interface NewsFeedPage {
  rows: NewsItem[];
  next_cursor: string | null;
}

export interface InsightSignal {
  type: string;
  value: string;
  confidence: number | null;
}

export interface InsightProvenance {
  provider: string;
  model: string;
  prompt_version: string;
  run_id: string | null;
  trace_id: string | null;
  generated_at: string;
  loop_iterations: number | null;
}

export interface InsightPanelData {
  ticker: string;
  headline: string;
  narrative: string;
  signals: InsightSignal[];
  contradictions: string[];
  provenance: InsightProvenance;
  confidence: number;
  status: InsightStatus;
  quality_flags: QualityFlag[];
}

export interface DataTelemetry {
  trade_date: string;
  session_state: string;
  coverage_ratio: number | null;
  missing_ticker_count: number | null;
  quarantine_row_count: number | null;
  dbt_tests_passed: number | null;
  dbt_tests_failed: number | null;
  gold_promoted_at: string | null;
  gold_promotion_ok: boolean | null;
  slo_breaches: Record<string, unknown>[];
  active_alerts: Record<string, unknown>[];
  llm_runs: number;
  llm_runs_degraded: number;
  total_tokens: number;
  notional_cost: number;
  quota_pct_groq: number;
  quota_pct_gemini: number;
  breaker_state_groq: string | null;
  breaker_state_gemini: string | null;
  low_confidence_artifact_count: number;
  live_prompt_versions: Record<string, string>;
}

export interface RunStatusData {
  run_id: string;
  status: string;
  objective: string;
  trade_date: string;
  abort_reason: string | null;
  consumed_tokens: number;
  consumed_iterations: number;
  started_at: string;
  ended_at: string | null;
}

export interface TraceStep {
  node: string;
  status: string;
  detail: string | null;
}

export interface RunReasoningTrace {
  run_id: string;
  objective: string;
  trade_date: string;
  status: string;
  loop_iterations: number;
  trace_id: string | null;
  steps: TraceStep[];
}
