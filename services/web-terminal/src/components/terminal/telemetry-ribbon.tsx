"use client";

import { useTelemetry } from "@/lib/api/hooks";
import { fmtWibDateTime } from "@/lib/format";
import { DOWN_CLASS, UP_CLASS, WARN_CLASS } from "@/lib/palette";
import type { StreamStatus } from "@/lib/stream/tape-stream";

/* Row three, the pipeline and inference health ribbon. */

interface TelemetryRibbonProps {
  streamStatus: StreamStatus;
}

const FEED_LABEL: Record<StreamStatus, { text: string; dot: string; textClass: string }> = {
  CONNECTING: { text: "FEED CONNECTING", dot: "bg-zinc-500", textClass: "text-zinc-500" },
  LIVE: { text: "FEED LIVE", dot: "animate-pulse bg-[#00FF66]", textClass: UP_CLASS },
  RESYNCING: { text: "FEED RESYNC", dot: "bg-[#FBBF24]", textClass: WARN_CLASS },
  FROZEN: { text: "FEED FROZEN", dot: "bg-zinc-400", textClass: "text-zinc-400" },
  RECONNECTING: { text: "FEED RECONNECTING", dot: "bg-[#FBBF24]", textClass: WARN_CLASS },
  OFFLINE: { text: "FEED OFFLINE", dot: "bg-[#FF3333]", textClass: DOWN_CLASS },
};

export function TelemetryRibbon({ streamStatus }: TelemetryRibbonProps) {
  const telemetry = useTelemetry();
  const envelope = telemetry.data ?? null;
  const data = envelope?.data ?? null;
  const feed = FEED_LABEL[streamStatus];

  const breachCount = (data?.slo_breaches.length ?? 0) + (data?.active_alerts.length ?? 0);
  const sloClass = envelope === null ? "text-zinc-600" : breachCount > 0 || !envelope.fresh ? DOWN_CLASS : UP_CLASS;
  const sloText =
    envelope === null ? "—" : breachCount > 0 ? `${breachCount} BREACH` : envelope.fresh ? "COMPLIANT" : "STALE";

  return (
    <footer className="flex h-[4vh] min-h-7 items-center gap-4 overflow-hidden border-t border-zinc-800 bg-[#0a0a0a] px-2 text-zinc-500">
      <span>
        QUARANTINE COUNT: <span className="text-zinc-300">{data?.quarantine_row_count ?? "—"}</span>
      </span>
      <span>
        GOLD PROMOTED: <span className="text-zinc-300">{fmtWibDateTime(data?.gold_promoted_at)}</span>
      </span>
      <span>
        COVERAGE:{" "}
        <span className="text-zinc-300">
          {data?.coverage_ratio === null || data === null ? "—" : `${(data.coverage_ratio * 100).toFixed(1)}%`}
        </span>
      </span>
      <span>
        SLO STATUS: <span className={sloClass}>{sloText}</span>
      </span>
      <span className="hidden lg:inline">
        LLM RUNS: <span className="text-zinc-300">{data?.llm_runs ?? "—"}</span>
        {data !== null && data.llm_runs_degraded > 0 && (
          <span className={`ml-1 ${WARN_CLASS}`}>({data.llm_runs_degraded} DEGRADED)</span>
        )}
      </span>
      <span className="hidden xl:inline">
        TOKENS: <span className="text-zinc-300">{data?.total_tokens ?? "—"}</span>
      </span>
      <span className="ml-auto flex items-center gap-1.5">
        <span className={`h-1.5 w-1.5 rounded-full ${feed.dot}`} />
        <span className={feed.textClass}>{feed.text}</span>
      </span>
    </footer>
  );
}
