"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import { Radio } from "lucide-react";

import { fmtWibDateTime } from "@/lib/format";
import { signClass, UP_CLASS } from "@/lib/palette";
import type { IndexLevel } from "@/lib/types/api";
import type { SessionPhase } from "@/lib/types/envelope";

/* Row one, the command input plus session strip. */

interface SessionHeaderProps {
  selectedTicker: string;
  onSelectTicker: (ticker: string) => void;
  marketState: SessionPhase | null;
  ihsg: IndexLevel | null;
}

const LIVE_PHASES: SessionPhase[] = ["SESSION_1", "SESSION_2", "PRE_CLOSING", "RANDOM_CLOSING"];

function ihsgChangePct(ihsg: IndexLevel): number | null {
  if (ihsg.change === null) return null;
  const previous = ihsg.level - ihsg.change;
  return previous === 0 ? null : ihsg.change / previous;
}

export function SessionHeader({ selectedTicker, onSelectTicker, marketState, ihsg }: SessionHeaderProps) {
  const [command, setCommand] = useState("");
  const [clock, setClock] = useState<string | null>(null);

  useEffect(() => {
    const tick = () => setClock(fmtWibDateTime(new Date()));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, []);

  const submit = () => {
    const ticker = command.trim().toUpperCase();
    if (ticker) onSelectTicker(ticker);
    setCommand("");
  };

  const isLive = marketState !== null && LIVE_PHASES.includes(marketState);
  const changePct = ihsg ? ihsgChangePct(ihsg) : null;

  return (
    <header className="flex h-[5vh] min-h-9 items-center justify-between border-b border-zinc-800 bg-[#0a0a0a] px-2">
      <div className="flex items-center gap-2">
        <Image
          src="/pw-squared.png"
          alt="Gloomberg Terminal"
          width={20}
          height={20}
          loading="eager"
          className="h-5 w-5 shrink-0"
        />
        <div className="flex items-center border border-zinc-700 bg-black px-2 py-0.5 focus-within:border-[#00FF66]">
          <span className="text-[#00FF66]">{"> "}</span>
          <input
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            placeholder={selectedTicker}
            aria-label="Ticker command input"
            className="w-20 bg-transparent uppercase text-[#00FF66] placeholder-zinc-600 outline-none"
          />
          <span className="text-zinc-400">{"<GO>"}</span>
          <span className="ml-0.5 animate-pulse text-[#00FF66]">_</span>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <span className="flex items-center gap-1.5">
          <Radio className={`h-3 w-3 ${isLive ? `animate-pulse ${UP_CLASS}` : "text-zinc-500"}`} />
          <span className={isLive ? `animate-pulse ${UP_CLASS}` : "text-zinc-500"}>
            MARKET STATE: {marketState ?? "—"}
          </span>
        </span>
        {/* suppressHydrationWarning because the clock only exists client-side */}
        <span className="text-zinc-500" suppressHydrationWarning>
          {clock ?? ""}
        </span>
      </div>

      <div className="hidden items-center gap-3 md:flex">
        <span>
          IHSG{" "}
          {ihsg ? (
            <>
              <span className="tabular-nums text-zinc-300">{ihsg.level.toFixed(2)}</span>{" "}
              <span className={`tabular-nums ${signClass(changePct)}`}>
                {changePct === null
                  ? "—"
                  : `${changePct >= 0 ? "+" : ""}${(changePct * 100).toFixed(2)}%`}
              </span>
            </>
          ) : (
            <span className="text-zinc-600">—</span>
          )}
        </span>
      </div>
    </header>
  );
}
