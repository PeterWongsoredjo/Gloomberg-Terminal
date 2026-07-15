"use client";

import { fmtIdrScale, fmtInt, fmtPct, fmtWibTime } from "@/lib/format";
import { signClass } from "@/lib/palette";
import type { LiveTapeRow } from "@/lib/types/api";
import type { StreamStatus } from "@/lib/stream/tape-stream";

import { PanelStatus } from "./panel-status";

/* The live tape watchlist, stream first, REST snapshot as fallback. */

interface LiveTapeProps {
  rows: LiveTapeRow[];
  loading: boolean;
  streamStatus: StreamStatus;
  asOf: string | null;
  selectedTicker: string;
  onSelectTicker: (ticker: string) => void;
}

const STATUS_LABEL: Record<StreamStatus, { text: string; className: string }> = {
  CONNECTING: { text: "CONNECTING", className: "text-zinc-500" },
  LIVE: { text: "RT", className: "text-[#00FF66]" },
  RESYNCING: { text: "RESYNC", className: "text-[#FBBF24]" },
  FROZEN: { text: "FROZEN", className: "text-zinc-400" },
  RECONNECTING: { text: "RECONNECTING", className: "text-[#FBBF24]" },
  OFFLINE: { text: "OFFLINE", className: "text-[#FF3333]" },
};

function boardBadge(row: LiveTapeRow) {
  if (row.dq_flags.includes("SUSPENDED")) {
    return <span className="ml-1 bg-[#FF3333]/20 px-1 text-[#FF3333] line-through">[SUSP]</span>;
  }
  if (row.is_fca || row.dq_flags.includes("FCA_PRICING")) {
    return <span className="ml-1 text-[#FBBF24]">[FCA]</span>;
  }
  if (row.price_series_integrity === "ADJUSTMENT_PENDING") {
    return <span className="ml-1 text-[#4DA6FF]">[ADJ-P]</span>;
  }
  return <span className="ml-1 text-zinc-500">[{row.board}]</span>;
}

function limitBadge(row: LiveTapeRow) {
  if (row.at_price_limit === "NONE") return null;
  const cls = row.at_price_limit === "ARA" ? "bg-[#238636]" : "bg-[#DA3633]";
  return <span className={`ml-1 px-1 text-black ${cls}`}>{row.at_price_limit}</span>;
}

export function LiveTape({ rows, loading, streamStatus, asOf, selectedTicker, onSelectTicker }: LiveTapeProps) {
  const status = STATUS_LABEL[streamStatus];
  const advancing = rows.filter((r) => (r.change_pct ?? 0) > 0).length;
  const declining = rows.filter((r) => (r.change_pct ?? 0) < 0).length;
  const unchanged = rows.length - advancing - declining;

  return (
    <section className="flex min-h-0 flex-col border-r border-zinc-800">
      <div className="flex items-center justify-between border-b border-zinc-800 bg-[#121212] px-2 py-1 text-zinc-400">
        <span className="tracking-widest">LIVE TAPE / WATCHLIST</span>
        <span className={status.className}>{status.text}</span>
      </div>

      <div className="grid grid-cols-[1.6fr_1fr_0.9fr_1fr] border-b border-zinc-800 bg-black px-2 py-1 text-zinc-500">
        <span>TICKER</span>
        <span className="text-right">LAST</span>
        <span className="text-right">CHG%</span>
        <span className="text-right">NET FGN</span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {loading && rows.length === 0 && <PanelStatus state="LOADING" />}
        {!loading && rows.length === 0 && (
          <PanelStatus state="EMPTY" message="Tape projection is empty. Waiting for the pipeline." />
        )}
        {rows.map((row, i) => {
          const activeRow = row.ticker === selectedTicker;
          return (
            <button
              key={row.security_id}
              onClick={() => onSelectTicker(row.ticker)}
              className={`grid w-full grid-cols-[1.6fr_1fr_0.9fr_1fr] px-2 py-1 text-left transition-colors ${
                i % 2 === 0 ? "bg-black" : "bg-zinc-950"
              } ${activeRow ? "outline -outline-offset-1 outline-[#00FF66]" : "hover:bg-zinc-900"}`}
            >
              <span className="flex items-center truncate">
                <span className={activeRow ? "text-[#00FF66]" : "text-zinc-200"}>{row.ticker}</span>
                {boardBadge(row)}
                {limitBadge(row)}
              </span>
              <span className="text-right tabular-nums text-zinc-300">{fmtInt(row.last_idr)}</span>
              <span className={`text-right tabular-nums ${signClass(row.change_pct)}`}>
                {fmtPct(row.change_pct)}
              </span>
              <span className={`text-right tabular-nums ${signClass(row.net_foreign_idr)}`}>
                {fmtIdrScale(row.net_foreign_idr)}
              </span>
            </button>
          );
        })}
      </div>

      <div className="border-t border-zinc-800 bg-[#121212] px-2 py-1 text-zinc-500">
        ADV {advancing} · DECL {declining} · UNCH {unchanged}
        {asOf && <span className="ml-2 text-zinc-600">AS OF {fmtWibTime(asOf)}</span>}
      </div>
    </section>
  );
}
