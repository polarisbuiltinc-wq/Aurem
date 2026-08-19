import { describe, it, expect } from "vitest";
import { brandProvider } from "../providerLabel";

// Leak-audit fix (2026-08-19) — GLM 5.2 (and other raw model slugs)
// were visible to regular users via the chat scope badge and the
// live-step floating card footer. Any raw provider string must
// collapse to the single public brand name.
describe("brandProvider", () => {
  it("collapses any raw model/provider slug to ORA", () => {
    expect(brandProvider("glm-5.2")).toBe("ORA");
    expect(brandProvider("z-ai/glm-5.2")).toBe("ORA");
    expect(brandProvider("deepseek-v3-rescue")).toBe("ORA");
    expect(brandProvider("groq-llama-3.3-70b-rescue")).toBe("ORA");
    expect(brandProvider("claude-sonnet-4.5")).toBe("ORA");
    expect(brandProvider("longcat-2.0")).toBe("ORA");
  });

  it("returns empty string for falsy input (no badge shown)", () => {
    expect(brandProvider("")).toBe("");
    expect(brandProvider(null)).toBe("");
    expect(brandProvider(undefined)).toBe("");
  });
});
