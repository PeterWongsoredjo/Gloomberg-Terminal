"use client";

import { useCallback, useRef, useState } from "react";
import { Newspaper } from "lucide-react";

import { useNewsFeed } from "@/lib/api/hooks";
import { fmtWibTime } from "@/lib/format";
import { DOWN_CLASS, FLAT_CLASS, LINK_CLASS, UP_CLASS, WARN_CLASS } from "@/lib/palette";
import type { NewsItem } from "@/lib/types/api";

import { PanelStatus } from "./panel-status";
import { ProvenanceModal } from "./provenance-modal";

/* Column A, the scraped news rail that keeps piling headlines. */

interface NewsFeedProps {
  onSelectTicker: (ticker: string) => void;
}

function sentimentClass(score: number | null, stale: boolean): string {
  if (stale) return WARN_CLASS;
  if (score === null) return "text-zinc-400";
  return score >= 0 ? UP_CLASS : DOWN_CLASS;
}

function sentimentGlyph(score: number | null): string {
  if (score === null) return "·";
  return score >= 0 ? `▲ +${score.toFixed(2)}` : `▼ ${score.toFixed(2)}`;
}

export function NewsFeed({ onSelectTicker }: NewsFeedProps) {
  const feed = useNewsFeed();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [auditItem, setAuditItem] = useState<NewsItem | null>(null);

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el || !feed.hasNextPage || feed.isFetchingNextPage) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 120) {
      feed.fetchNextPage();
    }
  }, [feed]);

  const pages = feed.data?.pages ?? [];
  const items: NewsItem[] = pages.flatMap((page) => page.data.rows);
  const stale = pages.length > 0 && !pages[0].fresh;
  const scored = items.filter((n) => n.sentiment_score !== null);
  const positive = scored.filter((n) => (n.sentiment_score ?? 0) >= 0).length;
  const negative = scored.length - positive;

  return (
    <section className="flex min-h-0 flex-col border-r border-zinc-800">
      <div className="flex items-center justify-between border-b border-zinc-800 bg-[#121212] px-2 py-1 text-zinc-400">
        <span className="flex items-center gap-2 tracking-widest">
          <Newspaper className="h-3 w-3" />
          SCRAPED RAW NEWS FEED
        </span>
        <span className="text-zinc-600">AI-SENT</span>
      </div>

      <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-auto">
        {feed.isPending && <PanelStatus state="LOADING" />}
        {feed.isError && <PanelStatus state="ERROR" message="News feed unreachable. Retrying." />}
        {feed.isSuccess && items.length === 0 && (
          <PanelStatus state="EMPTY" message="No headlines ingested for this window." />
        )}
        {items.map((n) => {
          const color = sentimentClass(n.sentiment_score, stale);
          const clickable = n.tickers.length > 0;
          const auditable = n.sentiment_provenance !== null;
          const select = () => clickable && onSelectTicker(n.tickers[0]);
          return (
            <div
              key={n.item_id}
              className={`border-b border-zinc-900 px-2 py-1.5 ${clickable ? "hover:bg-zinc-900/40" : ""}`}
            >
              <div className="flex items-center justify-between text-[10px]">
                <button
                  onClick={select}
                  disabled={!clickable}
                  className={`flex-1 truncate text-left ${clickable ? "" : "cursor-default"}`}
                >
                  <span className={LINK_CLASS}>{`[${n.source}]`}</span>{" "}
                  <span className="text-zinc-500">{fmtWibTime(n.published_at)}</span>
                  {n.tickers.length > 0 && (
                    <span className="ml-1 text-zinc-600">{n.tickers.join(" ")}</span>
                  )}
                </button>
                {auditable ? (
                  <button
                    onClick={() => setAuditItem(n)}
                    aria-label={`Audit AI sentiment provenance for ${n.tickers[0] ?? "item"}`}
                    className={`tabular-nums ${color} underline decoration-dotted underline-offset-2 hover:brightness-125`}
                  >
                    {sentimentGlyph(n.sentiment_score)}
                  </button>
                ) : (
                  <span className={`tabular-nums ${n.sentiment_score === null ? FLAT_CLASS : color}`}>
                    {sentimentGlyph(n.sentiment_score)}
                  </span>
                )}
              </div>
              <button
                onClick={select}
                disabled={!clickable}
                className={`block w-full text-left ${clickable ? "" : "cursor-default"}`}
              >
                <p className={`mt-0.5 leading-snug ${color}`}>{n.title}</p>
              </button>
            </div>
          );
        })}
        {feed.isFetchingNextPage && (
          <div className="px-2 py-1.5 text-center text-zinc-600">LOADING MORE…</div>
        )}
      </div>

      <div className="border-t border-zinc-800 bg-[#121212] px-2 py-1 text-zinc-500">
        INGESTED {items.length} · POS {positive} · NEG {negative}
        {pages[0] && <span className="ml-2 text-zinc-600">AS OF {fmtWibTime(pages[0].asOf)}</span>}
      </div>

      {auditItem?.sentiment_provenance && (
        <ProvenanceModal
          title={`AUDIT PROVENANCE // NEWS SENTIMENT · ${auditItem.tickers[0] ?? ""}`}
          provenance={auditItem.sentiment_provenance}
          ticker={auditItem.tickers[0] ?? ""}
          evidenceItemIds={auditItem.sentiment_provenance.evidence_item_ids}
          onClose={() => setAuditItem(null)}
        />
      )}
    </section>
  );
}
