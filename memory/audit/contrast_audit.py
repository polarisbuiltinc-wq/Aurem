import re

def hex_to_rgb(h):
    h = h.lstrip('#')
    if len(h) == 3:
        h = ''.join([c*2 for c in h])
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def srgb_to_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

def relative_luminance(rgb):
    r, g, b = rgb
    R, G, B = srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b)
    return 0.2126 * R + 0.7152 * G + 0.0722 * B

def contrast(hex1, hex2):
    l1 = relative_luminance(hex_to_rgb(hex1))
    l2 = relative_luminance(hex_to_rgb(hex2))
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)

def blend(fg_hex, alpha, bg_hex):
    fr, fg_, fb = hex_to_rgb(fg_hex)
    br, bgn, bb = hex_to_rgb(bg_hex)
    r = round(fr * alpha + br * (1 - alpha))
    g = round(fg_ * alpha + bgn * (1 - alpha))
    b = round(fb * alpha + bb * (1 - alpha))
    return '#%02x%02x%02x' % (r, g, b)

pairs = []

# ── Legacy :root theme (main app, non-ds2) ──
BG = "#07080d"
PANEL = "#11141d"
PANEL2 = "#161a25"
pairs.append(("Body text --text #f4ecdc on --bg #07080d",           contrast("#f4ecdc", BG)))
pairs.append(("Body text --text #f4ecdc on --panel #11141d",        contrast("#f4ecdc", PANEL)))
pairs.append(("Muted --text-dim #a39d8a on --bg #07080d",           contrast("#a39d8a", BG)))
pairs.append(("Faint --text-faint #948c79 on --bg #07080d",         contrast("#948c79", BG)))
pairs.append(("Faint --text-faint #948c79 on --panel-2 #161a25",    contrast("#948c79", PANEL2)))
pairs.append(("Accent --accent #ff8a2a on --bg #07080d (non-text 3:1 target if icon/border)", contrast("#ff8a2a", BG)))
pairs.append(("Accent-2 --accent-2 #ffc560 (focus ring) on --bg #07080d", contrast("#ffc560", BG)))
pairs.append(("btn-primary text #0a0a0a on --accent #ff8a2a",       contrast("#0a0a0a", "#ff8a2a")))
pairs.append(("btn-primary text #0a0a0a on --accent-end #e57718",   contrast("#0a0a0a", "#e57718")))
pairs.append(("ok --ok #6dd4a1 on --bg #07080d",                    contrast("#6dd4a1", BG)))
pairs.append(("danger --danger #ff6b6b on --bg #07080d",            contrast("#ff6b6b", BG)))
pairs.append(("warn --warn #ffc560 on --bg #07080d",                contrast("#ffc560", BG)))
pairs.append(("info --info #7da4ff on --bg #07080d",                contrast("#7da4ff", BG)))
b_border = blend("#ffc878", 0.10, BG)
b_border_strong = blend("#ffc878", 0.22, BG)
pairs.append((f"--border rgba(255,200,120,.10) blended -> {b_border} vs --bg (non-text 3:1)", contrast(b_border, BG)))
pairs.append((f"--border-strong rgba(255,200,120,.22) blended -> {b_border_strong} vs --bg (non-text 3:1)", contrast(b_border_strong, BG)))

# ── ds2-root dashboard theme ──
DS2_BG = "#0A0A0A"
DS2_CARD = "#161616"
pairs.append(("ds2 --ds2-fg #F5F5F5 on --ds2-bg #0A0A0A",           contrast("#F5F5F5", DS2_BG)))
pairs.append(("ds2 --ds2-fg #F5F5F5 on --ds2-card #161616",         contrast("#F5F5F5", DS2_CARD)))
pairs.append(("ds2 --ds2-muted-fg #8A8A8A on --ds2-bg #0A0A0A",     contrast("#8A8A8A", DS2_BG)))
pairs.append(("ds2 --ds2-muted-fg #8A8A8A on --ds2-card #161616",   contrast("#8A8A8A", DS2_CARD)))
pairs.append(("ds2 --ds2-primary #FF6608 on --ds2-bg #0A0A0A",      contrast("#FF6608", DS2_BG)))
pairs.append(("ds2 primary-fg #0A0A0A on --ds2-primary #FF6608",    contrast("#0A0A0A", "#FF6608")))
pairs.append(("ds2 --ds2-success #22C55E on --ds2-bg #0A0A0A",      contrast("#22C55E", DS2_BG)))
pairs.append(("ds2 --ds2-warning #F59E0B on --ds2-bg #0A0A0A",      contrast("#F59E0B", DS2_BG)))
pairs.append(("ds2 --ds2-destructive #EF4444 on --ds2-bg #0A0A0A",  contrast("#EF4444", DS2_BG)))
b_ds2_border = blend("#222222", 1.0, DS2_BG)  # solid, not alpha
pairs.append(("ds2 --ds2-border #222222 on --ds2-bg #0A0A0A (non-text 3:1)", contrast("#222222", DS2_BG)))

