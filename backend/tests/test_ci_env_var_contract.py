"""
tests/test_ci_env_var_contract.py — CI env-var contract guard (Feb 2026)

Purpose:
    Catch the AUREM_MASTER_KEY-vs-AUREM_CTO_MASTER_KEY class of bug
    where production code reads env var X but the CI config sets X'
    (a typo, a legacy name, or a boundary-refactor rename). CI passes
    every prior-generation test, then a whole set of secret-dependent
    tests fail with `<X> must be set` because CI's env block never
    matched the production reader.

Rule:
    Every "important" env var that production code reads MUST also
    appear in `.github/workflows/ci.yml` under the `env:` block of a
    job that will actually load `services/` or `routers/` code.

    "Important" is defined as: any env var referenced in
    `services/` or `routers/` via `os.environ.get(<NAME>...)` or
    `os.getenv(<NAME>...)` that also has a corresponding
    `RuntimeError("<NAME> must be set")` / `raise` / hard-boot
    dependency (i.e. the app fails-fast if unset). Optional keys
    with silent-fallback behaviour are exempt.

    We hand-curate the REQUIRED list from a shortlist of well-known
    boot-critical variables — grep-based auto-discovery of "must be
    set" strings misses legitimate cases (JWT_SECRET is required
    even without that exact literal), and would over-match.

Real bug this catches:
    Feb 2026 — CI set `AUREM_CTO_MASTER_KEY` but `services/vault.py`
    reads `AUREM_MASTER_KEY`. Ten+ tests in
    `test_iter212m170_ora_context_isolation.py` failed in CI only.
    This test would have caught the mismatch before merge.
"""
from __future__ import annotations

from pathlib import Path

import pytest


CI_YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


# ── Ground truth: env vars production must have set at import/boot ────
# When adding a new hard-required env var to production code, add it
# here AND to ci.yml's `env:` block for backend-tests. If you delete
# it from prod, delete it here.
REQUIRED_ENV_VARS: list[str] = [
    "MONGO_URL",         # services/db.py — Motor connection
    "DB_NAME",           # services/db.py — DB name is not defaulted
    "JWT_SECRET",        # routers/auth.py — token sign/verify
    "AUREM_MASTER_KEY",  # services/vault.py — PAT-vault Fernet master
]


@pytest.fixture(scope="module")
def ci_yml_text() -> str:
    assert CI_YML.exists(), f"ci.yml not found at {CI_YML}"
    return CI_YML.read_text(encoding="utf-8")


@pytest.mark.parametrize("var_name", REQUIRED_ENV_VARS)
def test_ci_has_env_var(ci_yml_text: str, var_name: str) -> None:
    """Each REQUIRED env var must appear as `<NAME>:` in ci.yml.

    We look for the YAML-key pattern `      <NAME>:` (env block
    indent, 6 spaces on GH-hosted workflows). This is stricter than
    a bare grep because it verifies the var lives inside a proper
    `env:` map, not just mentioned in a comment.
    """
    key_pattern = f"      {var_name}:"
    assert key_pattern in ci_yml_text, (
        f"CI config missing required env var `{var_name}` — production "
        f"code reads it at boot and will fail-fast. Add "
        f"`{var_name}: <ci-test-value>` under the `env:` block of the "
        f"backend-tests job in .github/workflows/ci.yml."
    )


def test_ci_env_names_match_production_readers() -> None:
    """No env var in ci.yml should use a legacy / prefixed name that
    production code does NOT actually read.

    Specifically guards against the AUREM_MASTER_KEY → AUREM_CTO_MASTER_KEY
    typo class. If CI sets `AUREM_CTO_MASTER_KEY` but no `services/`
    or `routers/` file reads that name, it's dead config — fail.
    """
    ci_text = CI_YML.read_text(encoding="utf-8")

    # Every var we set in CI:
    import re
    ci_vars = set(re.findall(r"^      ([A-Z][A-Z0-9_]{2,}):", ci_text, re.M))

    # Vars actually referenced by production code:
    root = Path(__file__).resolve().parents[1]
    referenced: set[str] = set()
    for pyfile in list((root / "services").rglob("*.py")) + \
                  list((root / "routers").rglob("*.py")) + \
                  [root / "main.py"]:
        try:
            text = pyfile.read_text(encoding="utf-8")
        except Exception:
            continue
        for match in re.finditer(
            r"os\.(?:environ\.get|getenv)\(\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]",
            text,
        ):
            referenced.add(match.group(1))
        # Also `os.environ["X"]` direct access:
        for match in re.finditer(
            r"os\.environ\[\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]", text
        ):
            referenced.add(match.group(1))

    # Whitelist: CI-only variables that are legitimately unused by
    # production reader code (e.g. GitHub Actions built-ins, or
    # ancillary CI knobs that only affect the runner behaviour, not
    # the app itself).
    ci_only_whitelist = {
        # Runner + workflow controls, not read by app code:
        "PYTHONDONTWRITEBYTECODE",
        "CI",
        "DISABLE_UPSTREAM_TOOLS",   # gate flag consumed via `if:` conditions
        "ENABLE_EVAL_CRON",         # optional cron toggle — read via getenv default
        "REDIS_URL",                # optional cache — read via getenv default
        "APP_URL",                  # exported to sub-processes only
        # PromptFoo eval tool — used only inside the prompt-eval CI job:
        "PROMPTFOO_DISABLE_REMOTE_GENERATION",
        "PROMPTFOO_DISABLE_SHARE",
        "PROMPTFOO_DISABLE_TELEMETRY",
        "API_KEY",                  # PromptFoo expects generic API_KEY name
        # Secret-scan job auth — POSTed to backend, not read via os.environ:
        "CI_TOKEN",
    }

    dead_config = (ci_vars - referenced) - ci_only_whitelist
    assert not dead_config, (
        f"CI config sets env var(s) that NO production reader code "
        f"actually references: {sorted(dead_config)}. This is the "
        f"AUREM_MASTER_KEY-class drift bug (Feb 2026) — either "
        f"production code was refactored to a new name, or the CI "
        f"config typoed the name. Reconcile with services/*.py and "
        f"routers/*.py, or add the var to `ci_only_whitelist` if it's "
        f"legitimately CI-only."
    )
