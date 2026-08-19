/**
 * PasswordInput.jsx — 2026-08-19
 * Shared password field with a show/hide (eye) toggle. Used by
 * Login, ChangePasswordCard, and ResetPassword so every password
 * field in the app behaves identically.
 */
import React, { useState } from "react";
import { Eye, EyeOff } from "lucide-react";

export default function PasswordInput({
  testId, value, onChange, placeholder, required, minLength, autoComplete,
}) {
  const [visible, setVisible] = useState(false);
  return (
    <div style={{ position: "relative" }}>
      <input
        data-testid={testId}
        className="input"
        type={visible ? "text" : "password"}
        required={required}
        minLength={minLength}
        autoComplete={autoComplete}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        style={{ paddingRight: 40 }}
      />
      <button
        type="button"
        data-testid={`${testId}-toggle-visibility`}
        onClick={() => setVisible((v) => !v)}
        aria-label={visible ? "Hide password" : "Show password"}
        tabIndex={-1}
        style={{
          position: "absolute", top: "50%", right: 10,
          transform: "translateY(-50%)",
          background: "transparent", border: "none", padding: 4,
          color: "var(--text-dim)", cursor: "pointer",
          display: "flex", alignItems: "center",
        }}
      >
        {visible ? <EyeOff size={16} /> : <Eye size={16} />}
      </button>
    </div>
  );
}
