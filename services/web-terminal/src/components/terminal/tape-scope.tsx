"use client";

import { ChevronDown } from "lucide-react";

/* The tape's scope picker, the whole board or just the curated names. */

export type TapeScope = "ALL" | "UNIVERSE";

interface TapeScopeSelectProps {
  value: TapeScope;
  onChange: (scope: TapeScope) => void;
  allCount: number;
  universeCount: number;
}

export function TapeScopeSelect({ value, onChange, allCount, universeCount }: TapeScopeSelectProps) {
  return (
    <div className="relative flex items-center">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as TapeScope)}
        aria-label="Tape scope"
        /* color-scheme dark keeps the native option list on the terminal palette */
        className="cursor-pointer appearance-none border border-zinc-700 bg-black py-0 pr-5 pl-1.5 uppercase tracking-widest text-zinc-300 outline-none [color-scheme:dark] hover:border-zinc-500 focus:border-[#00FF66]"
      >
        <option value="ALL">ALL ({allCount})</option>
        <option value="UNIVERSE">UNIVERSE ({universeCount})</option>
      </select>
      <ChevronDown className="pointer-events-none absolute right-1 h-3 w-3 text-zinc-500" />
    </div>
  );
}
