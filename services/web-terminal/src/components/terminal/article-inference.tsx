"use client";

import { useState } from "react";
import { Brain, Zap } from "lucide-react";

import { feedRows, useNewsFeed } from "@/lib/api/hooks";
import { fmtWibDateTime } from "@/lib/format";
import { LINK_CLASS, UNSCORED_CLASS, labelClass, sentimentGlyph } from "@/lib/palette";

import { LinkOut } from "./link-out";
import { PanelStatus } from "./panel-status";
import { ProvenanceModal } from "./provenance-modal";

/* Column C left, what the model made of one headline. */

interface ArticleInferenceProps {
  itemId: string | null;
}

export function ArticleInference({ itemId }: ArticleInferenceProps) {
  const feed = useNewsFeed();
  const [modalOpen, setModalOpen] = useState(false);

  const item = feedRows(feed.data?.pages).find((r) => r.item_id === itemId) ?? null;
  const scored = item !== null && item.sentiment_label !== null;
  const provenance = item?.sentiment_provenance ?? null;

  return (
    <section className="flex min-h-0 flex-col border-r border-zinc-800">
      <div className="flex items-center justify-between border-b border-zinc-800 bg-[#121212] px-2 py-1 text-zinc-400">
        <span className="flex items-center gap-2 tracking-widest">
          <Brain className="h-3 w-3" />
          [AI READ ON THIS ARTICLE]
        </span>
        {provenance && (
          <button
            onClick={() => setModalOpen(true)}
            className="flex items-center gap-1.5 border border-zinc-700 bg-zinc-900 px-2 py-0.5 tracking-wide text-zinc-100 transition-colors hover:border-[#00FF66] hover:text-[#00FF66]"
          >
            <Zap className="h-3 w-3" />
            AUDIT
          </button>
        )}
      </div>

      {itemId === null && (
        <PanelStatus
          state="EMPTY"
          message="Click a headline in the news feed to see how the model read it."
        />
      )}
      {itemId !== null && feed.isPending && <PanelStatus state="LOADING" />}
      {itemId !== null && item === null && !feed.isPending && (
        <PanelStatus
          state="EMPTY"
          message="That headline has scrolled out of the loaded feed window."
        />
      )}

      {item && (
        <div className="min-h-0 flex-1 overflow-auto p-2">
          <div className="flex items-center gap-1.5 text-[10px]">
            <span className={LINK_CLASS}>{`[${item.source}]`}</span>
            <span className="text-zinc-500">{fmtWibDateTime(item.published_at)}</span>
          </div>
          <p className="mt-0.5 leading-snug text-zinc-200">{item.title}</p>

          {!scored ? (
            <div className={`mt-2 border border-zinc-800 p-2 ${UNSCORED_CLASS}`}>
              Not scored yet. The model reads new headlines on the next scoring poll.
            </div>
          ) : (
            <>
              {item.sentiment_rationale ? (
                <p className="mt-2 leading-relaxed text-zinc-400">{item.sentiment_rationale}</p>
              ) : (
                <div className="mt-2">
                  <div className="tracking-widest text-zinc-600">[EVIDENCE PHRASES]</div>
                  {item.sentiment_drivers.length > 0 ? (
                    <ul className="mt-1 flex flex-col gap-0.5 text-zinc-400">
                      {item.sentiment_drivers.map((d) => (
                        <li key={d}>• {d}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className={`mt-1 ${UNSCORED_CLASS}`}>
                      Scored before the model was asked to explain itself.
                    </p>
                  )}
                </div>
              )}

              <div className="mt-2 flex items-center gap-3 border-t border-zinc-900 pt-2">
                <span className={`tabular-nums ${labelClass(item.sentiment_label)}`}>
                  {sentimentGlyph(item.sentiment_label, item.sentiment_score)}
                </span>
                <span className={labelClass(item.sentiment_label)}>{item.sentiment_label}</span>
                {provenance?.confidence != null && (
                  <span className="text-zinc-600">CONF {provenance.confidence.toFixed(2)}</span>
                )}
              </div>

              {item.ticker_sentiments.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {item.ticker_sentiments.map((t) => (
                    <span key={t.ticker} className="border border-zinc-700 px-1.5 py-0.5">
                      <span className={labelClass(t.sentiment_label)}>{t.ticker}</span>
                      <span className="ml-1 text-zinc-600">{t.relevance ?? "UNSCORED"}</span>
                      {t.sentiment_score !== null && (
                        <span className={`ml-1 tabular-nums ${labelClass(t.sentiment_label)}`}>
                          {t.sentiment_score.toFixed(2)}
                        </span>
                      )}
                    </span>
                  ))}
                </div>
              )}
            </>
          )}

          <div className="mt-3">
            <LinkOut href={item.url} label="OPEN SOURCE ARTICLE" variant="button" />
          </div>
        </div>
      )}

      {item && provenance && (
        <div className="flex items-center gap-3 border-t border-zinc-800 bg-[#0a0a0a] px-2 py-1 text-[10px] text-zinc-600">
          <span>
            {provenance.provider ?? "—"} / {provenance.model ?? "—"}
          </span>
          <span>PROMPT {provenance.prompt_version ?? "—"}</span>
          {provenance.generated_at && <span>SCORED {fmtWibDateTime(provenance.generated_at)}</span>}
          <span className="ml-auto">DESCRIPTIVE · NON-ADVISORY</span>
        </div>
      )}

      {modalOpen && item && provenance && (
        <ProvenanceModal
          title={"AUDIT PROVENANCE // ARTICLE SENTIMENT"}
          provenance={provenance}
          ticker={item.ticker_sentiments[0]?.ticker ?? ""}
          evidenceItemIds={[item.item_id]}
          onClose={() => setModalOpen(false)}
        />
      )}
    </section>
  );
}
