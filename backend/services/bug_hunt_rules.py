"""
services/bug_hunt_rules.py  —  Iter 212m-73 (Bug Hunt category)
=================================================================
50+ Nuclei-template-inspired STATIC code analysis rules.

Pure regex. Zero LLM cost. Walks the same {path: text} cache as the
other Codebase Health scanners and returns Finding dicts in the same
shape: {id, category, severity, file, line, title, message, fix_hint,
fix_tokens}.

Categories embedded inside the single Bug Hunt scanner:
  • SECRETS                 (15 patterns)
  • VULNERABLE CODE         (20 patterns)
  • EXPOSED ENDPOINTS       (10 patterns)
  • DEPENDENCY CVES         (5 + extensible CVE map)

Cost model: each finding's `fix_tokens` is 8 (higher than the regular
5 — Bug Hunt findings are higher-risk + take more LLM work to patch
correctly).

Source inspiration: ProjectDiscovery's Nuclei template catalog
(https://github.com/projectdiscovery/nuclei-templates). Patterns were
adapted from HTTP-detection templates into static-source detectors so
we can run them at commit-time without a live target.
"""
from __future__ import annotations

import re

# ──────────────────────────────────────────────────────────────────────
# Category A — SECRETS (15 patterns)
# Each tuple: (rule_id, regex, severity, message)
# ──────────────────────────────────────────────────────────────────────
_SECRET_RULES: list[tuple[str, re.Pattern, str, str]] = [
    ("aws_access_key_id",
     re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
     "CRITICAL",
     "Hard-coded AWS access key ID — anyone with this can spend from your AWS bill."),
    ("aws_temp_token",
     re.compile(r"\b(?:ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{16}\b"),
     "CRITICAL",
     "AWS short-term token committed — rotate and revoke immediately."),
    ("gcp_api_key",
     re.compile(r"\bAIza[0-9A-Za-z\-_]{35}(?![0-9A-Za-z\-_])"),
     "CRITICAL",
     "Google Cloud API key in source — billing exposure and quota theft."),
    ("stripe_live_secret",
     re.compile(r"\bsk_live_[0-9a-zA-Z]{24,}\b"),
     "CRITICAL",
     "Stripe LIVE secret key — can issue refunds, see customers, drain funds."),
    ("stripe_live_publishable",
     re.compile(r"\bpk_live_[0-9a-zA-Z]{24,}\b"),
     "MEDIUM",
     "Publishable key is public by design, but committing it makes rotation harder."),
    ("sendgrid_api_key",
     re.compile(r"\bSG\.[a-zA-Z0-9_\-]{22}\.[a-zA-Z0-9_\-]{43}\b"),
     "CRITICAL",
     "SendGrid full-access API key — attacker can mail-bomb from your domain."),
    ("slack_bot_token",
     re.compile(r"\bxox[baprs]-[0-9a-zA-Z\-]{10,72}\b"),
     "CRITICAL",
     "Slack token — exfiltrates entire workspace history and DMs."),
    ("github_pat",
     re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
     "CRITICAL",
     "GitHub personal access token — can clone, push, and delete repos."),
    ("github_oauth_token",
     re.compile(r"\bgho_[A-Za-z0-9]{36}\b"),
     "CRITICAL",
     "GitHub OAuth user token — full account access at GitHub permissions level."),
    ("github_app_token",
     re.compile(r"\b(?:ghs|ghu)_[A-Za-z0-9]{36}\b"),
     "CRITICAL",
     "GitHub App/server token — installation-wide access on repos."),
    ("jwt_secret_hardcoded",
     re.compile(r"""(?i)(?:jwt[_-]?secret|jwt[_-]?key|JWT_SECRET)\s*[:=]\s*['"][^'"\s]{6,}['"]"""),
     "CRITICAL",
     "JWT signing secret in source — anyone can forge admin tokens."),
    ("private_rsa_key",
     re.compile(r"-----BEGIN\s+(?:RSA|DSA|EC|OPENSSH|PGP)?\s*PRIVATE\s+KEY-----"),
     "CRITICAL",
     "Private key block committed — must be rotated and the repo history rewritten."),
    ("azure_storage_key",
     re.compile(r"""(?i)(?:AccountKey|azure[_-]?storage[_-]?key)\s*[:=]\s*['"]?[A-Za-z0-9+/=]{60,}['"]?"""),
     "CRITICAL",
     "Azure Storage account key in source — full container read/write."),
    ("twilio_api_key",
     re.compile(r"\bSK[0-9a-fA-F]{32}\b"),
     "CRITICAL",
     "Twilio API key — attacker can send paid SMS / make calls on your account."),
    ("env_var_in_code",
     re.compile(r"""(?im)^[A-Z][A-Z0-9_]{6,}\s*=\s*['"][A-Za-z0-9_+/=.\-]{16,}['"]"""),
     "MEDIUM",
     "Looks like a .env line committed in source — move to .env and gitignore it."),
]


