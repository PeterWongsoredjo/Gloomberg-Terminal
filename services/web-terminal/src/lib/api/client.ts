import { unwrapEnvelope, type ViewEnvelope } from "@/lib/types/envelope";

/* Serving API access, the only place a request leaves the browser. */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000/api/v1";

export const API_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN ?? "";

export const LANGFUSE_HOST =
  process.env.NEXT_PUBLIC_LANGFUSE_HOST ?? "https://cloud.langfuse.com";

/** RFC 9457 problem body the API sends on every error. */
export interface Problem {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
}

/** A non-200 response, carrying the problem body when present. */
export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem | null;

  constructor(status: number, problem: Problem | null) {
    super(problem?.detail ?? problem?.title ?? `request failed with ${status}`);
    this.status = status;
    this.problem = problem;
  }
}

function authHeaders(): HeadersInit {
  return API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {};
}

async function problemOf(response: Response): Promise<Problem | null> {
  try {
    return (await response.json()) as Problem;
  } catch {
    return null;
  }
}

/** GETs one endpoint and unwraps its envelope, throws ApiError otherwise. */
export async function fetchEnvelope<TData>(path: string): Promise<ViewEnvelope<TData>> {
  const response = await fetch(`${API_BASE}${path}`, { headers: authHeaders() });
  if (!response.ok) {
    throw new ApiError(response.status, await problemOf(response));
  }
  return unwrapEnvelope<TData>(await response.json());
}

/** The tape stream address, a public read needing no token. */
export function tapeStreamUrl(): string {
  return `${API_BASE.replace(/^http/, "ws")}/tape/stream`;
}
