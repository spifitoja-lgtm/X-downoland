# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "requests",
#     "browser-cookie3>=0.19",
# ]
# ///
"""X-downoland — pobieracz zdjęć z X.com (Twitter).

Dwa tryby:
- bez logowania: publiczny endpoint syndication.twitter.com (limit ~30 mediów)
- z logowaniem: GraphQL API + cookies (auth_token + ct0), pełna historia mediów

Cookies dociągane automatycznie z lokalnej przeglądarki (Chrome / Firefox /
Safari / Edge / Brave / Opera / Vivaldi) przez browser-cookie3.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Callable, Iterable
from urllib.parse import urlparse

import requests

APP_NAME = "X-downoland"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

if sys.platform == "win32":
    CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
else:
    CONFIG_DIR = Path.home() / ".config" / "x-downoland"
COOKIES_FILE = CONFIG_DIR / "cookies.json"
QUERY_IDS_FILE = CONFIG_DIR / "query_ids.json"

SUPPORTED_BROWSERS = [
    ("Chrome", "chrome"),
    ("Firefox", "firefox"),
    ("Edge", "edge"),
    ("Brave", "brave"),
    ("Safari", "safari"),
    ("Opera", "opera"),
    ("Vivaldi", "vivaldi"),
    ("Chromium", "chromium"),
    ("LibreWolf", "librewolf"),
    ("Arc", "arc"),
]

# Permissive features blob — covers UserByScreenName / UserMedia / UserTweets.
# X validates only required keys; extras are ignored. If X adds a NEW required
# key we'll see it in the error response and update here.
FEATURES_BLOB: dict[str, bool] = {
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
    "hidden_profile_subscriptions_enabled": True,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "premium_content_api_read_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": False,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
}

HEADERS_HTML = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# --------------------------------------------------------------------------- cookies / session
def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_cookies() -> dict | None:
    if not COOKIES_FILE.exists():
        return None
    try:
        data = json.loads(COOKIES_FILE.read_text())
        if data.get("auth_token") and data.get("ct0"):
            return data
    except Exception:
        return None
    return None


def save_cookies(auth_token: str, ct0: str) -> None:
    _ensure_config_dir()
    COOKIES_FILE.write_text(json.dumps({"auth_token": auth_token, "ct0": ct0}))
    try:
        COOKIES_FILE.chmod(0o600)
    except OSError:
        pass


def clear_cookies() -> None:
    if COOKIES_FILE.exists():
        COOKIES_FILE.unlink()


def authed_session(auth_token: str, ct0: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Authorization": f"Bearer {BEARER}",
        "x-csrf-token": ct0,
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "x-twitter-client-language": "en",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://x.com",
        "Referer": "https://x.com/",
    })
    s.cookies.set("auth_token", auth_token, domain=".x.com")
    s.cookies.set("ct0", ct0, domain=".x.com")
    return s


def import_browser_cookies(browser_name: str) -> tuple[str, str]:
    """Pull auth_token + ct0 from a locally-installed browser. Raises on failure."""
    import browser_cookie3 as bc3

    fn = getattr(bc3, browser_name, None)
    if fn is None:
        raise RuntimeError(f"browser-cookie3 nie wspiera '{browser_name}'")
    try:
        jar = fn(domain_name="x.com")
    except Exception:
        # fallback — sometimes cookies live under twitter.com
        jar = fn(domain_name="twitter.com")

    auth_token = ct0 = None
    for c in jar:
        if c.name == "auth_token":
            auth_token = c.value
        elif c.name == "ct0":
            ct0 = c.value
    if not (auth_token and ct0):
        raise RuntimeError(
            "Nie znaleziono cookies auth_token + ct0 w tej przeglądarce. "
            "Zaloguj się na x.com w tej przeglądarce i spróbuj ponownie."
        )
    return auth_token, ct0


# --------------------------------------------------------------------------- query ID discovery
def load_query_ids() -> dict[str, str]:
    if not QUERY_IDS_FILE.exists():
        return {}
    try:
        return json.loads(QUERY_IDS_FILE.read_text())
    except Exception:
        return {}


def save_query_ids(qids: dict[str, str]) -> None:
    _ensure_config_dir()
    QUERY_IDS_FILE.write_text(json.dumps(qids, indent=2))


def discover_query_ids(session: requests.Session, log: Callable[[str], None]) -> dict[str, str]:
    """Fetch the X.com bundle and extract operation→queryId mapping."""
    log("Wykrywam query ID-eki z bundla X.com…")
    home = session.get("https://x.com/home", headers={"User-Agent": UA}, timeout=30)
    home.raise_for_status()
    js_urls = sorted(set(re.findall(
        r"https://abs\.twimg\.com/responsive-web/client-web/[A-Za-z0-9._/-]+\.js",
        home.text,
    )))
    if not js_urls:
        raise RuntimeError("Nie znalazłem URL-i do bundla JS — X mógł zmienić layout.")
    qids: dict[str, str] = {}
    pat_a = re.compile(r'queryId:"([^"]+)",operationName:"([^"]+)"')
    pat_b = re.compile(r'operationName:"([^"]+)",[^}]*?queryId:"([^"]+)"')
    for u in js_urls:
        try:
            r = session.get(u, headers={"User-Agent": UA}, timeout=60)
            if not r.ok:
                continue
            for m in pat_a.finditer(r.text):
                qids[m.group(2)] = m.group(1)
            for m in pat_b.finditer(r.text):
                qids[m.group(1)] = m.group(2)
        except Exception:
            continue
    log(f"  znalazłem {len(qids)} operacji")
    save_query_ids(qids)
    return qids


def get_query_id(session: requests.Session, name: str, log: Callable[[str], None]) -> str:
    qids = load_query_ids()
    if name not in qids:
        qids = discover_query_ids(session, log)
    if name not in qids:
        raise RuntimeError(f"Nie znalazłem query ID dla '{name}' — X bundle nie zawiera tej operacji.")
    return qids[name]


# --------------------------------------------------------------------------- GraphQL calls
def gql_user_id(session: requests.Session, screen_name: str, log: Callable[[str], None]) -> str:
    qid = get_query_id(session, "UserByScreenName", log)
    url = f"https://x.com/i/api/graphql/{qid}/UserByScreenName"
    params = {
        "variables": json.dumps({"screen_name": screen_name}, separators=(",", ":")),
        "features": json.dumps(FEATURES_BLOB, separators=(",", ":")),
        "fieldToggles": json.dumps({"withAuxiliaryUserLabels": False}, separators=(",", ":")),
    }
    r = session.get(url, params=params, timeout=30)
    if r.status_code in (401, 403):
        raise RuntimeError(f"X odrzucił logowanie (HTTP {r.status_code}). Cookies wygasły — zaloguj ponownie.")
    r.raise_for_status()
    j = r.json()
    try:
        return j["data"]["user"]["result"]["rest_id"]
    except KeyError as e:
        raise RuntimeError(f"Nie znalazłem rest_id w odpowiedzi UserByScreenName: {j}") from e


def _extract_entries_and_cursor(timeline_obj: dict) -> tuple[list[dict], str | None]:
    """Walk a GraphQL timeline result; return (tweet itemContents, next cursor)."""
    instructions: list[dict] = []
    # several shapes possible — v2 etc.
    candidates = [
        timeline_obj.get("timeline_v2", {}).get("timeline", {}).get("instructions"),
        timeline_obj.get("timeline", {}).get("instructions"),
        timeline_obj.get("instructions"),
    ]
    for c in candidates:
        if isinstance(c, list):
            instructions = c
            break
    item_contents: list[dict] = []
    next_cursor: str | None = None
    for inst in instructions:
        if inst.get("type") in ("TimelineAddEntries", "TimelineAddToModule"):
            for e in inst.get("entries", []) or inst.get("moduleItems", []):
                content = e.get("content") or e.get("item") or {}
                if content.get("entryType") == "TimelineTimelineModule" or "items" in content:
                    for it in content.get("items", []):
                        ic = it.get("item", {}).get("itemContent") or it.get("itemContent")
                        if ic:
                            item_contents.append(ic)
                elif content.get("entryType") == "TimelineTimelineItem":
                    ic = content.get("itemContent")
                    if ic:
                        item_contents.append(ic)
                elif content.get("cursorType") == "Bottom":
                    next_cursor = content.get("value")
        elif inst.get("type") == "TimelineReplaceEntry":
            entry = inst.get("entry", {})
            content = entry.get("content", {})
            if content.get("cursorType") == "Bottom":
                next_cursor = content.get("value")
    return item_contents, next_cursor


def gql_user_media_page(
    session: requests.Session,
    user_id: str,
    cursor: str | None,
    log: Callable[[str], None],
) -> tuple[list[dict], str | None]:
    qid = get_query_id(session, "UserMedia", log)
    variables: dict = {
        "userId": user_id,
        "count": 100,
        "includePromotedContent": False,
        "withClientEventToken": False,
        "withBirdwatchNotes": False,
        "withVoice": True,
        "withV2Timeline": True,
    }
    if cursor:
        variables["cursor"] = cursor
    url = f"https://x.com/i/api/graphql/{qid}/UserMedia"
    params = {
        "variables": json.dumps(variables, separators=(",", ":")),
        "features": json.dumps(FEATURES_BLOB, separators=(",", ":")),
    }
    r = session.get(url, params=params, timeout=30)
    if r.status_code == 429:
        raise RateLimited(int(r.headers.get("x-rate-limit-reset", "0")))
    if r.status_code in (401, 403):
        raise RuntimeError(f"X odrzucił logowanie (HTTP {r.status_code}). Cookies wygasły — zaloguj ponownie.")
    r.raise_for_status()
    j = r.json()
    errs = j.get("errors")
    if errs and not j.get("data"):
        raise RuntimeError(f"GraphQL error: {errs}")
    try:
        timeline = j["data"]["user"]["result"].get("timeline_v2") or j["data"]["user"]["result"].get("timeline")
        if timeline is None:
            return [], None
        entries, next_cursor = _extract_entries_and_cursor({"timeline_v2": j["data"]["user"]["result"].get("timeline_v2"), "timeline": j["data"]["user"]["result"].get("timeline")})
    except (KeyError, TypeError) as e:
        raise RuntimeError(f"Niespodziewany kształt odpowiedzi UserMedia: {e}") from e
    return entries, next_cursor


class RateLimited(Exception):
    def __init__(self, reset_ts: int):
        self.reset_ts = reset_ts


# --------------------------------------------------------------------------- syndication (no-login)
def fetch_profile_html(username: str) -> str:
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}"
    r = requests.get(url, headers=HEADERS_HTML, timeout=30)
    r.raise_for_status()
    return r.text


def extract_next_data(html: str) -> dict:
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html, re.S,
    )
    if not m:
        raise RuntimeError(
            "Nie znalazłem __NEXT_DATA__ — profil prywatny / zbanowany / X zmienił format."
        )
    return json.loads(m.group(1))


# --------------------------------------------------------------------------- media extraction
def walk_media(data) -> list[dict]:
    """Walk any nested JSON; collect every media item from entities/extended_entities."""
    out: list[dict] = []
    seen_keys: set[str] = set()
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            # tweet might be node itself, or under "tweet", or under "legacy"
            tweet_candidates = [node]
            for k in ("tweet", "legacy", "tweet_results"):
                v = node.get(k)
                if isinstance(v, dict):
                    tweet_candidates.append(v)
                    if isinstance(v.get("result"), dict):
                        tweet_candidates.append(v["result"])
                        if isinstance(v["result"].get("legacy"), dict):
                            tweet_candidates.append(v["result"]["legacy"])
            for t in tweet_candidates:
                ent = t.get("entities") if isinstance(t.get("entities"), dict) else None
                ext = t.get("extended_entities") if isinstance(t.get("extended_entities"), dict) else None
                if not (ent or ext):
                    continue
                tweet_id = (
                    t.get("conversation_id_str") or t.get("id_str")
                    or str(t.get("id") or "")
                )
                created = t.get("created_at", "")
                media_lists = []
                if ext and isinstance(ext.get("media"), list):
                    media_lists.append(ext["media"])
                if ent and isinstance(ent.get("media"), list):
                    media_lists.append(ent["media"])
                for ml in media_lists:
                    for m in ml:
                        if not isinstance(m, dict):
                            continue
                        key = m.get("media_key") or m.get("id_str") or m.get("media_url_https")
                        if not key or key in seen_keys:
                            continue
                        media_url = m.get("media_url_https")
                        if not media_url:
                            continue
                        seen_keys.add(key)
                        kind = m.get("type", "photo")
                        best_video = None
                        if kind in ("video", "animated_gif"):
                            variants = (m.get("video_info") or {}).get("variants", [])
                            mp4s = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
                            if mp4s:
                                best_video = max(mp4s, key=lambda v: v.get("bitrate", 0))["url"]
                        out.append({
                            "tweet_id": tweet_id,
                            "created_at": created,
                            "url": media_url,
                            "type": kind,
                            "video_url": best_video,
                        })
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return out


# --------------------------------------------------------------------------- downloading
def upgrade_photo_url(url: str) -> str:
    base = url.split("?", 1)[0]
    return base + "?format=jpg&name=orig"


def safe_filename(url: str, tweet_id: str, ext: str | None = None) -> str:
    name = os.path.basename(urlparse(url).path) or f"{tweet_id}"
    if ext and not name.lower().endswith(ext):
        name = name.rsplit(".", 1)[0] + ext
    return name


def download_one(item: dict, out_dir: Path, include_videos: bool, log: Callable[[str], None]) -> str:
    kind = item["type"]
    tweet_id = item["tweet_id"]
    if kind == "photo":
        dl_url = upgrade_photo_url(item["url"])
        fname = safe_filename(item["url"], tweet_id)
    elif kind in ("video", "animated_gif") and include_videos and item.get("video_url"):
        dl_url = item["video_url"]
        fname = safe_filename(item["video_url"], tweet_id, ext=".mp4")
    else:
        return "skipped-video"

    out_path = out_dir / fname
    if out_path.exists() and out_path.stat().st_size > 0:
        return "exists"
    try:
        with requests.get(dl_url, headers={"User-Agent": UA}, timeout=120, stream=True) as r:
            r.raise_for_status()
            tmp = out_path.with_suffix(out_path.suffix + ".part")
            with tmp.open("wb") as f:
                for chunk in r.iter_content(64 * 1024):
                    if chunk:
                        f.write(chunk)
            tmp.rename(out_path)
        return "ok"
    except Exception as e:
        log(f"  x {fname}: {e}")
        return "fail"


# --------------------------------------------------------------------------- scraping orchestration
def collect_media_syndication(username: str, log: Callable[[str], None]) -> list[dict]:
    log(f"→ syndication.twitter.com /screen-name/{username}")
    html = fetch_profile_html(username)
    data = extract_next_data(html)
    media = walk_media(data)
    return media


def collect_media_graphql(
    session: requests.Session,
    username: str,
    max_items: int,
    log: Callable[[str], None],
    stop_flag: threading.Event,
) -> list[dict]:
    user_id = gql_user_id(session, username, log)
    log(f"→ user_id={user_id}, paginuję UserMedia…")
    collected: list[dict] = []
    cursor: str | None = None
    page = 0
    empty_pages = 0
    while not stop_flag.is_set():
        page += 1
        try:
            entries, cursor = gql_user_media_page(session, user_id, cursor, log)
        except RateLimited as rl:
            wait = max(0, rl.reset_ts - int(time.time())) or 60
            log(f"⏸  Rate limit. Czekam {wait}s do resetu…")
            for _ in range(wait):
                if stop_flag.is_set():
                    return collected
                time.sleep(1)
            continue
        page_media = walk_media(entries)
        # dedupe within run
        existing = {(m.get("video_url") or m["url"]) for m in collected}
        new = [m for m in page_media if (m.get("video_url") or m["url"]) not in existing]
        collected.extend(new)
        log(f"  strona {page}: +{len(new)} mediów (suma {len(collected)})")
        if not new:
            empty_pages += 1
            if empty_pages >= 2:
                break
        else:
            empty_pages = 0
        if not cursor:
            break
        if max_items and len(collected) >= max_items:
            log(f"  osiągnąłem limit {max_items}")
            break
    return collected


# --------------------------------------------------------------------------- GUI
class LoginDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, on_success: Callable[[], None]):
        super().__init__(parent)
        self.title(f"{APP_NAME} — Logowanie")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.on_success = on_success

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        # --- TAB 1: auto-import
        tab_auto = ttk.Frame(nb, padding=10)
        nb.add(tab_auto, text="Auto-import z przeglądarki")

        ttk.Label(
            tab_auto,
            text=(
                "Wymaga, żebyś był zalogowany na x.com w wybranej przeglądarce.\n"
                "Chrome szyfruje cookies (Keychain na macOS, DPAPI na Windows) —\n"
                "jeśli zobaczysz 'unable to get key for cookie decryption', spróbuj\n"
                "Firefox / Safari, albo użyj zakładki obok (ręczna wklejka)."
            ),
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(tab_auto, text="Przeglądarka:").grid(row=1, column=0, sticky="w", padx=(0, 6))
        self.browser_var = tk.StringVar(value=SUPPORTED_BROWSERS[0][0])
        ttk.Combobox(
            tab_auto,
            textvariable=self.browser_var,
            values=[b[0] for b in SUPPORTED_BROWSERS],
            state="readonly",
            width=20,
        ).grid(row=1, column=1, sticky="w")
        self.auto_status = tk.StringVar(value="")
        ttk.Label(tab_auto, textvariable=self.auto_status, foreground="gray", wraplength=420, justify="left").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        self.import_btn = ttk.Button(tab_auto, text="Importuj cookies", command=self._do_import)
        self.import_btn.grid(row=3, column=0, columnspan=2, sticky="e", pady=(10, 0))

        # --- TAB 2: manual paste
        tab_manual = ttk.Frame(nb, padding=10)
        nb.add(tab_manual, text="Wklej ręcznie")

        ttk.Label(
            tab_manual,
            text=(
                "Jak wyciągnąć cookies z przeglądarki:\n"
                "1. Otwórz x.com w przeglądarce (musisz być zalogowany).\n"
                "2. Naciśnij F12 (DevTools) → zakładka Application (Chrome/Edge)\n"
                "   lub Storage (Firefox).\n"
                "3. W lewym panelu: Cookies → https://x.com\n"
                "4. Znajdź wiersze 'auth_token' i 'ct0', skopiuj kolumnę Value\n"
                "   i wklej poniżej."
            ),
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        ttk.Label(tab_manual, text="auth_token:").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=2)
        self.auth_var = tk.StringVar()
        ttk.Entry(tab_manual, textvariable=self.auth_var, width=58, show="•").grid(row=1, column=1, sticky="we", pady=2)

        ttk.Label(tab_manual, text="ct0:").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=2)
        self.ct0_var = tk.StringVar()
        ttk.Entry(tab_manual, textvariable=self.ct0_var, width=58, show="•").grid(row=2, column=1, sticky="we", pady=2)

        self.manual_status = tk.StringVar(value="")
        ttk.Label(tab_manual, textvariable=self.manual_status, foreground="gray", wraplength=420, justify="left").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Button(tab_manual, text="Zapisz", command=self._do_manual).grid(
            row=4, column=0, columnspan=2, sticky="e", pady=(10, 0)
        )

        # Common cancel button
        ttk.Button(self, text="Zamknij", command=self.destroy).pack(side="right", padx=10, pady=(0, 10))

    def _finish(self) -> None:
        self.on_success()
        self.after(800, self.destroy)

    def _do_import(self) -> None:
        label = self.browser_var.get()
        bname = next((b[1] for b in SUPPORTED_BROWSERS if b[0] == label), None)
        if not bname:
            return
        self.import_btn.config(state="disabled")
        self.auto_status.set(f"Czytam cookies z {label}…")
        self.update_idletasks()
        try:
            auth_token, ct0 = import_browser_cookies(bname)
            save_cookies(auth_token, ct0)
            self.auto_status.set("✓ Zapisano. To okno zamknie się za chwilę.")
            self._finish()
        except Exception as e:
            self.auto_status.set(
                f"Błąd: {e}\n→ spróbuj innej przeglądarki, albo przejdź do zakładki 'Wklej ręcznie'."
            )
            self.import_btn.config(state="normal")

    def _do_manual(self) -> None:
        auth_token = self.auth_var.get().strip()
        ct0 = self.ct0_var.get().strip()
        if not auth_token or not ct0:
            self.manual_status.set("Oba pola są wymagane.")
            return
        if len(auth_token) < 20 or len(ct0) < 20:
            self.manual_status.set("Te wartości wyglądają na za krótkie — sprawdź czy skopiowałeś pełne Value.")
            return
        save_cookies(auth_token, ct0)
        self.manual_status.set("✓ Zapisano. To okno zamknie się za chwilę.")
        self._finish()


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(APP_NAME)
        root.geometry("820x600")
        root.minsize(680, 460)

        pad = {"padx": 10, "pady": 6}

        # login bar
        self.login_frame = ttk.LabelFrame(root, text="Logowanie", padding=8)
        self.login_frame.pack(fill="x", padx=10, pady=(10, 4))
        self.login_status = tk.StringVar()
        ttk.Label(self.login_frame, textvariable=self.login_status).pack(side="left")
        ttk.Button(self.login_frame, text="Wyloguj", command=self._logout).pack(side="right", padx=2)
        ttk.Button(self.login_frame, text="Zaloguj (import z przeglądarki)", command=self._login).pack(side="right", padx=2)

        # username
        frm = ttk.Frame(root)
        frm.pack(fill="x", **pad)
        ttk.Label(frm, text="Profil:").pack(side="left")
        ttk.Label(frm, text="@").pack(side="left")
        self.username_var = tk.StringVar()
        entry = ttk.Entry(frm, textvariable=self.username_var, width=24)
        entry.pack(side="left", padx=(0, 8))
        entry.bind("<Return>", lambda _e: self.start())
        self.videos_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="Także wideo / GIF", variable=self.videos_var).pack(side="left")
        ttk.Label(frm, text="  Limit:").pack(side="left")
        self.limit_var = tk.StringVar(value="0")
        ttk.Entry(frm, textvariable=self.limit_var, width=7).pack(side="left")
        ttk.Label(frm, text="(0 = bez limitu)").pack(side="left")

        # folder
        frm2 = ttk.Frame(root)
        frm2.pack(fill="x", **pad)
        ttk.Label(frm2, text="Folder:").pack(side="left")
        self.folder_var = tk.StringVar(value=str(Path.home() / "Downloads" / "x-downoland"))
        ttk.Entry(frm2, textvariable=self.folder_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(frm2, text="…", width=3, command=self.pick_folder).pack(side="left")
        ttk.Button(frm2, text="Otwórz", command=self.open_folder).pack(side="left", padx=(4, 0))

        # buttons
        frm3 = ttk.Frame(root)
        frm3.pack(fill="x", **pad)
        self.start_btn = ttk.Button(frm3, text="Start", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(frm3, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)

        self.progress = ttk.Progressbar(root, mode="determinate")
        self.progress.pack(fill="x", padx=10)

        self.status_var = tk.StringVar(value="Gotowy.")
        ttk.Label(root, textvariable=self.status_var, padding=(10, 4)).pack(fill="x")

        self.log_widget = scrolledtext.ScrolledText(root, height=22)
        self.log_widget.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._stop = threading.Event()
        self._log_q: queue.Queue[str] = queue.Queue()
        self.root.after(100, self._drain_log)
        self._refresh_login_state()

    # ---- login state
    def _refresh_login_state(self) -> None:
        c = load_cookies()
        if c:
            self.login_status.set("Tryb: ZALOGOWANY ✓  — pełna historia mediów dostępna")
        else:
            self.login_status.set("Tryb: bez logowania (limit ~30 mediów). Zaloguj, by pobrać wszystkie.")

    def _login(self) -> None:
        LoginDialog(self.root, on_success=self._refresh_login_state)

    def _logout(self) -> None:
        clear_cookies()
        self._refresh_login_state()
        self.log("Wylogowano (skasowano cookies).")

    # ---- logging plumbing
    def log(self, msg: str) -> None:
        self._log_q.put(msg)

    def _drain_log(self) -> None:
        try:
            while True:
                msg = self._log_q.get_nowait()
                self.log_widget.insert("end", msg + "\n")
                self.log_widget.see("end")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log)

    # ---- folder helpers
    def pick_folder(self) -> None:
        d = filedialog.askdirectory(initialdir=self.folder_var.get())
        if d:
            self.folder_var.set(d)

    def open_folder(self) -> None:
        path = self.folder_var.get()
        Path(path).mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            subprocess.run(["open", path])
        elif sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", path])

    # ---- core
    def start(self) -> None:
        username = self.username_var.get().strip().lstrip("@")
        if not username or not re.fullmatch(r"[A-Za-z0-9_]{1,15}", username):
            messagebox.showerror("Błąd", "Nieprawidłowa nazwa profilu.")
            return
        try:
            limit = int(self.limit_var.get() or "0")
            if limit < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Błąd", "Limit musi być liczbą >= 0.")
            return
        out_dir = Path(self.folder_var.get()) / username
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Błąd", f"Nie mogę utworzyć folderu: {e}")
            return
        self._stop.clear()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.progress.config(value=0, maximum=1)
        self.log_widget.delete("1.0", "end")
        threading.Thread(
            target=self._scrape,
            args=(username, out_dir, self.videos_var.get(), limit),
            daemon=True,
        ).start()

    def stop(self) -> None:
        self._stop.set()
        self.log("⏹  Przerywam…")

    def _scrape(self, username: str, out_dir: Path, include_videos: bool, limit: int) -> None:
        try:
            cookies = load_cookies()
            if cookies:
                self.log("Tryb: zalogowany (GraphQL).")
                self.status_var.set("Łączę z X.com…")
                session = authed_session(cookies["auth_token"], cookies["ct0"])
                media = collect_media_graphql(
                    session, username, limit, self.log, self._stop,
                )
            else:
                self.log("Tryb: bez logowania (syndication).")
                self.status_var.set(f"Pobieram timeline @{username}…")
                media = collect_media_syndication(username, self.log)

            # dedupe
            seen, unique = set(), []
            for m in media:
                key = m.get("video_url") or m["url"]
                if key in seen:
                    continue
                seen.add(key)
                unique.append(m)

            photos = [m for m in unique if m["type"] == "photo"]
            videos = [m for m in unique if m["type"] in ("video", "animated_gif")]
            self.log(f"✓ Znaleziono unikalnych: {len(photos)} zdjęć, {len(videos)} wideo/GIF")

            to_download = photos + (videos if include_videos else [])
            if limit:
                to_download = to_download[:limit]
            if not to_download:
                self.status_var.set("Nic do pobrania.")
                self.log("Brak mediów do pobrania.")
                return

            self.progress.config(maximum=len(to_download), value=0)
            ok = exists = fail = sv = 0
            self.log(f"⬇  Zapisuję do: {out_dir}")
            with ThreadPoolExecutor(max_workers=6) as ex:
                futs = {ex.submit(download_one, m, out_dir, include_videos, self.log): m for m in to_download}
                done = 0
                for fut in as_completed(futs):
                    if self._stop.is_set():
                        for f in futs:
                            f.cancel()
                        break
                    res = fut.result()
                    if res == "ok": ok += 1
                    elif res == "exists": exists += 1
                    elif res == "skipped-video": sv += 1
                    else: fail += 1
                    done += 1
                    self.progress.config(value=done)
                    self.status_var.set(
                        f"{done}/{len(to_download)} — nowe: {ok}, były: {exists}, błędy: {fail}"
                    )
            self.log("")
            self.log("━━ Koniec ━━")
            self.log(f"  nowe pliki: {ok}")
            self.log(f"  pominięte (już są): {exists}")
            self.log(f"  pominięte wideo (wyłączone): {sv}")
            self.log(f"  błędy: {fail}")
            self.status_var.set(f"Gotowe. Nowe: {ok}, były: {exists}, błędy: {fail}")
        except requests.HTTPError as e:
            self.log(f"x HTTP {e.response.status_code}: {e}")
            self.status_var.set("Błąd HTTP — patrz log.")
        except Exception as e:
            self.log(f"x Błąd: {e}")
            self.status_var.set("Błąd — patrz log.")
        finally:
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")


def main() -> int:
    root = tk.Tk()
    try:
        style = ttk.Style()
        if sys.platform == "darwin":
            style.theme_use("aqua")
        elif sys.platform == "win32":
            style.theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
