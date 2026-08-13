import { AlertTriangle } from "lucide-react";

/* The shared loading, empty, and error faces every panel resolves to. */

export interface PanelStatusProps {
  state: "LOADING" | "EMPTY" | "ERROR";
  message?: string;
}

export function PanelStatus({ state, message }: PanelStatusProps) {
  if (state === "LOADING") {
    return (
      <div className="flex flex-1 items-center justify-center p-4 text-zinc-600">
        <span className="animate-pulse tracking-widest">LOADING…</span>
      </div>
    );
  }
  if (state === "ERROR") {
    return (
      <div className="flex flex-1 items-center justify-center p-4">
        <div className="flex items-center gap-2 border border-[#FF3333]/50 bg-[#FF3333]/10 px-3 py-2 text-[#FF3333]">
          <AlertTriangle className="h-3 w-3 shrink-0" />
          <span>{message ?? "Request failed. Retrying on the next cycle."}</span>
        </div>
      </div>
    );
  }
  return (
    <div className="flex flex-1 items-center justify-center p-4 text-center text-zinc-600">
      <span>{message ?? "No data for this view yet."}</span>
    </div>
  );
}