# ──────────────────────────────────────────────────────────────────────
# Category B — VULNERABLE CODE PATTERNS (20 patterns)
# ──────────────────────────────────────────────────────────────────────
_VULN_RULES: list[tuple[str, re.Pattern, str, str]] = [
    ("log4shell_jndi",
     re.compile(r"\$\{jndi:"),
     "CRITICAL",
     "Log4Shell-style JNDI lookup in a string — remote code execution via log injection."),
    ("eval_usage",
     re.compile(r"\beval\s*\([^)]*(?:request|input|argv|body|params|query)\b"),
     "CRITICAL",
     "eval() with user-controlled input — direct RCE primitive."),
    ("exec_with_request",
     re.compile(r"\bexec\s*\([^)]*(?:request|input|argv|body|params|query)\b"),
     "CRITICAL",
     "exec() with user-controlled input — direct RCE primitive."),
    ("pickle_loads_untrusted",
     re.compile(r"\bpickle\.loads?\s*\("),
     "HIGH",
     "pickle.loads on any input is RCE — replace with JSON or msgpack."),
    ("yaml_load_unsafe",
     re.compile(r"\byaml\.load\s*\((?!.*Loader\s*=)"),
     "HIGH",
     "yaml.load without a Loader is unsafe — use yaml.safe_load."),
    ("subprocess_shell_true_input",
     re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True[^)]*(?:request|input|argv|body|params|query|user)"),
     "CRITICAL",
     "subprocess(shell=True) with user input — shell injection RCE."),
    ("os_system_user_input",
     re.compile(r"\bos\.system\s*\([^)]*(?:request|input|argv|body|params|query|user)"),
     "CRITICAL",
     "os.system with user input — shell injection RCE."),
    ("xml_etree_no_defusedxml",
     re.compile(r"(?:from\s+xml\.etree|import\s+xml\.etree|xml\.etree\.ElementTree\.parse|minidom\.parseString)"),
     "MEDIUM",
     "stdlib XML parsers are vulnerable to XXE / billion-laughs — use defusedxml."),
    ("regex_catastrophic_backtracking",
     re.compile(r"""(?:\(\.\*\+?\)\+|\(\.\+\)\+|\(\\\w\*\)\+|\(\[\^[^\]]+\]\*\)\+)"""),
     "MEDIUM",
     "Regex with nested quantifiers — ReDoS attack surface."),
    ("xxe_external_entity",
     re.compile(r"<!ENTITY\s+\w+\s+SYSTEM"),
     "HIGH",
     "External entity declaration in source — confirms XXE-vulnerable design."),
    ("weak_crypto_md5",
     re.compile(r"hashlib\.md5\s*\(|MD5\s*\(|CryptoJS\.MD5"),
     "MEDIUM",
     "MD5 is broken for security purposes — use SHA-256 or bcrypt/argon2 for passwords."),
    ("weak_crypto_sha1",
     re.compile(r"hashlib\.sha1\s*\(|CryptoJS\.SHA1"),
     "MEDIUM",
     "SHA-1 is collision-broken — use SHA-256 or stronger."),
    ("weak_random_token",
     re.compile(r"""(?i)(?:token|secret|password|nonce|salt)\s*=\s*(?:random\.random|random\.randint|Math\.random)\s*\("""),
     "HIGH",
     "Tokens/secrets generated from non-cryptographic PRNG — use secrets.token_urlsafe or crypto.randomBytes."),
    ("jwt_alg_none",
     re.compile(r"""['"]alg['"]\s*:\s*['"]none['"]"""),
     "CRITICAL",
     "JWT with alg=none accepts unsigned tokens — anyone can forge claims."),
    ("cors_wildcard_with_creds",
     re.compile(r"Access-Control-Allow-Origin[^\n]*\*[\s\S]{0,200}Access-Control-Allow-Credentials[^\n]*true",
                re.IGNORECASE),
     "HIGH",
     "CORS wildcard with credentials — defeats the same-origin policy entirely."),
    ("cookie_no_secure_flag",
     re.compile(r"set_cookie\([^)]*(?!.*secure\s*=\s*True)[^)]*\)"),
     "MEDIUM",
     "Cookie set without Secure flag — can be sent over plaintext HTTP."),
    ("cookie_no_httponly_flag",
     re.compile(r"set_cookie\([^)]*(?!.*httponly\s*=\s*True)[^)]*\)"),
     "MEDIUM",
     "Cookie set without HttpOnly — readable from JS / XSS-exfiltratable."),
    ("ssrf_open_url_fetch",
     re.compile(r"""(?:requests\.get|httpx\.get|urlopen|fetch)\s*\(\s*(?:request\.|input\(|user_input|params\[)"""),
     "HIGH",
     "Outbound HTTP with user-supplied URL — SSRF: attacker hits your internal services."),
    ("sql_string_format",
     re.compile(r"""(?i)(?:execute|cursor\.execute)\s*\(\s*[f'\"]+.*\{"""),
     "CRITICAL",
     "Raw SQL built with f-string / format — SQL injection."),
    ("dangerously_set_html",
     re.compile(r"\bdangerouslySetInnerHTML\b"),
     "HIGH",
     "React dangerouslySetInnerHTML — sanitize via DOMPurify or refuse to use it."),
    ("inner_html_assign",
     re.compile(r"\.innerHTML\s*="),
     "HIGH",
     "Direct .innerHTML assignment — XSS sink. Use textContent or DOMPurify."),
]