# ── WorkCard.jsx TONE badges (soft bg tint over near-black card bg) ──
WC_BG = blend("#0d1018", 1.0, BG)  # WorkCard sits on chat bg approx
pairs.append(("WorkCard blue fg #7dd3fc vs soft bg (blend 14% #38bdf8 over card)", contrast("#7dd3fc", blend("#38bdf8", 0.14, PANEL))))
pairs.append(("WorkCard green fg #4ade80 vs soft bg (blend 16% #22c55e over card)", contrast("#4ade80", blend("#22c55e", 0.16, PANEL))))
pairs.append(("WorkCard amber fg #fbbf24 vs soft bg (blend 14% #f59e0b over card)", contrast("#fbbf24", blend("#f59e0b", 0.14, PANEL))))
pairs.append(("WorkCard red fg #fca5a5 vs soft bg (blend 14% #ef4444 over card)", contrast("#fca5a5", blend("#ef4444", 0.14, PANEL))))
pairs.append(("WorkCard grey fg #cbd5e1 vs soft bg (blend 10% #94a3b8 over card)", contrast("#cbd5e1", blend("#94a3b8", 0.10, PANEL))))
pairs.append(("WorkCard body text var(--text-dim,#555) on card", contrast("#a39d8a", PANEL)))
pairs.append(("WorkCard meta text var(--text-faint,#8b949e) on card", contrast("#948c79", PANEL)))

# ── ShipLintBadge ──
pairs.append(("ShipLintBadge clean fg #22c55e on blend(15% over panel)", contrast("#22c55e", blend("#22c55e", 0.15, PANEL))))
pairs.append(("ShipLintBadge blocked fg #ef4444 on blend(15% over panel)", contrast("#ef4444", blend("#ef4444", 0.15, PANEL))))
pairs.append(("ShipLintBadge warning fg #f59e0b on blend(15% over panel)", contrast("#f59e0b", blend("#f59e0b", 0.15, PANEL))))

# ── LoopStatusChip C palette on C.bg #111827 ──
LSC_BG = "#111827"
pairs.append(("LoopStatusChip text #e6ebf3 on bg #111827", contrast("#e6ebf3", LSC_BG)))
pairs.append(("LoopStatusChip dim #9aa0a8 on bg #111827", contrast("#9aa0a8", LSC_BG)))
pairs.append(("LoopStatusChip green #34d399 on bg #111827", contrast("#34d399", LSC_BG)))
pairs.append(("LoopStatusChip red #f87171 on bg #111827", contrast("#f87171", LSC_BG)))
pairs.append(("LoopStatusChip amber #f5a524 on bg #111827", contrast("#f5a524", LSC_BG)))

# ── LiveTaskPopup C palette on C.bg #0c0f15 ──
LTP_BG = "#0c0f15"
pairs.append(("LiveTaskPopup ink #e8e2cf on bg #0c0f15", contrast("#e8e2cf", LTP_BG)))
pairs.append(("LiveTaskPopup dim #7a7e88 on bg #0c0f15", contrast("#7a7e88", LTP_BG)))
pairs.append(("LiveTaskPopup green #6dd4a1 on bg #0c0f15", contrast("#6dd4a1", LTP_BG)))
pairs.append(("LiveTaskPopup red #ff6b6b on bg #0c0f15", contrast("#ff6b6b", LTP_BG)))
pairs.append(("LiveTaskPopup amber #c8922a on bg #0c0f15", contrast("#c8922a", LTP_BG)))

# ── LoopStepBar colours on card #161616 ──
LSB_BG = "#161616"
pairs.append(("LoopStepBar amber #FF6608 on card #161616", contrast("#FF6608", LSB_BG)))
pairs.append(("LoopStepBar green #22C55E on card #161616", contrast("#22C55E", LSB_BG)))
pairs.append(("LoopStepBar red #EF4444 on card #161616", contrast("#EF4444", LSB_BG)))
pairs.append(("LoopStepBar neutral/muted #666 on card #161616 (non-text 3:1)", contrast("#666666", LSB_BG)))
pairs.append(("LoopStepBar retry pill fg #FB923C on blend(10% over card)", contrast("#FB923C", blend("#FB923C", 0.10, LSB_BG))))

# ── IntentTierIndicator on bg blend(0.03 white over panel) ──
ITI_BG = blend("#ffffff", 0.03, PANEL)
pairs.append(("IntentTierIndicator casual #94a3b8 on composer bg", contrast("#94a3b8", ITI_BG)))
pairs.append(("IntentTierIndicator query #fde68a on composer bg", contrast("#fde68a", ITI_BG)))
pairs.append(("IntentTierIndicator agentic #fdba74 on composer bg", contrast("#fdba74", ITI_BG)))
pairs.append(("IntentTierIndicator clarify #fef08a on composer bg", contrast("#fef08a", ITI_BG)))

print(f"{'PAIR':<75} {'RATIO':>8}  {'AA-text(4.5)':>12}  {'AA-nontext(3.0)':>16}")
print("-" * 120)
for label, ratio in pairs:
    aa_text = "PASS" if ratio >= 4.5 else "FAIL"
    aa_nontext = "PASS" if ratio >= 3.0 else "FAIL"
    aaa_text = "PASS" if ratio >= 7.0 else "FAIL"
    print(f"{label:<75} {ratio:>8.2f}  {aa_text:>12}  {aa_nontext:>16}  AAA:{aaa_text}")
