"use client";

import { useEffect, useRef, useState } from "react";

import { tapeStreamUrl } from "@/lib/api/client";
import type { LiveTapeRow } from "@/lib/types/api";
import type { QualityFlag, SessionPhase } from "@/lib/types/envelope";

/* The Live Tape WebSocket client and its connection state machine. */

export type StreamStatus =
  | "CONNECTING"
  | "LIVE"
  | "RESYNCING"
  | "FROZEN"
  | "RECONNECTING"
  | "OFFLINE";

export interface TapeStream {
  rows: LiveTapeRow[];
  status: StreamStatus;
  asOf: string | null;
  fresh: boolean;
  marketState: SessionPhase | null;
  flags: QualityFlag[];
}

interface FrameHeader {
  data_as_of: string;
  freshness_slo_met: boolean;
  market_state: SessionPhase;
  quality_flags: QualityFlag[];
}

interface TapeFrame {
  type: "snapshot" | "delta" | "market_state" | "heartbeat" | "error";
  envelope?: FrameHeader;
  rows?: LiveTapeRow[];
  changed?: LiveTapeRow[];
  state?: SessionPhase;
  seq?: number;
}

const FROZEN_PHASES: SessionPhase[] = ["SESSION_BREAK", "POST_TRADING", "CLOSED"];
const MAX_BACKOFF_MS = 30_000;
const OFFLINE_AFTER_ATTEMPTS = 4;

function liveness(phase: SessionPhase | null): StreamStatus {
  return phase !== null && FROZEN_PHASES.includes(phase) ? "FROZEN" : "LIVE";
}

/** Streams the tape, resyncing on sequence gaps, backing off on drops. */
export function useTapeStream(): TapeStream {
  const [stream, setStream] = useState<TapeStream>({
    rows: [],
    status: "CONNECTING",
    asOf: null,
    fresh: true,
    marketState: null,
    flags: [],
  });

  const rowsRef = useRef<Map<number, LiveTapeRow>>(new Map());
  const seqRef = useRef<number | null>(null);
  const attemptsRef = useRef(0);
  const resyncRef = useRef(false);

  useEffect(() => {
    // per effect flag so a torn down instance never reconnects
    let stopped = false;
    let socket: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const publish = (partial: Partial<TapeStream>) => {
      setStream((prev) => ({
        ...prev,
        rows: sortedRows(rowsRef.current),
        ...partial,
      }));
    };

    const applyHeader = (header: FrameHeader | undefined): Partial<TapeStream> => {
      if (!header) return {};
      return {
        asOf: header.data_as_of,
        fresh: header.freshness_slo_met,
        marketState: header.market_state,
        flags: header.quality_flags,
        status: liveness(header.market_state),
      };
    };

    const resync = () => {
      seqRef.current = null;
      resyncRef.current = true;
      publish({ status: "RESYNCING" });
      socket?.close();
    };

    const handleFrame = (frame: TapeFrame) => {
      if (frame.type === "error") {
        return;
      }
      if (frame.seq !== undefined) {
        const expected = seqRef.current === null ? frame.seq : seqRef.current + 1;
        if (frame.type !== "snapshot" && frame.seq !== expected) {
          resync();
          return;
        }
        seqRef.current = frame.seq;
      }
      if (frame.type === "snapshot") {
        rowsRef.current = new Map((frame.rows ?? []).map((r) => [r.security_id, r]));
        publish(applyHeader(frame.envelope));
      } else if (frame.type === "delta") {
        for (const row of frame.changed ?? []) {
          rowsRef.current.set(row.security_id, row);
        }
        publish(applyHeader(frame.envelope));
      } else if (frame.type === "market_state") {
        const phase = frame.state ?? frame.envelope?.market_state ?? null;
        publish({ ...applyHeader(frame.envelope), status: liveness(phase) });
      }
    };

    const connect = () => {
      if (stopped) return;
      socket = new WebSocket(tapeStreamUrl());

      socket.onopen = () => {
        attemptsRef.current = 0;
      };

      socket.onmessage = (event) => {
        try {
          handleFrame(JSON.parse(event.data as string) as TapeFrame);
        } catch {
          resync();
        }
      };

      socket.onclose = () => {
        if (stopped) return;
        seqRef.current = null;
        if (resyncRef.current) {
          resyncRef.current = false;
          retryTimer = setTimeout(connect, 100);
          return;
        }
        attemptsRef.current += 1;
        publish({
          status: attemptsRef.current >= OFFLINE_AFTER_ATTEMPTS ? "OFFLINE" : "RECONNECTING",
        });
        const backoff = Math.min(1000 * 2 ** attemptsRef.current, MAX_BACKOFF_MS);
        const jitter = Math.random() * 500;
        retryTimer = setTimeout(connect, backoff + jitter);
      };
    };

    connect();

    return () => {
      stopped = true;
      if (retryTimer) clearTimeout(retryTimer);
      socket?.close();
    };
  }, []);

  return stream;
}

function sortedRows(map: Map<number, LiveTapeRow>): LiveTapeRow[] {
  return [...map.values()].sort((a, b) => a.ticker.localeCompare(b.ticker));
}
