/* Shared terminal color classes so meaning stays consistent everywhere. */

export const UP_CLASS = "text-[#00FF66]";
export const DOWN_CLASS = "text-[#FF3333]";
export const FLAT_CLASS = "text-zinc-500";
export const WARN_CLASS = "text-[#FBBF24]";
export const LINK_CLASS = "text-[#4DA6FF]";

/** Green above zero, red below, muted when unknown. */
export function signClass(value: number | null | undefined): string {
  if (value === null || value === undefined) return FLAT_CLASS;
  return value >= 0 ? UP_CLASS : DOWN_CLASS;
}
