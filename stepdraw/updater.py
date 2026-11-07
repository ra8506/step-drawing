"""Self-update from GitHub Releases.

Each release carries `STEP-Drawing-<version>-Setup.exe`. The app compares its version with the
latest release; if newer, it downloads the installer and runs it silently (per-user install, no
admin prompt), then exits so the files can be replaced. The installer relaunches the app.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass

from . import GITHUB_REPO, __version__

API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"


class ReleasesNotPublic(Exception):
    """The repository is private, so releases can't be read without signing in."""


@dataclass
class Release:
    version: str
    notes: str
    url: str  # installer download URL
    page: str


def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def is_newer(remote: str, local: str = __version__) -> bool:
    return _vtuple(remote) > _vtuple(local)


def latest_release(timeout: float = 8.0) -> Release | None:
    req = urllib.request.Request(API, headers={"User-Agent": "STEP-Drawing-Updater", "Accept": "application/vnd.github+json"})
    import urllib.error

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:  # private repo (or no release yet)
            raise ReleasesNotPublic() from e
        raise
    for a in data.get("assets", []):
        if a["name"].lower().endswith("setup.exe"):
            return Release(data["tag_name"].lstrip("v"), data.get("body") or "", a["browser_download_url"], data.get("html_url", ""))
    return None


def check() -> Release | None:
    """Newer release, or None if up to date. Raises on network errors."""
    rel = latest_release()
    return rel if rel and is_newer(rel.version) else None


def download(rel: Release, progress=None) -> str:
    path = os.path.join(tempfile.gettempdir(), f"STEP-Drawing-{rel.version}-Setup.exe")
    req = urllib.request.Request(rel.url, headers={"User-Agent": "STEP-Drawing-Updater"})
    with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return path


def run_installer(path: str):
    """Start the installer detached; caller must quit the app right after."""
    args = [path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(args, close_fds=True, creationflags=flags)
