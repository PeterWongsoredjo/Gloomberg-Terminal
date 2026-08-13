import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CuratedUniverse, LiveTapeRow } from "@/lib/types/api";

import { LiveTape } from "./live-tape";

function tapeRow(ticker: string, overrides: Partial<LiveTapeRow> = {}): LiveTapeRow {
  return {
    security_id: ticker.charCodeAt(0) * 1000 + ticker.charCodeAt(3),
    ticker,
    board: "RG",
    is_fca: false,
    special_notation: [],
    last_idr: 1000,
    prev_idr: 1000,
    change_idr: 0,
    change_pct: 0,
    at_price_limit: "NONE",
    volume_shares: 100,
    value_idr: 100_000,
    net_foreign_idr: 0,
    price_series_integrity: "CLEAN",
    dq_flags: [],
    ...overrides,
  };
}

const CURATED = tapeRow("BBCA", { change_pct: 0.012 });
const ALSO_CURATED = tapeRow("TLKM", { change_pct: -0.004 });
const OUTSIDER = tapeRow("ZYRX", { change_pct: 0.25 });

function mockUniverse(tickers: string[]) {
  const data: CuratedUniverse = { tickers };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => ({
        api_version: "v1",
        served_at: "2026-07-10T10:00:00+00:00",
        data_as_of: "2026-07-10T03:00:00+00:00",
        freshness_slo_met: true,
        market_state: "SESSION_1",
        quality_flags: tickers.length === 0 ? ["MISSING_UPSTREAM"] : [],
        data,
      }),
    })),
  );
}

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function renderTape(rows: LiveTapeRow[]) {
  return render(
    <LiveTape
      rows={rows}
      loading={false}
      streamStatus="LIVE"
      asOf="2026-07-10T03:00:00+00:00"
      selectedTicker="BBCA"
      onSelectTicker={() => {}}
    />,
    { wrapper: Wrapper },
  );
}

function scopeSelect() {
  return screen.getByLabelText("Tape scope");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("LiveTape scope", () => {
  it("opens on the curated universe and hides everything outside it", async () => {
    mockUniverse(["BBCA", "TLKM"]);
    renderTape([CURATED, ALSO_CURATED, OUTSIDER]);

    expect(await screen.findByText("BBCA")).toBeInTheDocument();
    expect(screen.getByText("TLKM")).toBeInTheDocument();
    expect(screen.queryByText("ZYRX")).not.toBeInTheDocument();
  });

  it("shows the whole board once the scope is ALL", async () => {
    mockUniverse(["BBCA", "TLKM"]);
    renderTape([CURATED, ALSO_CURATED, OUTSIDER]);
    await screen.findByText("BBCA");

    fireEvent.change(scopeSelect(), { target: { value: "ALL" } });

    expect(screen.getByText("ZYRX")).toBeInTheDocument();
    expect(screen.getByText("BBCA")).toBeInTheDocument();
  });

  it("counts each option by what it would actually show", async () => {
    mockUniverse(["BBCA", "TLKM", "ASII"]);
    renderTape([CURATED, ALSO_CURATED, OUTSIDER]);

    // ASII is curated but has no row today, so the option counts two, not three
    expect(await screen.findByRole("option", { name: "UNIVERSE (2)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "ALL (3)" })).toBeInTheDocument();
  });

  it("tallies advancers and decliners over the visible scope only", async () => {
    mockUniverse(["BBCA", "TLKM"]);
    renderTape([CURATED, ALSO_CURATED, OUTSIDER]);
    await screen.findByText("BBCA");

    expect(screen.getByText(/ADV 1 · DECL 1 · UNCH 0/)).toBeInTheDocument();

    fireEvent.change(scopeSelect(), { target: { value: "ALL" } });

    expect(screen.getByText(/ADV 2 · DECL 1 · UNCH 0/)).toBeInTheDocument();
  });

  it("says the curated file is unreadable instead of quietly showing the whole board", async () => {
    mockUniverse([]);
    renderTape([CURATED, OUTSIDER]);

    expect(await screen.findByText(/Curated universe unreadable/)).toBeInTheDocument();
    expect(screen.queryByText("BBCA")).not.toBeInTheDocument();

    fireEvent.change(scopeSelect(), { target: { value: "ALL" } });

    expect(screen.getByText("BBCA")).toBeInTheDocument();
    expect(screen.getByText("ZYRX")).toBeInTheDocument();
  });

  it("keeps the empty-projection message when the tape itself has no rows", async () => {
    mockUniverse(["BBCA"]);
    renderTape([]);

    expect(await screen.findByText(/Tape projection is empty/)).toBeInTheDocument();
  });
});
