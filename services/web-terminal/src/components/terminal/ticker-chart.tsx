"use client";

import { fmtIdrScale, fmtInt, fmtPct, fmtShares } from "@/lib/format";
import { signClass } from "@/lib/palette";
import type { LiveTapeRow } from "@/lib/types/api";

/* Column B, the TradingView embed plus our own Gold stat strip. */

interface TickerChartProps {
  ticker: string;
  row: LiveTapeRow | null;
}

function tradingViewSrc(ticker: string): string {
  const params = new URLSearchParams({
    symbol: `IDX:${ticker}`,
    interval: "D",
    theme: "dark",
    style: "1",
    locale: "en",
    hide_side_toolbar: "1",
    allow_symbol_change: "0",
    save_image: "0",
    withdateranges: "1",
  });
  return `https://s.tradingview.com/widgetembed/?${params.toString()}`;
}

export function TickerChart({ ticker, row }: TickerChartProps) {
  const stats: [string, string, string][] = [
    ["LAST", fmtInt(row?.last_idr ?? null), "text-zinc-200"],
    ["PREV", fmtInt(row?.prev_idr ?? null), "text-zinc-200"],
    ["CHG%", fmtPct(row?.change_pct ?? null), signClass(row?.change_pct)],
    ["VOL", fmtShares(row?.volume_shares ?? null), "text-zinc-200"],
    ["VAL", fmtShares(row?.value_idr ?? null), "text-zinc-200"],
    ["NET FGN", fmtIdrScale(row?.net_foreign_idr ?? null), signClass(row?.net_foreign_idr)],
  ];

  return (
    <section className="flex min-h-0 flex-col bg-[#0a0a0a]">
      <div className="flex items-center justify-between border-b border-zinc-800 bg-[#121212] px-2 py-1">
        <div className="flex items-center gap-3">
          <span className="text-zinc-200">{`IDX:${ticker}`}</span>
          <span className="border border-zinc-700 px-1.5 py-0.5 text-[10px] tracking-wide text-zinc-500">
            TRADINGVIEW · DELAYED 15M
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="tabular-nums text-zinc-200">{fmtInt(row?.last_idr ?? null)}</span>
          <span className={`tabular-nums ${signClass(row?.change_pct)}`}>
            {fmtPct(row?.change_pct ?? null)}%
          </span>
        </div>
      </div>

      <div className="min-h-0 flex-1">
        <iframe
          key={ticker}
          src={tradingViewSrc(ticker)}
          title={`TradingView chart for IDX:${ticker}`}
          className="h-full w-full border-0"
          allow="fullscreen"
        />
      </div>

      {/* the strip below is our Gold data, not TradingView */}
      <div className="grid grid-cols-6 border-t border-zinc-800 bg-[#121212] text-zinc-400">
        {stats.map(([label, value, valueClass]) => (
          <div key={label} className="border-r border-zinc-800 px-2 py-1 last:border-r-0">
            <div className="text-zinc-600">{label}</div>
            <div className={`tabular-nums ${valueClass}`}>{value}</div>
          </div>
        ))}
      </div>
    </section>
  );
}
