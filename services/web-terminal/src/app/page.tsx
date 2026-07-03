import { DataTelemetry } from "@/components/terminal/data-telemetry";
import { InsightPanel } from "@/components/terminal/insight-panel";
import { LiveTape } from "@/components/terminal/live-tape";
import { SentimentMatrix } from "@/components/terminal/sentiment-matrix";

export default function TerminalPage() {
  return (
    <main className="grid flex-1 grid-cols-2 grid-rows-2 gap-2 p-2">
      <LiveTape />
      <SentimentMatrix />
      <InsightPanel />
      <DataTelemetry />
    </main>
  );
}
