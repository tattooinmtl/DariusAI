"""The web and voice slash commands' real work (`/fetch`, `/wiki`, `/speak`…).

Kept out of commands.py so that module stays a table of handlers. Everything
here uses free public endpoints — no keys — and fails with a readable
message instead of an exception.
"""

from __future__ import annotations

import html
import re
import subprocess
import sys
from urllib.parse import quote, urlparse

MAX_BYTES = 2_000_000
MAX_TEXT = 4000
UA = {"User-Agent": "DariusAI/1.0 (+https://github.com/tattooinmtl/DariusAI)"}


def _get(url: str, timeout: float = 15.0, as_json: bool = False):
    import httpx
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=UA) as c:
        r = c.get(url)
        r.raise_for_status()
        if as_json:
            return r.json()
        content = r.content[:MAX_BYTES]
        return content.decode(r.encoding or "utf-8", errors="replace"), r.headers.get("content-type", "")


def normalise_url(raw: str) -> str:
    raw = raw.strip().strip("<>\"'")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
        raw = "https://" + raw
    parts = urlparse(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"not a web address: {raw}")
    return raw


def html_to_text(page: str) -> tuple[str, str]:
    """(title, readable text) — scripts, styles and tags stripped."""
    title_m = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    title = html.unescape(re.sub(r"\s+", " ", title_m.group(1))).strip() if title_m else ""
    body = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", page)
    body = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|section|article)>", "\n", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = html.unescape(body)
    lines = [re.sub(r"[ \t ]+", " ", l).strip() for l in body.splitlines()]
    text = "\n".join(l for l in lines if l)
    return title, re.sub(r"\n{3,}", "\n\n", text)


def fetch_page(raw_url: str) -> str:
    url = normalise_url(raw_url)
    body, ctype = _get(url)
    if "html" in ctype or body.lstrip().startswith("<"):
        title, text = html_to_text(body)
    else:
        title, text = "", body
    head = (f"# {title}\n{url}\n\n" if title else f"{url}\n\n")
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + f"\n\n… {len(text) - MAX_TEXT:,} more characters"
    return head + text


def wiki_summary(topic: str) -> str:
    slug = quote(topic.strip().replace(" ", "_"))
    try:
        data = _get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}", as_json=True)
    except Exception:
        hits = _get("https://en.wikipedia.org/w/api.php?action=opensearch&limit=5&format=json&search="
                    + quote(topic), as_json=True)
        titles = hits[1] if isinstance(hits, list) and len(hits) > 1 else []
        if not titles:
            return f"No Wikipedia article for {topic!r}."
        return f"No article named {topic!r}. Did you mean: " + ", ".join(titles) + "?"
    out = f"# {data.get('title', topic)}\n"
    if data.get("description"):
        out += f"_{data['description']}_\n"
    out += "\n" + (data.get("extract") or "(no summary)")
    page = (data.get("content_urls") or {}).get("desktop", {}).get("page")
    return out + (f"\n\n{page}" if page else "")


def github_repo(spec: str) -> str:
    spec = spec.strip()
    m = re.search(r"github\.com/([^/\s]+)/([^/\s#?]+)", spec)
    owner_repo = f"{m.group(1)}/{m.group(2)}" if m else spec
    if owner_repo.endswith(".git"):
        owner_repo = owner_repo[:-4]
    if owner_repo.count("/") != 1:
        return "Give a repository as owner/name, e.g. /github tattooinmtl/DariusAI"
    try:
        r = _get(f"https://api.github.com/repos/{owner_repo}", as_json=True)
    except Exception as exc:
        return f"Couldn't read {owner_repo} from GitHub: {exc}"
    lines = [f"# {r.get('full_name')}", r.get("description") or "", "",
             f"★ {r.get('stargazers_count', 0):,}  ·  forks {r.get('forks_count', 0):,}  ·  "
             f"open issues {r.get('open_issues_count', 0):,}  ·  {r.get('language') or 'no main language'}",
             f"default branch {r.get('default_branch')}  ·  updated {str(r.get('pushed_at', ''))[:10]}"
             + (f"  ·  license {r['license']['spdx_id']}" if r.get("license") else ""),
             r.get("html_url", "")]
    return "\n".join(l for l in lines if l is not None)


def youtube_info(raw_url: str) -> str:
    url = normalise_url(raw_url)
    try:
        data = _get("https://www.youtube.com/oembed?format=json&url=" + quote(url, safe=""), as_json=True)
    except Exception as exc:
        return f"Couldn't read that video: {exc}"
    return f"# {data.get('title')}\nby {data.get('author_name')}\n{url}"


# ---- voice output (Windows' built-in speech engine) -------------------------

_speaker: subprocess.Popen | None = None


def speak(text: str, volume: int = 100) -> str:
    """Read `text` aloud in the background with System.Speech (no extra
    install). Any speech already playing is stopped first."""
    global _speaker
    if sys.platform != "win32":
        return "Speech output needs Windows' speech engine."
    stop_speaking()
    from .sandbox import quiet_creationflags
    script = ("Add-Type -AssemblyName System.Speech; "
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$s.Volume = {max(0, min(100, int(volume)))}; "
              "$s.Speak([Console]::In.ReadToEnd())")
    _speaker = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=quiet_creationflags())
    try:
        _speaker.stdin.write(text.encode("utf-8", errors="replace"))
        _speaker.stdin.close()
    except OSError:
        pass
    return f"Speaking {len(text)} characters — /stop to stop."


def stop_speaking() -> bool:
    global _speaker
    proc, _speaker = _speaker, None
    if proc is not None and proc.poll() is None:
        proc.kill()
        return True
    return False
