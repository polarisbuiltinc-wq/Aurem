"""
services/generation_rules_triggers.py — one-line trigger descriptions
per scanner rule id, used by services/generation_rules.py's condensed
manifest builder.

Extracted from services/generation_rules.py (2026-08-27, mechanical
split — no behaviour change) to keep that module under the platform's
file-size guard.
"""
from __future__ import annotations

# One-line trigger descriptions per rule id. Hand-written because the
# regex source is neither readable nor safe to embed in a prompt (a
# raw regex would encourage the model to try to defeat the pattern
# rather than internalise the intent).
#
# Rule ids MUST match those emitted by the scanners. If an id is
# missing here the manifest falls back to the scanner's own message.
TRIGGER_ONELINE: dict[str, str] = {
    # ── Vanguard secrets ──
    "generic_api_key":         "any assignment like `api_key='<value>'` with 8+ non-whitespace chars",
    "aws_access_key":          "literals matching AKIA/AGPA/AIDA/AROA + 16 uppercase alphanumerics",
    "aws_secret_key":          "`aws_secret_access_key='...'` with a 40-char literal",
    "password_assignment":     "`password='...'` / `passwd='...'` with 4+ chars",
    "token_assignment":        "`bearer/auth_token/access_token/refresh_token='...'` 16+ chars",
    "private_key":             "`-----BEGIN [RSA|DSA|EC|OPENSSH|PGP] PRIVATE KEY-----` in source",
    "github_token":            "literals matching `gh[pousr]_[36+ chars]`",
    "slack_token":             "literals matching `xox[bpors]-<digits>-<...>`",
    "generic_secret":          "`secret/signing_key/encryption_key='...'` 16+ chars",
    "db_connection_string":    "`postgres://user:pass@` / `mongodb://user:pass@` style URI in source",
    "stripe_live_key":         "literals `sk_live_ / pk_live_ / rk_live_[20+ chars]`",
    "google_api_key":          "literals `AIza[0-9A-Za-z_-]{35}`",
    "sendgrid_key":            "literals `SG.<22 chars>.<43 chars>`",
    "openai_key":              "literals `sk-<32+ alphanumerics>` (excluding `sk-test-` / `sk-aurem-`)",

    # ── Vanguard dangerous code ──
    "eval_usage":              "any call to `eval(`",
    "exec_usage":              "any call to `exec(`",
    "subprocess_shell_true":   "`subprocess.*(... shell=True ...)`",
    "os_system":               "any call to `os.system(`",
    "pickle_loads":            "any call to `pickle.load(` or `pickle.loads(`",
    "yaml_unsafe_load":        "`yaml.load(...)` without an explicit `Loader=`",
    "requests_no_verify":      "`requests/httpx/urllib .* verify=False`",
    "sql_string_format":       "`cursor.execute(f\"…{var}…\")` — f-string SQL",
    "innerHTML_assignment":    "`.innerHTML = ...` in JS/TS",
    "dangerously_set_html":    "React `dangerouslySetInnerHTML` prop",

    # ── Bug Hunt secrets (adds tighter fingerprints on top of Vanguard) ──
    "aws_access_key_id":       "literal starting `AKIA` + 16 uppercase alphanumerics (long-term key)",
    "aws_temp_token":          "literals starting ASIA/AGPA/AIDA/AROA/AIPA/ANPA/ANVA + 16 chars (STS token)",
    "gcp_api_key":             "`AIza[35 chars]` bordered by non-key chars — GCP API key",
    "stripe_live_secret":      "`sk_live_[24+ chars]` — Stripe secret key (live)",
    "stripe_live_publishable": "`pk_live_[24+ chars]` — Stripe publishable key",
    "sendgrid_api_key":        "`SG.<22>.<43>` — SendGrid API key",
    "slack_bot_token":         "`xox[baprs]-<10..72 chars>` — Slack workspace token",
    "github_pat":              "`ghp_[36 chars]` — GitHub PAT",
    "github_oauth_token":      "`gho_[36 chars]` — GitHub user OAuth token",
    "github_app_token":        "`ghs_/ghu_[36 chars]` — GitHub App/server token",
    "jwt_secret_hardcoded":    "`jwt_secret / JWT_SECRET = '<6+ chars>'` — JWT signing secret",
    "private_rsa_key":         "any `-----BEGIN … PRIVATE KEY-----` block in source",
    "azure_storage_key":       "`AccountKey='<60+ base64 chars>'` — Azure storage key",
    "twilio_api_key":          "`SK<32 hex>` — Twilio API key",
    "env_var_in_code":         "line matching `[UPPERCASE_NAME]='<16+ chars>'` in a code file",

    # ── Bug Hunt vulnerability patterns ──
    "log4shell_jndi":          "any occurrence of `${jndi:` in a string",
    "eval_with_request":       "`eval(...request/input/argv/body/params/query...)`",
    "exec_with_request":       "`exec(...request/input/argv/body/params/query...)`",
    "pickle_loads_untrusted":  "`pickle.load` / `pickle.loads` at all (treat as untrusted)",
    "yaml_load_unsafe":        "`yaml.load(` without `Loader=`",
    "subprocess_shell_true_input": "`subprocess.*(shell=True, ...user/input/request/argv...)`",
    "os_system_user_input":    "`os.system(...user/input/request/argv/body/params/query...)`",
    "xml_etree_no_defusedxml": "any use of stdlib xml.etree/minidom parsers (should be defusedxml)",
    "regex_catastrophic_backtracking": "nested regex quantifiers: `(.*+)+ / (.+)+ / (\\w*)+ / ([^…]*)+`",
    "xxe_external_entity":     "`<!ENTITY name SYSTEM ...>` declaration in source",
    "weak_crypto_md5":         "`hashlib.md5(` or `CryptoJS.MD5`",
    "weak_crypto_sha1":        "`hashlib.sha1(` or `CryptoJS.SHA1`",
    "weak_random_token":       "token/secret/password/nonce/salt = random.random / Math.random",
    "jwt_alg_none":            "`\"alg\": \"none\"` literal in code / JSON",
    "cors_wildcard_with_creds":"`Access-Control-Allow-Origin: *` within 200 chars of `Allow-Credentials: true`",
    "cookie_no_secure_flag":   "`set_cookie(...)` without `secure=True`",
    "cookie_no_httponly_flag": "`set_cookie(...)` without `httponly=True`",
    "ssrf_open_url_fetch":     "`requests/httpx/urlopen/fetch(request./input(/user_input/params[…])`",
    "inner_html_assign":       "`.innerHTML =` (XSS sink)",

    # ── Bug Hunt exposed endpoints ──
    "debug_route_no_auth":     "`@app/router.get/post('/debug|/console|/repl|/shell')` without auth decorator",
    "admin_route_no_auth":     "`@app/router.<verb>('/admin…')` followed by an unguarded `def`",
    "actuator_endpoint":       "any `/actuator|/jolokia|/env|/heapdump|/threaddump|/trace` path literal",
    "metrics_endpoint_no_auth":"`@app/router.get('/metrics')` with an unguarded `def`",
    "health_endpoint_leaks":   "`/health` handler that returns `mongo/db/version/commit/env/secret` info",
    "api_key_in_url":          "URL query string carrying `api_key / token / access_token / secret`",
    "stack_trace_returned":    "returning `traceback.format_exc() / str(e) / repr(e)` to the client",
    "debug_true_production":  "`DEBUG = True` at module scope",
    "swagger_in_prod":         "`docs_url='/docs'` or `openapi_url='/…'` without prod gate",
    "cors_allow_all":          "`allow_origins=['*']` in FastAPI/Starlette CORS middleware",

    # ── HTTP security headers (repo-level rule) ──
    "http_headers_missing":    "web-app entrypoint present (FastAPI/Flask/Express) but NO helmet / secure-headers / HSTS / X-Frame-Options / CSP / X-Content-Type-Options / Referrer-Policy / Permissions-Policy anywhere in the repo",

    # ── Docker CIS ──
    "docker_cis_4_7_latest_tag":       "Dockerfile `FROM image:latest` or `FROM image` (no explicit tag/digest)",
    "docker_cis_4_9_add_instead_copy": "Dockerfile uses `ADD` for a local file (should be `COPY`)",
    "docker_cis_4_10_secret_in_env":   "Dockerfile `ENV/ARG NAME=value` where NAME contains PASSWORD/SECRET/TOKEN/API_KEY",
    "docker_cis_curl_pipe_sh":         "Dockerfile `RUN curl … | sh` / `RUN wget … | bash`",
    "docker_cis_apt_upgrade":          "Dockerfile `apt-get upgrade` / `apt-get dist-upgrade`",
    "docker_cis_4_1_no_user":          "Dockerfile has no `USER` instruction (runs as root)",
    "docker_cis_4_6_no_healthcheck":   "Dockerfile has no `HEALTHCHECK` instruction",
    "docker_cis_5_4_privileged":       "compose file `privileged: true`",
    "docker_cis_5_31_docker_sock":     "compose file mounts `/var/run/docker.sock`",
}
