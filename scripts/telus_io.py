#!/usr/bin/env python3
"""Upload / download files to a TELUS Jupyter pod via the /api/contents API.

Usage:
  telus_io.py --url <pod_url> --token <token> upload <local_path> <remote_path>
  telus_io.py --url <pod_url> --token <token> download <remote_path> <local_path>

`<remote_path>` is relative to the pod's home (what JupyterLab shows as /).
Creates intermediate directories as needed on upload.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.parse
import urllib.request


def _req(method: str, url: str, token: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Authorization": f"token {token}"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read()) if method != "DELETE" else {}


def _ensure_dirs(base: str, token: str, remote_path: str):
    parts = remote_path.strip("/").split("/")
    if len(parts) <= 1:
        return
    for i in range(1, len(parts)):
        sub = "/".join(parts[:i])
        url = f"{base}/api/contents/{urllib.parse.quote(sub)}?token={token}"
        try:
            _req("PUT", url, token, {"type": "directory", "format": None, "content": None})
        except Exception:
            pass  # already exists → 4xx, ignore


def upload(base: str, token: str, local: str, remote: str):
    with open(local, "rb") as f:
        content = base64.b64encode(f.read()).decode()
    _ensure_dirs(base, token, remote)
    url = f"{base}/api/contents/{urllib.parse.quote(remote.lstrip('/'))}?token={token}"
    body = {"type": "file", "format": "base64", "content": content}
    _req("PUT", url, token, body)
    size = os.path.getsize(local)
    print(f"uploaded {local} -> {remote} ({size:,} bytes)")


def download(base: str, token: str, remote: str, local: str):
    url = f"{base}/api/contents/{urllib.parse.quote(remote.lstrip('/'))}?token={token}&format=base64"
    body = _req("GET", url, token)
    content = body.get("content", "")
    if body.get("format") == "base64":
        raw = base64.b64decode(content)
    else:
        raw = content.encode()
    os.makedirs(os.path.dirname(os.path.abspath(local)) or ".", exist_ok=True)
    with open(local, "wb") as f:
        f.write(raw)
    print(f"downloaded {remote} -> {local} ({len(raw):,} bytes)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="Pod base URL (e.g., https://notebook-xxx.console.ai.telus.com)")
    ap.add_argument("--token", required=True, help="Jupyter token")
    sub = ap.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("upload")
    up.add_argument("local")
    up.add_argument("remote")

    dn = sub.add_parser("download")
    dn.add_argument("remote")
    dn.add_argument("local")

    args = ap.parse_args()
    base = args.url.rstrip("/")

    if args.cmd == "upload":
        upload(base, args.token, args.local, args.remote)
    else:
        download(base, args.token, args.remote, args.local)


if __name__ == "__main__":
    main()
