# web-terminal

The Gloomberg Terminal frontend: a single-screen, Bloomberg-style Next.js app over the
serving API. Read-only and non-advisory; every rendered number arrives inside a CT-011
envelope and travels with its `data_as_of` and quality flags.

## Layout

- **Row 1** — command input (`> TICKER <GO>`), market state + WIB clock, IHSG level.
- **Column A (25%)** — scraped news rail from `GET /news`, headlines colored by the
  first tagged ticker's AI sentiment, infinite scroll, click selects the ticker.
- **Column B top** — live tape watchlist over `WS /tape/stream` (REST `/tape`
  fallback) beside a TradingView embed (external, delayed 15m, labeled) with a stat
  strip from our own Gold tape row.
- **Column B bottom** — GenAI insight from `GET /insights/{ticker}` with signals,
  contradictions, provenance footer, on-demand refresh (`POST /insights/{ticker}/refresh`
  then run polling), and the audit modal (`GET /runs/{run_id}/trace` + evidence headlines).
- **Row 3** — telemetry ribbon from `GET /telemetry` plus the stream state dot.

## Structure

- `src/lib/types/` — wire types; `envelope.ts` holds the single unwrap guard that turns
  `Envelope<T>` into the `ViewEnvelope<T>` every component consumes.
- `src/lib/api/` — fetch client (problem+json aware) and TanStack Query hooks,
  session-aware polling intervals.
- `src/lib/stream/` — the tape WebSocket state machine
  (CONNECTING → LIVE/FROZEN → RESYNCING/RECONNECTING → OFFLINE), seq-gap resync.
- `src/components/terminal/` — one file per panel.

## Run

```sh
npm install
cp .env.example .env.local   # defaults target http://127.0.0.1:8000/api/v1
npm run dev                  # backend-api must be running for live data
```

`npm run lint`, `npx tsc --noEmit`, and `npm run build` are the gates.
