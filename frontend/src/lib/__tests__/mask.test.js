/**
 * lib/__tests__/mask.test.js — Iter 388w · reusable masking utilities.
 *
 * Contract-level tests for maskEmail() and maskId() so downstream
 * consumers (DangerZone, future Stripe/GitHub ID surfaces) can
 * depend on stable behaviour.
 */
import { describe, it, expect } from "vitest";
import { maskEmail, maskId } from "../mask";

describe("maskEmail", () => {
  it("hides local part except last 2 chars, keeps domain verbatim", () => {
    expect(maskEmail("teji.ss1986@gmail.com")).toBe("*********86@gmail.com");
    expect(maskEmail("test@aurem.dev")).toBe("**st@aurem.dev");
  });

  it("respects reveal option", () => {
    expect(maskEmail("foobar@x.com", { reveal: 3 })).toBe("***bar@x.com");
    expect(maskEmail("foobar@x.com", { reveal: 1 })).toBe("*****r@x.com");
  });

  it("uses minMask fallback for local parts <= reveal", () => {
    expect(maskEmail("ab@x.com", { reveal: 2 })).toBe("****@x.com");
    expect(maskEmail("ab@x.com", { reveal: 2, minMask: 8 })).toBe("********@x.com");
    expect(maskEmail("a@x.com")).toBe("****@x.com");
  });

  it("empty / null / undefined → empty string", () => {
    expect(maskEmail("")).toBe("");
    expect(maskEmail(null)).toBe("");
    expect(maskEmail(undefined)).toBe("");
  });

  it("trims surrounding whitespace before masking", () => {
    expect(maskEmail("  test@aurem.dev  ")).toBe("**st@aurem.dev");
  });

  it("malformed (no @) falls through to maskId semantics", () => {
    // "notanemail" — 10 chars, reveal 2 → 8 stars + "il"
    expect(maskEmail("notanemail")).toBe("********il");
  });

  it("preserves multi-@ addresses correctly (last @ splits)", () => {
    // "a@b@example.com" — local="a@b", domain="@example.com"
    // local length 3, reveal 2 → 1 star + "@b" + domain
    expect(maskEmail("a@b@example.com")).toBe("*@b@example.com");
  });

  it("long local parts still mask everything except tail", () => {
    const out = maskEmail("very.long.email.address@corp.io");
    expect(out.endsWith("ss@corp.io")).toBe(true);
    expect(out).not.toContain("very");
    expect(out).not.toContain("email");
    expect(out).not.toContain("addre");
  });
});

describe("maskId", () => {
  it("keeps trailing 4 chars by default", () => {
    const id = "sub_1P8xY2zAbCdE3fGh4iJ5k6L";  // 27 chars
    const out = maskId(id);
    expect(out.endsWith("5k6L")).toBe(true);
    expect(out.length).toBe(id.length);
    expect(out.slice(0, -4)).toBe("*".repeat(id.length - 4));
  });

  it("respects reveal option", () => {
    expect(maskId("gh_installation_12345678", { reveal: 6 }))
      .toBe("******************345678");
  });

  it("uses minMask when value is shorter than reveal", () => {
    expect(maskId("abc", { reveal: 4 })).toBe("****");
    expect(maskId("abcd", { reveal: 4 })).toBe("****");
  });

  it("respects minMask floor for values just over reveal", () => {
    // value="abcdef" (6), reveal=4 → tail="cdef", mask=max(2, 4)=4 → "****cdef"
    expect(maskId("abcdef", { reveal: 4, minMask: 4 })).toBe("****cdef");
  });

  it("empty / null / undefined → empty string", () => {
    expect(maskId("")).toBe("");
    expect(maskId(null)).toBe("");
    expect(maskId(undefined)).toBe("");
  });

  it("coerces numbers to strings", () => {
    expect(maskId(1234567890, { reveal: 3 })).toBe("*******890");
  });
});
