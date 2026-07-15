/* Centralized value formatting, components never hand-format numbers. */

const DASH = "—";

const intFormat = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 0 });

const scaleFormat = new Intl.NumberFormat("id-ID", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

/** Integer rupiah with id-ID grouping, null stays a dash, never zero. */
export function fmtInt(value: number | null | undefined): string {
  return value === null || value === undefined ? DASH : intFormat.format(value);
}

/** Signed percent with two decimals from a fractional change. */
export function fmtPct(fraction: number | null | undefined): string {
  if (fraction === null || fraction === undefined) return DASH;
  const pct = fraction * 100;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}`;
}

/** Large rupiah on the Indonesian scale, like 407,15 M. */
export function fmtIdrScale(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  const sign = value < 0 ? "-" : "+";
  const abs = Math.abs(value);
  if (abs >= 1e12) return `${sign}${scaleFormat.format(abs / 1e12)} T`;
  if (abs >= 1e9) return `${sign}${scaleFormat.format(abs / 1e9)} M`;
  if (abs >= 1e6) return `${sign}${scaleFormat.format(abs / 1e6)} jt`;
  if (abs >= 1e3) return `${sign}${scaleFormat.format(abs / 1e3)} rb`;
  return `${sign}${scaleFormat.format(abs)}`;
}

/** Share volume on the same scale without a sign. */
export function fmtShares(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  return fmtIdrScale(value).replace(/^[+-]/, "");
}

const wibTime = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Jakarta",
  hour: "2-digit",
  minute: "2-digit",
});

const wibDateTime = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Jakarta",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

/** HH:mm in WIB from an ISO instant. */
export function fmtWibTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? DASH : wibTime.format(parsed);
}

/** Full WIB timestamp like 2026-07-13 14:22:07 WIB. */
export function fmtWibDateTime(iso: string | Date | null | undefined): string {
  if (!iso) return DASH;
  const parsed = iso instanceof Date ? iso : new Date(iso);
  if (Number.isNaN(parsed.getTime())) return DASH;
  const parts = wibDateTime.formatToParts(parsed);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")}:${get("second")} WIB`;
}
