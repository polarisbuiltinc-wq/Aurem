# TEMPORARY — Pillar 2 deploy-gate E2E proof (founder-approved).
# Deliberately failing test to prove the gate-on-ci polling logic in
# auto_deploy.yml blocks a deploy when CI genuinely fails.
# DELETE this file immediately after the block evidence is captured.


def test_deploy_gate_e2e_deliberate_failure():
    assert False, "DELIBERATE failure — Pillar 2 deploy-gate E2E block proof"
