"use client";

import { useCallback, useRef, useState } from "react";
import { Newspaper } from "lucide-react";

import { feedRows, useNewsFeed } from "@/lib/api/hooks";
import { fmtWibTime } from "@/lib/format";
import {
  CORP_ACTION_CLASS,
  LINK_CLASS,
  UNSCORED_CLASS,
  WARN_CLASS,
  labelClass,
  sentimentGlyph,
} from "@/lib/palette";
import type { NewsItem } from "@/lib/types/api";

import { PanelStatus } from "./panel-status";
import { ProvenanceModal } from "./provenance-modal";

/* Column A, the scraped news rail that keeps piling headlines. */

interface NewsFeedProps {
  onSelectTicker: (ticker: string) => void;
  onSelectArticle: (item: NewsItem) => void;
  selectedItemId: string | null;
}

function articleClass(label: string | null, stale: boolean): string {
  return stale ? WARN_CLASS : labelClass(label);
}

function primaryTicker(item: NewsItem): string | null {
  const scored = item.ticker_sentiments;
  const lead = scored.find((t) => t.relevance === "PRIMARY") ?? scored[0];
  return lead?.ticker ?? item.tickers[0] ?? null;
}

function counts(items: NewsItem[]) {
  const tally = { pos: 0, neu: 0, neg: 0 };
  for (const n of items) {
    if (n.sentiment_label === "BULLISH") tally.pos += 1;
    else if (n.sentiment_label === "BEARISH") tally.neg += 1;
    else if (n.sentiment_label === "NEUTRAL") tally.neu += 1;
  }
  return tally;
}

export function NewsFeed({ onSelectTicker, onSelectArticle, selectedItemId }: NewsFeedProps) {
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
  const items = feedRows(feed.data?.pages);
  const stale = pages.length > 0 && !pages[0].fresh;
  const tally = counts(items);
  const unscored = items.length - tally.pos - tally.neu - tally.neg;

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
          const color = articleClass(n.sentiment_label, stale);
          const auditable = n.sentiment_provenance !== null;
          const primary = primaryTicker(n);
          const pinned = n.item_id === selectedItemId;
          return (
            <div
              key={n.item_id}
              className={`border-b border-zinc-900 px-2 py-1.5 hover:bg-zinc-900/40 ${
                pinned ? "border-l-2 border-l-[#00FF66] bg-zinc-900/60" : ""
              }`}
            >
              <div className="flex items-center justify-between text-[10px]">
                <div className="flex min-w-0 flex-1 items-center gap-1 truncate">
                  {n.item_type === "CORPORATE_ACTION" ? (
                    <span className={CORP_ACTION_CLASS}>CORP ACTION</span>
                  ) : (
                    <span className={LINK_CLASS}>{`[${n.source}]`}</span>
                  )}
                  <span className="text-zinc-500">{fmtWibTime(n.published_at)}</span>
                  {/* one chip per issuer, each tinted by its own read and clickable on its own */}
                  {n.ticker_sentiments.map((t) => (
                    <button
                      key={t.ticker}
                      onClick={() => onSelectTicker(t.ticker)}
                      title={
                        t.sentiment_label
                          ? `${t.ticker} ${t.sentiment_label} ${t.sentiment_score?.toFixed(2) ?? ""} (${t.relevance})`
                          : `${t.ticker} not scored yet`
                      }
                      className={`shrink-0 hover:brightness-125 ${labelClass(t.sentiment_label)}`}
                    >
                      {t.ticker}
                    </button>
                  ))}
                </div>
                {auditable ? (
                  <button
                    onClick={() => setAuditItem(n)}
                    aria-label={`Audit AI sentiment provenance for ${n.title}`}
                    className={`shrink-0 tabular-nums ${color} underline decoration-dotted underline-offset-2 hover:brightness-125`}
                  >
                    {sentimentGlyph(n.sentiment_label, n.sentiment_score)}
                  </button>
                ) : (
                  <span className={`shrink-0 tabular-nums ${UNSCORED_CLASS}`}>
                    {sentimentGlyph(n.sentiment_label, n.sentiment_score)}
                  </span>
                )}
              </div>
              {/* one click pins the article and picks its lead issuer */}
              <button
                onClick={() => {
                  onSelectArticle(n);
                  if (primary) onSelectTicker(primary);
                }}
                className="block w-full text-left"
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
        INGESTED {items.length} · POS {tally.pos} · NEU {tally.neu} · NEG {tally.neg} · UNSCORED{" "}
        {unscored}
        {pages[0] && <span className="ml-2 text-zinc-600">AS OF {fmtWibTime(pages[0].asOf)}</span>}
      </div>

      {auditItem?.sentiment_provenance && (
        <ProvenanceModal
          title={`AUDIT PROVENANCE // ARTICLE SENTIMENT`}
          provenance={auditItem.sentiment_provenance}
          ticker={auditItem.ticker_sentiments[0]?.ticker ?? ""}
          evidenceItemIds={[auditItem.item_id]}
          onClose={() => setAuditItem(null)}
        />
      )}
    </section>
  );
}
