import { describe, expect, it } from "vitest";

import {
  DOWN_CLASS,
  NEUTRAL_CLASS,
  UNSCORED_CLASS,
  UP_CLASS,
  labelClass,
  sentimentGlyph,
} from "./palette";

describe("labelClass", () => {
  it("colours from the verdict, not the sign of the score", () => {
    expect(labelClass("BULLISH")).toBe(UP_CLASS);
    expect(labelClass("BEARISH")).toBe(DOWN_CLASS);
    expect(labelClass("NEUTRAL")).toBe(NEUTRAL_CLASS);
  });

  it("treats an absent or unrecognised verdict as unscored", () => {
    expect(labelClass(null)).toBe(UNSCORED_CLASS);
    expect(labelClass(undefined)).toBe(UNSCORED_CLASS);
    expect(labelClass("bullish")).toBe(UNSCORED_CLASS);
  });
});

describe("sentimentGlyph", () => {
  it("pairs a glyph with the score so colour is never the only cue", () => {
    expect(sentimentGlyph("BULLISH", 0.22)).toBe("▲ +0.22");
    expect(sentimentGlyph("BEARISH", -0.38)).toBe("▼ -0.38");
    expect(sentimentGlyph("NEUTRAL", 0.01)).toBe("◆ 0.01");
  });

  it("falls back to a dot when there is nothing to show", () => {
    expect(sentimentGlyph(null, null)).toBe("·");
    expect(sentimentGlyph("BULLISH", null)).toBe("·");
    expect(sentimentGlyph(null, 0.5)).toBe("·");
  });
});
