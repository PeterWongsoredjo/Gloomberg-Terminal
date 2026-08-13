"use client";

import { useMemo, useState } from "react";

import { useUniverse } from "@/lib/api/hooks";
import { fmtIdrScale, fmtInt, fmtPct, fmtWibTime } from "@/lib/format";
import { signClass } from "@/lib/palette";
import type { LiveTapeRow } from "@/lib/types/api";
import type { StreamStatus } from "@/lib/stream/tape-stream";

import { PanelStatus, type PanelStatusProps } from "./panel-status";
import { TapeScopeSelect, type TapeScope } from "./tape-scope";

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

interface EmptyInput {
  loading: boolean;
  tapeRows: number;
  visibleRows: number;
  curatedSize: number;
  universeLoading: boolean;
}

/** Why the list is empty, or null when there are rows to draw. */
function emptyStatus(input: EmptyInput): PanelStatusProps | null {
  if (input.visibleRows > 0) return null;
  if (input.loading) return { state: "LOADING" };
  if (input.tapeRows === 0) {
    return { state: "EMPTY", message: "Tape projection is empty. Waiting for the pipeline." };
  }
  // rows exist but none survived, so the scope is the universe
  if (input.universeLoading) return { state: "LOADING" };
  if (input.curatedSize === 0) {
    return { state: "ERROR", message: "Curated universe unreadable. Switch to ALL for the full tape." };
  }
  return { state: "EMPTY", message: "No curated ticker has a row on this tape yet." };
}

export function LiveTape({ rows, loading, streamStatus, asOf, selectedTicker, onSelectTicker }: LiveTapeProps) {
  const [scope, setScope] = useState<TapeScope>("UNIVERSE");
  const universe = useUniverse();

  const curated = useMemo(
    () => new Set(universe.data?.data.tickers ?? []),
    [universe.data],
  );
  const curatedRows = useMemo(() => rows.filter((r) => curated.has(r.ticker)), [rows, curated]);
  const visible = scope === "ALL" ? rows : curatedRows;

  const status = STATUS_LABEL[streamStatus];
  const advancing = visible.filter((r) => (r.change_pct ?? 0) > 0).length;
  const declining = visible.filter((r) => (r.change_pct ?? 0) < 0).length;
  const unchanged = visible.length - advancing - declining;
  const empty = emptyStatus({
    loading: loading && rows.length === 0,
    tapeRows: rows.length,
    visibleRows: visible.length,
    curatedSize: curated.size,
    universeLoading: universe.isPending,
  });

  return (
    <section className="flex min-h-0 flex-col border-r border-zinc-800">
      <div className="flex items-center justify-between gap-1 border-b border-zinc-800 bg-[#121212] px-2 py-1 text-zinc-400">
        <span className="min-w-0 truncate tracking-widest">LIVE TAPE / WATCHLIST</span>
        <TapeScopeSelect
          value={scope}
          onChange={setScope}
          allCount={rows.length}
          universeCount={curatedRows.length}
        />
        <span className={status.className}>{status.text}</span>
      </div>

      <div className="grid grid-cols-[1.6fr_1fr_0.9fr_1fr] border-b border-zinc-800 bg-black px-2 py-1 text-zinc-500">
        <span>TICKER</span>
        <span className="text-right">LAST</span>
        <span className="text-right">CHG%</span>
        <span className="text-right">NET FGN</span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {empty && <PanelStatus state={empty.state} message={empty.message} />}
        {visible.map((row, i) => {
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
