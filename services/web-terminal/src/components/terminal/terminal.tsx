"use client";

import { useState } from "react";

import { useMarketState, useTapeSnapshot } from "@/lib/api/hooks";
import { useTapeStream } from "@/lib/stream/tape-stream";

import { InsightPanel } from "./insight-panel";
import { LiveTape } from "./live-tape";
import { NewsFeed } from "./news-feed";
import { SessionHeader } from "./session-header";
import { TelemetryRibbon } from "./telemetry-ribbon";
import { TickerChart } from "./ticker-chart";

/* The terminal shell, selection state and the three column layout. */

const DEFAULT_TICKER = "BBCA";

export function Terminal() {
  const [selectedTicker, setSelectedTicker] = useState(DEFAULT_TICKER);

  const stream = useTapeStream();
  const tapeRest = useTapeSnapshot();
  const market = useMarketState();

  const streaming = stream.rows.length > 0;
  const rows = streaming ? stream.rows : (tapeRest.data?.data.rows ?? []);
  const tapeAsOf = streaming ? stream.asOf : (tapeRest.data?.asOf ?? null);
  const tapeFresh = streaming ? stream.fresh : (tapeRest.data?.fresh ?? true);
  const marketState = stream.marketState ?? market.data?.marketState ?? null;

  const selectTicker = (ticker: string) => setSelectedTicker(ticker.toUpperCase());
  const activeRow = rows.find((r) => r.ticker === selectedTicker) ?? null;

  return (
    <main className="h-screen overflow-hidden bg-black font-mono text-xs text-zinc-300 select-none">
      {!tapeFresh && (
        <div className="fixed top-0 left-0 z-40 w-full border-b border-[#FBBF24] bg-[#FBBF24]/15 py-1 text-center text-[#FBBF24]">
          [DATA STALE — FRESHNESS SLO OUTSIDE BOUNDS, VALUES SHOWN WITH THEIR AS-OF]
        </div>
      )}

      <div className="flex h-full flex-col">
        <SessionHeader
          selectedTicker={selectedTicker}
          onSelectTicker={selectTicker}
          marketState={marketState}
          ihsg={market.data?.data.ihsg ?? null}
        />

        <div className="grid min-h-0 flex-1 grid-cols-[25%_75%]">
          <NewsFeed onSelectTicker={selectTicker} />

          <div className="grid min-h-0 grid-rows-[1.6fr_1fr]">
            <div className="grid min-h-0 grid-cols-[30%_70%] border-b border-zinc-800">
              <LiveTape
                rows={rows}
                loading={tapeRest.isPending && !streaming}
                streamStatus={stream.status}
                asOf={tapeAsOf}
                selectedTicker={selectedTicker}
                onSelectTicker={selectTicker}
              />
              <TickerChart ticker={selectedTicker} row={activeRow} />
            </div>

            <InsightPanel ticker={selectedTicker} />
          </div>
        </div>

        <TelemetryRibbon streamStatus={stream.status} />
      </div>
    </main>
  );
}
