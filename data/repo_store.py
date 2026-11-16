"""Shared, lasting storage for things people add in the app (uploaded results,
concall summaries): small JSON files on the repo's `data` branch, next to the
NSE filings the GitHub Action collects.

    read_json("results/HDFCBANK.json")          -> dict | None
    write_json("results/HDFCBANK.json", {...}, "Add HDFCBANK Q2 FY27 results")

Reading works for everyone (public repo). Writing needs a GitHub token with
"Contents: read and write" on this repo only — GITHUB_TOKEN in .env locally or
in the Streamlit Secrets. Set EQUITYLENS_DATA_DIR to use a local folder instead
(tests, offline work).
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

import requests

import config
from data.common import USER_AGENT

log = logging.getLogger("equitylens.repo_store")
API = "https://api.github.com"


class StoreError(RuntimeError):
    """Couldn't save; the message is safe to show to the user."""


def _local_dir() -> Path | None:
    d = os.getenv("EQUITYLENS_DATA_DIR") or config.DATA_DIR
    return Path(d) if d else None


def _headers(raw: bool = False) -> dict:
    h = {"User-Agent": USER_AGENT, "X-GitHub-Api-Version": "2022-11-28",
         "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"}
    token = config.github_token()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _url(path: str) -> str:
    return f"{API}/repos/{config.DATA_REPO}/contents/{path}"


def can_write() -> bool:
    return _local_dir() is not None or bool(config.github_token())


def read_text(path: str) -> str | None:
    """File contents, or None if it doesn't exist yet."""
    local = _local_dir()
    if local:
        p = local / path
        return p.read_text() if p.exists() else None
    if config.github_token():
        # The API has no CDN delay, so a file saved a moment ago is visible at once.
        r = requests.get(_url(path), params={"ref": config.DATA_BRANCH},
                         headers=_headers(raw=True), timeout=20)
    else:
        r = requests.get(f"https://raw.githubusercontent.com/{config.DATA_REPO}/"
                         f"{config.DATA_BRANCH}/{path}", headers={"User-Agent": USER_AGENT},
                         timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.text


def read_json(path: str):
    text = read_text(path)
    return None if text is None else json.loads(text)


def write_text(path: str, text: str, message: str) -> None:
    local = _local_dir()
    if local:
        p = local / path
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(text)
        tmp.replace(p)
        return
    if not config.github_token():
        raise StoreError("Saving needs a GitHub token (GITHUB_TOKEN) in the app's secrets.")

    body = {"message": message, "branch": config.DATA_BRANCH,
            "content": base64.b64encode(text.encode()).decode(),
            "committer": {"name": "equitylens-bot",
                          "email": "41898282+github-actions[bot]@users.noreply.github.com"}}
    for attempt in (1, 2):                     # the collector may have pushed in between
        r = requests.get(_url(path), params={"ref": config.DATA_BRANCH}, headers=_headers(),
                         timeout=20)
        if r.status_code == 200:
            body["sha"] = r.json()["sha"]
        elif r.status_code != 404:
            raise StoreError(f"Couldn't reach GitHub to save ({r.status_code}).")
        r = requests.put(_url(path), json=body, headers=_headers(), timeout=30)
        if r.status_code in (200, 201):
            return
        if r.status_code in (409, 422) and attempt == 1:
            continue
        if r.status_code in (401, 403):
            raise StoreError("GitHub refused the save: check that GITHUB_TOKEN is valid and "
                             "has Contents: read and write on this repository.")
        log.warning("save %s failed: %s %s", path, r.status_code, r.text[:200])
        raise StoreError(f"GitHub didn't accept the save ({r.status_code}).")


def write_json(path: str, value, message: str) -> None:
    write_text(path, json.dumps(value, indent=1, ensure_ascii=False) + "\n", message)
