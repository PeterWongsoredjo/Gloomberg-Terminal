"use client";

import { useEffect, useState } from "react";
import { Activity, Check, Copy, X } from "lucide-react";

import { LANGFUSE_HOST } from "@/lib/api/client";
import { useNewsFeed, useRunTrace } from "@/lib/api/hooks";
import { fmtWibDateTime, fmtWibTime } from "@/lib/format";
import { LINK_CLASS } from "@/lib/palette";
import type { TraceStep } from "@/lib/types/api";

import { PanelStatus } from "./panel-status";

/* The audit modal, evidence headlines left, reasoning ledger right. */

interface RunLink {
  run_id: string | null;
  trace_id: string | null;
}

interface ProvenanceModalProps {
  title: string;
  provenance: RunLink;
  ticker: string;
  // when present, the evidence pane shows the exact items the model read, not a ticker filter
  evidenceItemIds?: string[];
  onClose: () => void;
}

function stepClass(step: TraceStep): string {
  const status = step.status.toUpperCase();
  if (["FAILED", "ABORTED"].includes(status)) return "border-[#FF3333]/50 text-[#FF3333]";
  if (["DEGRADED", "REACHED", "WARN"].includes(status)) return "border-[#FBBF24]/50 text-[#FBBF24]";
  return "border-[#00FF66]/50 text-[#00FF66]";
}

export function ProvenanceModal({ title, provenance, ticker, evidenceItemIds, onClose }: ProvenanceModalProps) {
  const [copied, setCopied] = useState(false);
  const trace = useRunTrace(provenance.run_id);
  const news = useNewsFeed();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const traceUrl = provenance.trace_id ? `${LANGFUSE_HOST}/trace/${provenance.trace_id}` : null;

  const copy = () => {
    if (!traceUrl) return;
    navigator.clipboard?.writeText(traceUrl).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  // evidence is the exact items the model read when given, else the ticker's tagged headlines
  const evidenceIds = evidenceItemIds ? new Set(evidenceItemIds) : null;
  const evidence = (news.data?.pages ?? [])
    .flatMap((page) => page.data.rows)
    .filter((n) => (evidenceIds ? evidenceIds.has(n.item_id) : n.tickers.includes(ticker)));

  const steps = trace.data?.data.steps ?? [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80"
      role="dialog"
      aria-modal="true"
      aria-label="Audit Provenance"
      onClick={onClose}
    >
      <div
        className="flex h-[80vh] w-[80vw] flex-col border border-zinc-700 bg-[#0a0a0a]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-zinc-800 bg-[#121212] px-3 py-1.5">
          <div className="flex items-center gap-2 text-[#FBBF24]">
            <Activity className="h-3.5 w-3.5" />
            <span className="tracking-widest">{title}</span>
          </div>
          <button
            onClick={onClose}
            aria-label="Close audit modal"
            className="flex items-center gap-1 border border-zinc-700 px-2 py-0.5 text-zinc-400 hover:border-[#FF3333] hover:text-[#FF3333]"
          >
            <X className="h-3 w-3" />
            ESC
          </button>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-2">
          <div className="flex min-h-0 flex-col border-r border-zinc-800">
            <div className="border-b border-zinc-800 bg-[#121212] px-3 py-1 text-zinc-500">
              {evidenceItemIds ? "[EVIDENCE HEADLINES // ITEMS THE MODEL READ]" : `[EVIDENCE HEADLINES // ${ticker} TAGGED ITEMS]`}
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-3">
              {news.isPending && <PanelStatus state="LOADING" />}
              {news.isSuccess && evidence.length === 0 && (
                <PanelStatus
                  state="EMPTY"
                  message={
                    evidenceItemIds
                      ? "The evidence items are outside the loaded feed window."
                      : `No ingested headlines tagged ${ticker}.`
                  }
                />
              )}
              {evidence.map((n) => (
                <div key={n.item_id} className="mb-3 border-l-2 border-zinc-700 pl-2">
                  <div className="text-[10px]">
                    <span className={LINK_CLASS}>{`[${n.source}]`}</span>{" "}
                    <span className="text-zinc-500">{fmtWibTime(n.published_at)}</span>
                  </div>
                  <p className="mt-0.5 text-zinc-300">{n.title}</p>
                  {n.summary && <p className="mt-0.5 text-[11px] leading-relaxed text-zinc-500">{n.summary}</p>}
                  <a
                    href={n.url}
                    target="_blank"
                    rel="noreferrer"
                    className={`text-[10px] ${LINK_CLASS} hover:underline`}
                  >
                    {n.url}
                  </a>
                </div>
              ))}
            </div>
          </div>

          <div className="flex min-h-0 flex-col">
            <div className="border-b border-zinc-800 bg-[#121212] px-3 py-1 text-zinc-500">
              {"[RUN REASONING LEDGER // LANGGRAPH]"}
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-3">
              {provenance.run_id === null && (
                <PanelStatus
                  state="EMPTY"
                  message="This artifact carries no run link. Ledger unavailable."
                />
              )}
              {trace.isPending && provenance.run_id !== null && <PanelStatus state="LOADING" />}
              {trace.isError && (
                <PanelStatus state="ERROR" message="Run trace unreachable. Retrying." />
              )}
              {trace.isSuccess && (
                <>
                  <div className="mb-2 text-zinc-500">
                    RUN {trace.data.data.run_id} · {trace.data.data.status} · ITER{" "}
                    {trace.data.data.loop_iterations} · {fmtWibDateTime(trace.data.asOf)}
                  </div>
                  <ol className="flex flex-col gap-2">
                    {steps.map((s, i) => {
                      const color = stepClass(s);
                      return (
                        <li key={`${s.node}-${i}`} className={`border-l-2 ${color} bg-zinc-900/40 py-1.5 pl-2 pr-2`}>
                          <div className="flex items-center gap-2">
                            <span className="text-zinc-500">{`[STEP ${String(i + 1).padStart(2, "0")}:`}</span>
                            <span className={color.split(" ").pop()}>
                              {s.node.toUpperCase()} · {s.status.toUpperCase()}
                            </span>
                            <span className="text-zinc-500">{"]"}</span>
                          </div>
                          {s.detail && <p className="mt-1 text-zinc-500">{s.detail}</p>}
                        </li>
                      );
                    })}
                  </ol>
                </>
              )}

              <div className="mt-3 border border-zinc-800 bg-black p-2">
                <div className="mb-1 text-zinc-500">{"[LANGFUSE TRACE ADDRESS]"}</div>
                {traceUrl ? (
                  <div className="flex items-center justify-between gap-2">
                    <code className={`truncate ${LINK_CLASS}`}>{traceUrl}</code>
                    <button
                      onClick={copy}
                      aria-label="Copy trace link"
                      className="flex shrink-0 items-center gap-1 border border-zinc-700 px-2 py-0.5 text-zinc-300 hover:border-[#00FF66] hover:text-[#00FF66]"
                    >
                      {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                      {copied ? "COPIED" : "COPY"}
                    </button>
                  </div>
                ) : (
                  <span className="text-zinc-600">No trace recorded for this run.</span>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