# ──────────────────────────────────────────────────────────────────────
# Category C — EXPOSED ENDPOINTS (10 patterns)
# ──────────────────────────────────────────────────────────────────────
_ENDPOINT_RULES: list[tuple[str, re.Pattern, str, str]] = [
    ("debug_route_no_auth",
     re.compile(r"""(?i)@(?:app|router)\.(?:get|post|route)\s*\(\s*['"]/(?:debug|console|repl|shell)['"][^)]*\)\s*(?!\s*@(?:require|auth|login))"""),
     "CRITICAL",
     "/debug-style endpoint without an auth decorator on the next line — public RCE risk."),
    ("admin_route_no_auth",
     re.compile(r"""(?i)@(?:app|router)\.(?:get|post|route)\s*\(\s*['"]/admin[^'"\n]*['"][^)]*\)\s*\n\s*(?:async\s+)?def"""),
     "HIGH",
     "/admin endpoint defined — verify the decorator above enforces is_admin/role check."),
    ("actuator_endpoint",
     re.compile(r"""(?i)['"]/(?:actuator|jolokia|env|heapdump|threaddump|trace)['"]"""),
     "HIGH",
     "Spring/JMX actuator path exposed — leaks env vars, heap, credentials."),
    ("metrics_endpoint_no_auth",
     re.compile(r"""(?i)@(?:app|router)\.(?:get|route)\s*\(\s*['"]/metrics['"][^)]*\)\s*\n\s*(?:async\s+)?def\s+\w+\s*\([^)]*\)"""),
     "MEDIUM",
     "/metrics endpoint — restrict to internal IPs / require bearer token."),
    ("health_endpoint_leaks",
     re.compile(r"""(?i)['"]/health['"][\s\S]{0,400}(?:mongo|db|database|version|commit|env|secret)"""),
     "MEDIUM",
     "/health endpoint appears to leak internals (DB names, commit SHA, env). Keep it boolean."),
    ("api_key_in_url",
     re.compile(r"""(?i)\?(?:api[_-]?key|token|access[_-]?token|secret)=\{?\w+\}?"""),
     "HIGH",
     "Secret passed in URL — gets logged in proxies, browser history, referrers."),
    ("stack_trace_returned",
     re.compile(r"""(?:traceback\.format_exc\(\)|str\(e\)|repr\(e\)).{0,40}(?:return|response|HTTPException|JSONResponse)"""),
     "MEDIUM",
     "Looks like an exception's full text is returned to the client — leaks file paths and code structure."),
    ("debug_true_production",
     re.compile(r"""(?im)^\s*DEBUG\s*=\s*True"""),
     "HIGH",
     "DEBUG=True at module level — Flask/Django debug mode is an RCE primitive (Werkzeug console)."),
    ("swagger_in_prod",
     re.compile(r"""(?i)(?:docs_url|openapi_url|swagger_ui)\s*=\s*['"]/(?:docs|swagger|api-docs)['"]"""),
     "LOW",
     "Swagger/OpenAPI UI enabled — restrict in prod or gate behind auth."),
    ("cors_allow_all",
     re.compile(r"""allow_origins\s*=\s*\[\s*['"]\*['"]\s*\]"""),
     "HIGH",
     "FastAPI/Starlette CORS allow_origins=['*'] — combined with credentials this is critical."),
]


# ──────────────────────────────────────────────────────────────────────
# Category D — DEPENDENCY CVES (extensible)
# Tuple: (package_name_lowercase, max_vulnerable_version, cve_id,
#         severity, message)
# Detection logic compares manifests (requirements.txt, package.json).
# ──────────────────────────────────────────────────────────────────────
_DEP_CVES: list[tuple[str, str, str, str, str]] = [
    ("requests",     "2.31.0", "CVE-2023-32681",
     "HIGH",     "requests < 2.31.0 — Proxy-Authorization leak via cross-origin redirect."),
    ("flask",        "2.3.0",  "multi-cves",
     "HIGH",     "flask < 2.3.0 — session-cookie + open-redirect CVEs."),
    ("django",       "4.2.0",  "multi-cves",
     "CRITICAL", "django < 4.2.0 — SQL injection + DoS CVEs."),
    ("pillow",       "10.0.0", "CVE-2023-44271",
     "HIGH",     "pillow < 10.0.0 — buffer overflow in JPEG/PNG parsing."),
    ("cryptography", "41.0.0", "multi-cves",
     "HIGH",     "cryptography < 41.0.0 — multiple openssl-bound vulns."),
    # bonus easy wins
    ("urllib3",