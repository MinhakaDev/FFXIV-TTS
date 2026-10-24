"""Checks GitHub Releases for a newer build.

Phase 1 only *detects* an update and points the user at the download page - it does
not replace anything itself. A dev/source build (version "0.0.0-dev") is skipped
entirely, before any network call, so running from source never nags.
"""

import json
import re
import ssl
import threading
import urllib.request

import certifi

from version import __version__

RELEASES_API = "https://api.github.com/repos/MinhakaDev/FFXIV-TTS/releases/latest"
RELEASES_PAGE = "https://github.com/MinhakaDev/FFXIV-TTS/releases/latest"

# Anchored on purpose: a released tag is exactly "vX.Y.Z", so the "0.0.0-dev" default
# (and any source/local build) fails to parse and is treated as "no version" - which
# makes check() bail before any network call instead of nagging every dev run.
_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def _parse(tag):
    """Extract (major, minor, patch) from a released tag like 'v0.2.1', else None."""
    match = _VERSION_RE.fullmatch(tag or "")
    return tuple(int(part) for part in match.groups()) if match else None


def _tls_context():
    """The system's CA certificates plus certifi's bundle.

    The Linux release bundles the CI runner's (Ubuntu's) OpenSSL, which looks for CA
    certificates under /usr/lib/ssl. Arch, Fedora and SteamOS keep theirs elsewhere, so
    with the system store alone every request failed verification there and the check
    went silent. certifi ships inside the build, so its bundle is always there.
    """
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


def _latest_release(timeout):
    request = urllib.request.Request(
        RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "ffxiv-tts"},
    )
    with urllib.request.urlopen(request, timeout=timeout, context=_tls_context()) as response:
        data = json.load(response)
    return data.get("tag_name"), data.get("html_url") or RELEASES_PAGE


def check(timeout=6):
    """Return (latest_tag, url) when a newer release exists, else None. Never raises.

    Returns None immediately - no network - for a dev/source build, since those are
    updated with git, not by downloading a binary.
    """
    current = _parse(__version__)
    if current is None:
        return None
    try:
        tag, url = _latest_release(timeout)
    except Exception:
        return None  # offline, rate-limited, API change - stay quiet, never block the app
    latest = _parse(tag)
    if latest and latest > current:
        return tag, url
    return None


def check_async(callback):
    """Run check() on a daemon thread and hand the result to callback(result).

    callback runs on that worker thread, so a Tk caller must marshal back to the UI
    thread itself (e.g. widget.after(0, ...)).
    """
    threading.Thread(target=lambda: callback(check()), daemon=True).start()
