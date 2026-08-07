import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { NewsFeedPage, NewsItem, NewsItemType } from "@/lib/types/api";

import { NewsFeed } from "./news-feed";

function newsItem(overrides: Partial<NewsItem> & { item_id: string; item_type: NewsItemType }): NewsItem {
  return {
    trade_date: "2026-07-10",
    source: "cnbc_market",
    lang: "id",
    title: "a headline",
    summary: null,
    url: "https://example.test/a",
    published_at: "2026-07-10T02:15:00+00:00",
    tickers: [],
    sentiment_score: null,
    sentiment_label: null,
    sentiment_rationale: null,
    sentiment_drivers: [],
    ticker_sentiments: [],
    sentiment_provenance: null,
    ...overrides,
  };
}

const ARTICLE = newsItem({
  item_id: "cnbc_market:aa11",
  item_type: "ARTICLE",
  title: "BBCA cetak laba bersih naik",
  published_at: "2026-07-10T03:00:00+00:00",
  tickers: ["BBCA"],
  sentiment_score: 0.42,
  sentiment_label: "BULLISH",
  ticker_sentiments: [
    { ticker: "BBCA", sentiment_score: 0.42, sentiment_label: "BULLISH", relevance: "PRIMARY" },
  ],
});

const SCORED_CORP_ACTION = newsItem({
  item_id: "corp_action:idx_ca:999001",
  item_type: "CORPORATE_ACTION",
  source: "idx_corporate_action",
  title: "AADI stock split, effective 10 Jul 2026",
  url: null,
  published_at: "2026-07-10T00:00:00+00:00",
  tickers: ["AADI"],
  sentiment_score: 0.22,
  sentiment_label: "BULLISH",
  ticker_sentiments: [
    { ticker: "AADI", sentiment_score: 0.22, sentiment_label: "BULLISH", relevance: "PRIMARY" },
  ],
});

const UNSCORED_CORP_ACTION = newsItem({
  item_id: "corp_action:idx_ca:82392",
  item_type: "CORPORATE_ACTION",
  source: "idx_corporate_action",
  title: "MFIN delisting, effective 22 Jun 2026",
  url: null,
  published_at: "2026-06-22T00:00:00+00:00",
  tickers: ["MFIN"],
});

function envelopeOf(rows: NewsItem[]) {
  const data: NewsFeedPage = { rows, next_cursor: null };
  return {
    api_version: "v1",
    served_at: "2026-07-10T10:00:00+00:00",
    data_as_of: "2026-07-10T03:00:00+00:00",
    freshness_slo_met: true,
    market_state: "SESSION_1",
    quality_flags: [],
    data,
  };
}

function mockFeed(rows: NewsItem[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, json: async () => envelopeOf(rows) })),
  );
}

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function renderFeed() {
  return render(
    <NewsFeed onSelectTicker={() => {}} onSelectArticle={() => {}} selectedItemId={null} />,
    { wrapper: Wrapper },
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("NewsFeed corporate actions", () => {
  it("labels a corporate action by type and still shows its sentiment", async () => {
    mockFeed([SCORED_CORP_ACTION]);
    renderFeed();

    expect(await screen.findByText("CORP ACTION")).toBeInTheDocument();
    expect(screen.getByText("AADI stock split, effective 10 Jul 2026")).toBeInTheDocument();
    expect(screen.getByText("▲ +0.22")).toBeInTheDocument();
    expect(screen.queryByText("[idx_corporate_action]")).not.toBeInTheDocument();
  });

  it("gives an article no corporate action badge", async () => {
    mockFeed([ARTICLE]);
    renderFeed();

    expect(await screen.findByText("[cnbc_market]")).toBeInTheDocument();
    expect(screen.queryByText("CORP ACTION")).not.toBeInTheDocument();
    expect(screen.getByText("▲ +0.42")).toBeInTheDocument();
  });

  it("keeps the badge on a corporate action the model has not scored yet", async () => {
    mockFeed([UNSCORED_CORP_ACTION]);
    renderFeed();

    expect(await screen.findByText("CORP ACTION")).toBeInTheDocument();
    expect(screen.getByText("·")).toBeInTheDocument();
    expect(screen.queryByText(/▲|▼|◆/)).not.toBeInTheDocument();
  });

  it("counts a scored corporate action in the feed tally", async () => {
    mockFeed([ARTICLE, SCORED_CORP_ACTION, UNSCORED_CORP_ACTION]);
    renderFeed();

    expect(await screen.findByText(/INGESTED 3/)).toBeInTheDocument();
    expect(screen.getByText(/POS 2/)).toBeInTheDocument();
    expect(screen.getByText(/UNSCORED\s+1/)).toBeInTheDocument();
  });

  it("renders both kinds interleaved in one feed", async () => {
    mockFeed([ARTICLE, SCORED_CORP_ACTION]);
    renderFeed();

    expect(await screen.findByText("CORP ACTION")).toBeInTheDocument();
    expect(screen.getByText("[cnbc_market]")).toBeInTheDocument();
    expect(screen.getByText("AADI")).toBeInTheDocument();
    expect(screen.getByText("BBCA")).toBeInTheDocument();
  });
});
