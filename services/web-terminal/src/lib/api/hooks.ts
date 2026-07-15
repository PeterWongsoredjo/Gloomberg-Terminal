"use client";

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type Query,
} from "@tanstack/react-query";

import { ApiError, fetchEnvelope, postForRunId } from "@/lib/api/client";
import type {
  DataTelemetry,
  InsightPanelData,
  LiveTapePage,
  MarketState,
  NewsFeedPage,
  RunReasoningTrace,
  RunStatusData,
} from "@/lib/types/api";
import type { SessionPhase, ViewEnvelope } from "@/lib/types/envelope";

/* Server-state hooks, one per endpoint, read-only and never optimistic. */

const FROZEN_PHASES: SessionPhase[] = ["SESSION_BREAK", "POST_TRADING", "CLOSED"];

const TERMINAL_RUN_STATUSES = new Set(["SUCCEEDED", "SUCCESS", "COMPLETED", "DEGRADED", "ABORTED", "FAILED"]);

type EnvelopeQuery<T> = Query<ViewEnvelope<T>, Error>;

function sessionAwareInterval<T>(liveMs: number, frozenMs: number) {
  return (query: EnvelopeQuery<T>): number => {
    const phase = query.state.data?.marketState;
    return phase !== undefined && FROZEN_PHASES.includes(phase) ? frozenMs : liveMs;
  };
}

export function useMarketState() {
  return useQuery({
    queryKey: ["market-state"],
    queryFn: () => fetchEnvelope<MarketState>("/market/state"),
    refetchInterval: sessionAwareInterval<MarketState>(30_000, 120_000),
  });
}

/** REST snapshot of the tape, the fallback when the stream is down. */
export function useTapeSnapshot() {
  return useQuery({
    queryKey: ["tape"],
    queryFn: () => fetchEnvelope<LiveTapePage>("/tape"),
    refetchInterval: sessionAwareInterval<LiveTapePage>(60_000, 300_000),
  });
}

export function useNewsFeed() {
  return useInfiniteQuery({
    queryKey: ["news"],
    queryFn: ({ pageParam }) =>
      fetchEnvelope<NewsFeedPage>(pageParam ? `/news?cursor=${encodeURIComponent(pageParam)}` : "/news"),
    initialPageParam: "",
    getNextPageParam: (last) => last.data.next_cursor ?? undefined,
    refetchInterval: 60_000,
  });
}

/** The insight for one ticker, a 404 is an honest empty, not an error. */
export function useInsight(ticker: string) {
  return useQuery({
    queryKey: ["insight", ticker],
    queryFn: async (): Promise<ViewEnvelope<InsightPanelData> | null> => {
      try {
        return await fetchEnvelope<InsightPanelData>(`/insights/${ticker}`);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    refetchInterval: 120_000,
  });
}

export function useTelemetry() {
  return useQuery({
    queryKey: ["telemetry"],
    queryFn: () => fetchEnvelope<DataTelemetry>("/telemetry"),
    refetchInterval: 60_000,
  });
}

export function useRunTrace(runId: string | null) {
  return useQuery({
    queryKey: ["run-trace", runId],
    queryFn: () => fetchEnvelope<RunReasoningTrace>(`/runs/${runId}/trace`),
    enabled: runId !== null,
  });
}

/** Polls a dispatched run until it reaches a terminal status. */
export function useRunStatus(runId: string | null) {
  return useQuery({
    queryKey: ["run-status", runId],
    queryFn: () => fetchEnvelope<RunStatusData>(`/runs/${runId}`),
    enabled: runId !== null,
    refetchInterval: (query: EnvelopeQuery<RunStatusData>) => {
      const status = query.state.data?.data.status?.toUpperCase();
      return status !== undefined && TERMINAL_RUN_STATUSES.has(status) ? false : 2_500;
    },
  });
}

export function isTerminalRunStatus(status: string | undefined): boolean {
  return status !== undefined && TERMINAL_RUN_STATUSES.has(status.toUpperCase());
}

/** Dispatches an on-demand insight run for one ticker. */
export function useInsightRefresh(ticker: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => postForRunId(`/insights/${ticker}/refresh`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["run-status"] });
    },
  });
}
