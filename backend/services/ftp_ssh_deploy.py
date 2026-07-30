"""
services/ftp_ssh_deploy.py — Iter 366 · FTP + SSH deploy targets

Adds two deploy backends alongside Vercel:

  - FTP  (paramiko-free, uses stdlib ftplib) — for classic PHP / static
         hosting (Bluehost, Hostinger, GoDaddy shared plans).
  - SSH  (paramiko) — for VPS / bare metal (rsync-over-SSH pattern).

Both surface via `POST /deploy/run` with `target="ftp"` or `"ssh"`.
Credentials are stored per-project encrypted in db.deploy_targets
(the existing collection).

NOTE: This is a MINIMAL working skeleton — enough to validate the
target selection + credential flow. Full production-hardening (retry,
progress SSE, dry-run) is a follow-on.
"""
from __future__ import annotations

import ftplib
import io
import logging
import os
import socket
from typing import Optional

logger = logging.getLogger("aurem.ftp_ssh_deploy")


async def deploy_via_ftp(
    *,
    host:       str,
    port:       int = 21,
    user:       str,
    password:   str,
    remote_dir: str,
    files:      dict[str, bytes],
    tls:        bool = True,
) -> dict:
    """Uploads a dict of {remote_relative_path: bytes} to an FTP server.

    Uses FTPS (FTP_TLS) by default — plain FTP transmits creds in the
    clear so we only fall back to plain if `tls=False` is explicit.
    """
    uploaded = 0
    errors: list[str] = []
    try:
        ftp_cls = ftplib.FTP_TLS if tls else ftplib.FTP
        # `timeout` is critical — no infinite hang on a bad host.
        with ftp_cls(host, timeout=30) as ftp:
            ftp.connect(host, port, timeout=30)
            ftp.login(user, password)
            if tls:
                ftp.prot_p()          # secure data channel
            try:
                ftp.cwd(remote_dir)
            except ftplib.error_perm:
                # Create the target dir on first deploy.
                _mkdirs_ftp(ftp, remote_dir)
                ftp.cwd(remote_dir)

            for path, data in files.items():
                try:
                    ftp.storbinary(f"STOR {path}", io.BytesIO(data))
                    uploaded += 1
                except ftplib.all_errors as e:
                    errors.append(f"{path}: {e}")
    except (socket.timeout, ftplib.all_errors, OSError) as e:
        return {"ok": False, "uploaded": uploaded,
                "errors": errors, "fatal": str(e)[:200]}
    return {"ok": len(errors) == 0, "uploaded": uploaded,
            "errors": errors, "target": "ftp"}


def _mkdirs_ftp(ftp: ftplib.FTP, path: str) -> None:
    parts = [p for p in path.split("/") if p]
    for i in range(1, len(parts) + 1):
        d = "/".join(parts[:i])
        try:
            ftp.mkd("/" + d)
        except ftplib.error_perm:
            pass


async def deploy_via_ssh(
    *,
    host:       str,
    port:       int = 22,
    user:       str,
    key_pem:    Optional[str] = None,   # private-key PEM string
    password:   Optional[str] = None,   # fallback
    remote_dir: str,
    files:      dict[str, bytes],
) -> dict:
    """rsync-style upload via paramiko SFTP. Prefers key auth over
    password. Returns per-file success counts."""
    try:
        import paramiko                              # type: ignore
    except ImportError:
        return {"ok": False, "reason": "paramiko_not_installed",
                "install_hint": "pip install paramiko"}

    uploaded = 0
    errors: list[str] = []
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs = {"hostname": host, "port": port, "username": user,
                           "timeout": 30}
        if key_pem:
            from io import StringIO
            connect_kwargs["pkey"] = paramiko.RSAKey.from_private_key(
                StringIO(key_pem))
        elif password:
            connect_kwargs["password"] = password
        else:
            return {"ok": False, "reason": "no_auth_supplied"}

        client.connect(**connect_kwargs)
        sftp = client.open_sftp()
        try:
            _sftp_mkdirs(sftp, remote_dir)
            for rel, data in files.items():
                full = f"{remote_dir.rstrip('/')}/{rel}"
                try:
                    # Ensure parent dir.
                    parent = os.path.dirname(full)
                    if parent and parent != remote_dir:
                        _sftp_mkdirs(sftp, parent)
                    with sftp.open(full, "wb") as fh:
                        fh.write(data)
                    uploaded += 1
                except Exception as e:              # noqa: BLE001
                    errors.append(f"{rel}: {e}")
        finally:
            sftp.close()
            client.close()
    except Exception as e:                          # noqa: BLE001
        return {"ok": False, "uploaded": uploaded,
                "errors": errors, "fatal": str(e)[:200]}
    return {"ok": len(errors) == 0, "uploaded": uploaded,
            "errors": errors, "target": "ssh"}


def _sftp_mkdirs(sftp, path: str) -> None:
    parts = [p for p in path.split("/") if p]
    cur = ""
    for p in parts:
        cur = f"{cur}/{p}"
        try:
            sftp.mkdir(cur)
        except IOError:
            pass  # exists
