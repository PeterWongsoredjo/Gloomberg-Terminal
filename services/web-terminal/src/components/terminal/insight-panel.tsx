"use client";

import { useState } from "react";
import { Activity, AlertTriangle, RefreshCw, Zap } from "lucide-react";

import { useInsight } from "@/lib/api/hooks";
import { fmtWibDateTime } from "@/lib/format";
import { UP_CLASS, WARN_CLASS } from "@/lib/palette";
import type { InsightPanelData } from "@/lib/types/api";

import { PanelStatus } from "./panel-status";
import { ProvenanceModal } from "./provenance-modal";

/* Column C, the descriptive GenAI report with full provenance. */

interface InsightPanelProps {
  ticker: string;
}

const STATUS_CLASS: Record<InsightPanelData["status"], string> = {
  OK: UP_CLASS,
  LOW_CONFIDENCE: WARN_CLASS,
  DEGRADED: WARN_CLASS,
  STALE: WARN_CLASS,
};

export function InsightPanel({ ticker }: InsightPanelProps) {
  const insight = useInsight(ticker);
  const [modalOpen, setModalOpen] = useState(false);

  const envelope = insight.data ?? null;
  const panel = envelope?.data ?? null;
  const degraded = panel?.status === "DEGRADED";
  const stale = envelope !== null && !envelope.fresh;
  const refreshing = insight.isRefetching;

  return (
    <section className="relative flex min-h-0 flex-col">
      {degraded && (
        <div className="absolute inset-0 z-30 flex items-center justify-center bg-black/60 bg-[repeating-linear-gradient(45deg,transparent,transparent_6px,rgba(251,191,36,0.06)_6px,rgba(251,191,36,0.06)_12px)]">
          <div className="mx-3 border border-[#FBBF24] bg-black/80 px-3 py-2 text-center text-[#FBBF24]">
            [INFERENCE DEGRADED — PROVIDER LADDER EXHAUSTED, SHOWING LAST ARTIFACT]
          </div>
        </div>
      )}

      <div className="flex items-center justify-between border-b border-zinc-800 bg-[#121212] px-2 py-1 text-[#00FF66]">
        <span className="flex items-center gap-2 tracking-widest">
          <Activity className="h-3 w-3" />
          [GENAI INTELLIGENCE REPORT]
          {panel && (
            <span className={`ml-2 ${STATUS_CLASS[panel.status]}`}>
              {panel.status} · CONF {panel.confidence.toFixed(2)}
            </span>
          )}
        </span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => insight.refetch()}
            disabled={refreshing}
            className="flex items-center gap-1.5 border border-zinc-700 bg-zinc-900 px-2 py-0.5 tracking-wide text-zinc-100 transition-colors hover:border-[#FBBF24] hover:text-[#FBBF24] disabled:opacity-50"
          >
            <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
            {refreshing ? "REFRESHING" : "REFRESH INSIGHT"}
          </button>
          <button
            onClick={() => setModalOpen(true)}
            disabled={!panel}
            className="flex items-center gap-1.5 border border-zinc-700 bg-zinc-900 px-2 py-0.5 font-medium tracking-wide text-zinc-100 transition-colors hover:border-[#00FF66] hover:text-[#00FF66] disabled:opacity-50"
          >
            <Zap className="h-3 w-3" />
            AUDIT PROVENANCE
          </button>
        </div>
      </div>

      {insight.isPending && <PanelStatus state="LOADING" />}
      {insight.isError && (
        <PanelStatus state="ERROR" message="Insight endpoint unreachable. Retrying." />
      )}
      {insight.isSuccess && panel === null && (
        <PanelStatus
          state="EMPTY"
          message={`No insight artifact for ${ticker} yet. The scheduler refreshes insights hourly during session.`}
        />
      )}

      {panel && (
        <div className="grid min-h-0 flex-1 grid-cols-2 gap-0 overflow-auto">
          <div className="overflow-auto p-2">
            <p className="mb-1 text-zinc-200">{panel.headline}</p>
            <p className={`leading-relaxed ${stale ? WARN_CLASS : "text-zinc-400"}`}>
              {panel.narrative}
            </p>
            {panel.signals.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {panel.signals.map((s) => (
                  <span key={`${s.type}-${s.value}`} className="border border-zinc-700 px-1.5 py-0.5 text-zinc-400">
                    <span className="text-zinc-600">{s.type}</span> {s.value}
                    {s.confidence !== null && (
                      <span className="ml-1 text-zinc-600">({s.confidence.toFixed(2)})</span>
                    )}
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="overflow-auto border-l border-zinc-800 p-2">
            {panel.contradictions.length > 0 ? (
              <div className="border border-[#FBBF24]/60 bg-[#FBBF24]/10 p-2">
                <div className="mb-1 flex items-center gap-1.5 text-[#FBBF24]">
                  <AlertTriangle className="h-3 w-3" />
                  <span className="tracking-widest">[SIGNAL CONTRADICTIONS ENCOUNTERED]</span>
                </div>
                <ul className="flex flex-col gap-1 text-zinc-400">
                  {panel.contradictions.map((c) => (
                    <li key={c}>• {c}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <div className="border border-zinc-800 p-2 text-zinc-600">
                No signal contradictions recorded for this artifact.
              </div>
            )}
          </div>
        </div>
      )}

      {panel && (
        <div className="flex items-center gap-3 border-t border-zinc-800 bg-[#0a0a0a] px-2 py-1 text-[10px] text-zinc-600">
          <span>
            {panel.provenance.provider} / {panel.provenance.model}
          </span>
          <span>PROMPT {panel.provenance.prompt_version}</span>
          <span>GENERATED {fmtWibDateTime(panel.provenance.generated_at)}</span>
          {panel.provenance.loop_iterations !== null && (
            <span>ITER {panel.provenance.loop_iterations}</span>
          )}
          <span className="ml-auto">DESCRIPTIVE · NON-ADVISORY</span>
        </div>
      )}

      {modalOpen && panel && (
        <ProvenanceModal
          title={`AUDIT PROVENANCE // IDX:${ticker}`}
          provenance={panel.provenance}
          ticker={ticker}
          onClose={() => setModalOpen(false)}
        />
      )}
    </section>
  );
}
