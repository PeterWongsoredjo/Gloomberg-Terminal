/* Shared terminal color classes so meaning stays consistent everywhere. */

export const UP_CLASS = "text-[#00FF66]";
export const DOWN_CLASS = "text-[#FF3333]";
export const FLAT_CLASS = "text-zinc-500";
export const WARN_CLASS = "text-[#FBBF24]";
export const LINK_CLASS = "text-[#4DA6FF]";

export const NEUTRAL_CLASS = "text-zinc-300";
export const UNSCORED_CLASS = "text-zinc-600";

export const CORP_ACTION_CLASS =
  "shrink-0 border border-[#4DA6FF] px-1 leading-none tracking-wide text-[#4DA6FF]";

/** Green above zero, red below, muted when unknown. */
export function signClass(value: number | null | undefined): string {
  if (value === null || value === undefined) return FLAT_CLASS;
  return value >= 0 ? UP_CLASS : DOWN_CLASS;
}

/** Colour from the model's own verdict, never from the sign of a score. */
export function labelClass(label: string | null | undefined): string {
  if (label === "BULLISH") return UP_CLASS;
  if (label === "BEARISH") return DOWN_CLASS;
  if (label === "NEUTRAL") return NEUTRAL_CLASS;
  return UNSCORED_CLASS;
}

/** The glyph that goes with a verdict, so colour is never the only cue. */
export function sentimentGlyph(label: string | null | undefined, score: number | null): string {
  if (label === null || label === undefined || score === null) return "·";
  if (label === "BULLISH") return `▲ +${score.toFixed(2)}`;
  if (label === "BEARISH") return `▼ ${score.toFixed(2)}`;
  return `◆ ${score.toFixed(2)}`;
}
