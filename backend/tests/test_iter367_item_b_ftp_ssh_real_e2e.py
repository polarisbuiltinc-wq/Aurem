"""Iter 367 (Item B) — REAL E2E test: local FTP + SFTP servers spun
up inside this container, real bytes transferred via the deploy
router's plumbing.

Not mocked. `pyftpdlib.FTPServer` and `paramiko`'s SSH server run on
127.0.0.1 with disposable creds. The test:

  1. Boots an FTP server on 127.0.0.1:2121 with a temp home dir.
  2. Boots an SFTP server on 127.0.0.1:2222 with a temp home dir.
  3. Calls services.ftp_ssh_deploy.deploy_via_ftp() + deploy_via_ssh()
     directly, uploading a fixture file.
  4. Reads the file back from the server's local filesystem to prove
     the bytes actually landed.
  5. Also exercises the wiring by staging a mock deploy_config via
     _run_deploy_ftp_or_sftp() (the router's private worker) and
     asserts the aurem_cto_deploy_runs row records ok+uploaded.
"""
from __future__ import annotations

import asyncio
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


# ─────────────────────────────────────────────────────────────────
# Fixture: real FTP server on 127.0.0.1:2121
# ─────────────────────────────────────────────────────────────────


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]; s.close(); return port


@pytest.fixture
def ftp_server():
    """Boot a real FTP server via `python -m pyftpdlib` subprocess on
    a random loopback port. Subprocess isolates the asyncore event
    loop from pytest and eliminates threading races."""
    import subprocess

    home = tempfile.mkdtemp(prefix="iter367_ftp_home_")
    port = _free_port()
    proc = subprocess.Popen(
        ["python3", "-m", "pyftpdlib",
         "--interface", "127.0.0.1", "--port", str(port),
         "--username", "testuser", "--password", "testpass",
         "--directory", home, "--write"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    # Wait for the port to open.
    for _ in range(40):
        try:
            s = socket.socket()
            s.settimeout(0.2)
            s.connect(("127.0.0.1", port))
            s.close(); break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        _stderr = (proc.stderr.read().decode(errors="replace")
                   if proc.stderr else "")
        pytest.skip(f"pyftpdlib did not come up on port {port}: {_stderr[:400]}")

    yield {"host": "127.0.0.1", "port": port, "user": "testuser",
           "password": "testpass", "home": home}
    proc.terminate()
    try: proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


# ─────────────────────────────────────────────────────────────────
# Fixture: real SFTP server on 127.0.0.1:<free>
# ─────────────────────────────────────────────────────────────────


@pytest.fixture
def sftp_server():
    """Boot a REAL OpenSSH `sshd` daemon on a random loopback port
    with password auth enabled. This is a real sftp-subsystem server
    from the system's openssh-server package — not a paramiko fake.
    """
    import subprocess
    import shutil

    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    if not os.path.exists(sshd):
        pytest.skip(f"sshd binary not found at {sshd}")

    home     = tempfile.mkdtemp(prefix="iter367_sftp_home_")
    run_dir  = tempfile.mkdtemp(prefix="iter367_sftp_run_")
    port     = _free_port()

    # Locate sftp-server helper (path varies). Prefer sshd's built-in
    # `internal-sftp` subsystem — it doesn't need an external binary and
    # avoids lib-path / shell issues when the target user has /bin/false.
    sftp_helper = "internal-sftp"

    # Generate a host key.
    host_key = os.path.join(run_dir, "host_ed25519")
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", host_key],
        check=True, capture_output=True,
    )

    # Create a testuser with a password on this system? No — sshd wants
    # a real /etc/passwd user. Since we're root in this container, we
    # can add a temp OS user.
    import pwd
    testuser = "iter367ftpuser"
    try:
        pwd.getpwnam(testuser)
    except KeyError:
        subprocess.run(
            ["useradd", "-M", "-d", home, "-s", "/bin/false", testuser],
            check=True, capture_output=True,
        )
    # Set the password (chpasswd expects "user:pass" on stdin).
    subprocess.run(
        ["chpasswd"], input=f"{testuser}:sftppass".encode(),
        check=True, capture_output=True,
    )
    # Ensure user owns the home so writes work.
    subprocess.run(["chown", "-R", f"{testuser}:{testuser}", home],
                    check=True)

    # Write sshd_config with password auth + only our user permitted.
    cfg_path = os.path.join(run_dir, "sshd_config")
    with open(cfg_path, "w") as fh:
        fh.write(f"""Port {port}
ListenAddress 127.0.0.1
HostKey {host_key}
PidFile {run_dir}/sshd.pid
PasswordAuthentication yes
PermitEmptyPasswords no
PubkeyAuthentication no
KbdInteractiveAuthentication no
UsePAM no
StrictModes no
AllowUsers {testuser}
Subsystem sftp {sftp_helper}
LogLevel DEBUG3
""")

    # Boot sshd in the foreground (-D) as a background subprocess.
    proc = subprocess.Popen(
        [sshd, "-f", cfg_path, "-D", "-e"],   # -e = stderr, not syslog
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    # Wait for port.
    for _ in range(30):
        try:
            s = socket.socket()
            s.settimeout(0.3)
            s.connect(("127.0.0.1", port))
            s.close(); break
        except OSError:
            time.sleep(0.15)
    else:
        proc.kill()
        _stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        pytest.skip(f"sshd did not come up on port {port}. stderr={_stderr[:400]}")

    yield {"host": "127.0.0.1", "port": port, "user": testuser,
           "password": "sftppass", "home": home}

    proc.terminate()
    try: proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
    # Best-effort cleanup of the OS user.
    subprocess.run(["userdel", testuser], capture_output=True)


# ─────────────────────────────────────────────────────────────────
# Direct-service tests — prove real bytes land on disk
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deploy_via_ftp_real_upload(ftp_server):
    """FTPS upload REAL bytes to the local FTP server, then read them
    back from disk to prove the transfer actually happened."""
    from services.ftp_ssh_deploy import deploy_via_ftp

    payload = b"iter367-item-b-real-ftp-payload-" + os.urandom(8).hex().encode()
    files = {"public/index.html": payload,
             "public/robots.txt": b"User-agent: *\nDisallow:\n"}
    res = await deploy_via_ftp(
        host=ftp_server["host"], port=ftp_server["port"],
        user=ftp_server["user"], password=ftp_server["password"],
        remote_dir="/site",  # will be created
        files=files,
        tls=False,  # pyftpdlib DummyAuthorizer doesn't do TLS
    )
    assert res["ok"] is True, f"deploy_via_ftp failed: {res}"
    assert res["uploaded"] == 2
    assert res["target"] == "ftp"

    # Read the file back from the server's home dir.
    landed = Path(ftp_server["home"]) / "site" / "public" / "index.html"
    assert landed.exists(), f"file not landed on FTP server: {landed}"
    assert landed.read_bytes() == payload, "byte-for-byte mismatch"


@pytest.mark.asyncio
async def test_deploy_via_sftp_real_upload(sftp_server):
    """SFTP upload real bytes via paramiko to a REAL OpenSSH sshd
    running locally, then read them back from disk."""
    from services.ftp_ssh_deploy import deploy_via_ssh

    payload = b"iter367-item-b-real-sftp-payload-" + os.urandom(8).hex().encode()
    files = {"app/main.py":     payload,
             "app/version.txt": b"1.0.0\n"}
    # Target inside the user's writable home dir.
    remote_dir = f"{sftp_server['home']}/deploys/latest"
    res = await deploy_via_ssh(
        host=sftp_server["host"], port=sftp_server["port"],
        user=sftp_server["user"], password=sftp_server["password"],
        remote_dir=remote_dir,
        files=files,
    )
    assert res["ok"] is True, f"deploy_via_ssh failed: {res}"
    assert res["uploaded"] == 2
    assert res["target"] == "ssh"

    landed = Path(remote_dir) / "app" / "main.py"
    assert landed.exists(), f"file not landed on SFTP server: {landed}"
    assert landed.read_bytes() == payload, "byte-for-byte mismatch"


# ─────────────────────────────────────────────────────────────────
# Router-worker test — proves the deploy router wires to ftp_ssh_deploy
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_router_worker_ftp_end_to_end(ftp_server, monkeypatch):
    """Exercise routers.deploy._run_deploy_ftp_or_sftp end-to-end:
      • Simulate a saved deploy config with target='ftp'
      • Fire the worker with a real files dict
      • Assert bytes land on disk + run doc records ok:True + uploaded
    """
    from routers import deploy as dep

    # Fake in-memory Mongo double
    class _Coll:
        def __init__(self):
            self.rows = []
        async def insert_one(self, doc):
            self.rows.append(dict(doc))
            class _R: inserted_id = "id"
            return _R()
        async def update_one(self, filt, ops, upsert=False):
            for r in self.rows:
                if all(r.get(k) == v for k, v in filt.items()):
                    if "$set" in ops:
                        r.update(ops["$set"])
                    if "$push" in ops:
                        for k, v in ops["$push"].items():
                            r.setdefault(k, []).append(v)

    class _DB:
        def __init__(self):
            self.aurem_cto_deploy_runs = _Coll()

    db = _DB()
    monkeypatch.setattr(dep, "require_db", lambda: db)

    # Fake decrypt returns the plaintext password directly.
    async def _fake_decrypt(uid, blob, kind=None):
        return "testpass"
    monkeypatch.setattr(dep, "decrypt", _fake_decrypt)

    # Seed the run doc so the worker's update_one has a row to hit.
    run_id = "run_test_ftp"
    await db.aurem_cto_deploy_runs.insert_one({
        "run_id": run_id, "status": "running", "output": []
    })

    cfg = {
        "host":     ftp_server["host"],
        "port":     ftp_server["port"],
        "username": ftp_server["user"],
        "private_key_enc": "encblob",
        "secret_kind":     "ftp_password",
        "target":     "ftp",
        "remote_dir": "/router_test",
        "ftp_tls":    False,
    }
    files = {"index.html": {"content_b64":
             __import__("base64").b64encode(b"router-e2e-content").decode()}}
    await dep._run_deploy_ftp_or_sftp(
        user_id="u1", run_id=run_id, cfg=cfg, files=files,
    )

    # Assert on the run row + on the file on disk.
    row = next(r for r in db.aurem_cto_deploy_runs.rows if r["run_id"] == run_id)
    assert row["status"] == "ok", f"expected ok, got {row}"
    assert row["uploaded"] == 1
    landed = Path(ftp_server["home"]) / "router_test" / "index.html"
    assert landed.exists()
    assert landed.read_bytes() == b"router-e2e-content"


@pytest.mark.asyncio
async def test_router_worker_sftp_end_to_end(sftp_server, monkeypatch):
    """Same as FTP but via SFTP transport (paramiko)."""
    from routers import deploy as dep

    class _Coll:
        def __init__(self): self.rows = []
        async def insert_one(self, doc):
            self.rows.append(dict(doc))
            class _R: inserted_id = "id"
            return _R()
        async def update_one(self, filt, ops, upsert=False):
            for r in self.rows:
                if all(r.get(k) == v for k, v in filt.items()):
                    if "$set" in ops: r.update(ops["$set"])
                    if "$push" in ops:
                        for k, v in ops["$push"].items():
                            r.setdefault(k, []).append(v)

    class _DB:
        def __init__(self): self.aurem_cto_deploy_runs = _Coll()
    db = _DB()
    monkeypatch.setattr(dep, "require_db", lambda: db)

    async def _fake_decrypt(uid, blob, kind=None):
        return "sftppass"
    monkeypatch.setattr(dep, "decrypt", _fake_decrypt)

    run_id = "run_test_sftp"
    await db.aurem_cto_deploy_runs.insert_one({
        "run_id": run_id, "status": "running", "output": [],
    })

    cfg = {
        "host":     sftp_server["host"],
        "port":     sftp_server["port"],
        "username": sftp_server["user"],
        "private_key_enc": "encblob",
        "secret_kind":     "ftp_password",   # password-based SFTP
        "target":     "sftp",
        "remote_dir": f"{sftp_server['home']}/router_sftp_test",
    }
    files = {"a/b/c.txt": b"router-sftp-e2e-bytes"}
    await dep._run_deploy_ftp_or_sftp(
        user_id="u1", run_id=run_id, cfg=cfg, files=files,
    )
    row = next(r for r in db.aurem_cto_deploy_runs.rows if r["run_id"] == run_id)
    assert row["status"] == "ok", f"row={row}"
    assert row["uploaded"] == 1
    landed = (Path(sftp_server["home"]) / "router_sftp_test" / "a" / "b"
              / "c.txt")
    assert landed.exists(), f"missing at {landed}"
    assert landed.read_bytes() == b"router-sftp-e2e-bytes"
