# Group C re-link email — draft, awaiting founder approval, NOT sent

Target: 3 real user_ids (Group C — legacy/never-migrated GitHub App connections).
Send mechanism: existing `POST /admin/users/email-offer` (admin-only, already deployed).

## Subject
Quick fix needed: reconnect your GitHub repo on AUREM

## Body (HTML, {{name}} / {{email}} supported by the endpoint)

```html
<p>Hi {{name}},</p>

<p>Your GitHub repo on AUREM needs a quick re-link — it was connected before we
moved to our current GitHub App–based setup, so it's no longer active.</p>

<p>This takes under a minute: open your dashboard, click <strong>"Reconnect
GitHub App"</strong> on the banner, and pick your repo again in the popup.
Your chat history and project stay exactly as they were.</p>

<p><a href="https://auremcto.com/dashboard" style="display:inline-block;padding:10px 18px;background:#FF8A2A;color:#fff;border-radius:6px;text-decoration:none;font-weight:600;">
Go to my dashboard →</a></p>

<p>Any trouble, just reply to this email.</p>

<p>— AUREM</p>
```

## Notes
- Tone: short, non-alarming, no "broken"/"error" language — framed as a
  one-time housekeeping step from the App-only migration, not a fault of theirs.
- Explicitly reassures no data loss (chat history/project preserved) since
  losing that reassurance could make someone hesitate to click.
- `from`/`reply_to` left to endpoint defaults (`DIGEST_FROM` / support inbox)
  unless founder wants to override.
