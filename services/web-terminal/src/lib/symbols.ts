
/** IDX publishes the composite as COMPOSITE; IHSG is our canonical id for it. */
const TRADINGVIEW_SYMBOLS: Record<string, string> = {
  IHSG: "IDX:COMPOSITE",
};

/** The TradingView symbol for one of our tickers. */
export function tradingViewSymbol(ticker: string): string {
  return TRADINGVIEW_SYMBOLS[ticker] ?? `IDX:${ticker}`;
}
