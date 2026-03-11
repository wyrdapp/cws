"""Webshare API synchronous client (no external dependencies beyond requests)."""

import hashlib
import logging
from xml.etree import ElementTree

import requests

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure-Python MD5-crypt (no passlib needed)
# ---------------------------------------------------------------------------

_ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _to64(value: int, length: int) -> str:
    result = []
    for _ in range(length):
        result.append(_ITOA64[value & 0x3F])
        value >>= 6
    return "".join(result)


def _md5crypt(password: str, salt: str) -> str:
    """Compute Unix MD5-crypt hash ($1$salt$hash)."""
    pw = password.encode("utf-8")

    # Extract bare salt (strip $1$ prefix and trailing $hash if present)
    s = salt
    if s.startswith("$1$"):
        s = s[3:]
    if "$" in s:
        s = s[: s.index("$")]
    s = s[:8].encode("utf-8")

    magic = b"$1$"

    # Step 1 – secondary digest
    b = hashlib.md5(pw + s + pw).digest()

    # Step 2 – primary digest
    a = hashlib.md5()
    a.update(pw)
    a.update(magic)
    a.update(s)
    plen = len(pw)
    while plen > 0:
        a.update(b[: min(16, plen)])
        plen -= 16
    i = len(pw)
    while i:
        a.update(b"\x00" if (i & 1) else pw[:1])
        i >>= 1
    final = a.digest()

    # Step 3 – 1000 rounds
    for i in range(1000):
        c = hashlib.md5()
        c.update(pw if (i & 1) else final)
        if i % 3:
            c.update(s)
        if i % 7:
            c.update(pw)
        c.update(final if (i & 1) else pw)
        final = c.digest()

    # Step 4 – encode
    f = final
    encoded = (
        _to64((f[0] << 16) | (f[6] << 8) | f[12], 4)
        + _to64((f[1] << 16) | (f[7] << 8) | f[13], 4)
        + _to64((f[2] << 16) | (f[8] << 8) | f[14], 4)
        + _to64((f[3] << 16) | (f[9] << 8) | f[15], 4)
        + _to64((f[4] << 16) | (f[10] << 8) | f[5], 4)
        + _to64(f[11], 2)
    )
    return f"$1${s.decode()}${encoded}"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def _parse_file_el(el: ElementTree.Element) -> dict:
    """Extract all available fields from a <file> XML element."""
    return {
        "ident": el.findtext("ident") or "",
        "name": el.findtext("name") or "",
        "size": int(el.findtext("size") or 0),
        "type": el.findtext("type") or "",
        "img": el.findtext("img") or "",
        "stripe": el.findtext("stripe") or "",
        "stripe_count": int(el.findtext("stripe_count") or 0),
        "positive_votes": int(el.findtext("positive_votes") or 0),
        "negative_votes": int(el.findtext("negative_votes") or 0),
        "password": el.findtext("password") == "1",
        "queued": el.findtext("queued") == "1",
        "copyrighted": el.findtext("copyrighted") == "1",
    }


class WebshareClient:
    BASE = "https://webshare.cz/api"

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.token: str | None = None
        self._session = requests.Session()
        self._session.headers.update({"Accept": "text/xml; charset=UTF-8"})

    def _post(self, endpoint: str, data: dict | None = None) -> ElementTree.Element:
        payload = dict(data or {})
        if self.token:
            payload["wst"] = self.token
        r = self._session.post(f"{self.BASE}/{endpoint}/", data=payload, timeout=30)
        return ElementTree.fromstring(r.text)

    def _authed(self, endpoint: str, data: dict | None = None) -> ElementTree.Element:
        if not self.token:
            self.login()
        root = self._post(endpoint, data)
        if root.findtext("status") == "FATAL" and "Access denied" in (root.findtext("message") or ""):
            self.login()
            root = self._post(endpoint, data)
        return root

    def login(self) -> str:
        root = self._post("salt", {"username_or_email": self.username})
        salt = root.findtext("salt")
        if not salt:
            raise RuntimeError("Webshare: bad username or no salt returned")
        md5_hash = _md5crypt(self.password, salt)
        sha1_hash = hashlib.sha1(md5_hash.encode("utf-8")).hexdigest()
        root = self._post("login", {
            "username_or_email": self.username,
            "password": sha1_hash,
            "keep_logged_in": 1,
        })
        if root.findtext("status") != "OK":
            raise RuntimeError(f"Webshare login failed: {root.findtext('message')}")
        self.token = root.findtext("token")
        log.info("Webshare login OK")
        return self.token

    # -- search -------------------------------------------------------------

    def search(self, query: str, category: str = "video", limit: int = 25) -> list[dict]:
        root = self._authed("search", {
            "what": query,
            "category": category,
            "sort": "rating",
            "limit": limit,
        })
        return [_parse_file_el(el) for el in root.findall("file")]

    # -- file operations ----------------------------------------------------

    def file_link(self, ident: str) -> str:
        root = self._authed("file_link", {
            "ident": ident,
            "download_type": "video_stream",
            "force_https": 1,
        })
        if root.findtext("status") != "OK":
            msg = root.findtext("message") or "neznámá chyba"
            raise RuntimeError(f"file_link error: {msg}")
        link = root.findtext("link") or ""
        if "?error=" in link or "&error=" in link:
            import re
            m = re.search(r"[?&]error=([^&/]+)", link)
            err = m.group(1) if m else "UNKNOWN"
            if err in ("UNKNOWN", "NOT_ALLOWED", "LIMIT_EXCEEDED"):
                raise RuntimeError(
                    "Webshare vyžaduje VIP účet pro streamování.\n"
                    "Pořiď si předplatné na webshare.cz."
                )
            raise RuntimeError(f"Webshare link error: {err}")
        return link

    def file_info(self, ident: str) -> dict:
        root = self._authed("file_info", {"ident": ident})
        return {
            "name": root.findtext("name") or "",
            "description": root.findtext("description") or "",
            "size": int(root.findtext("size") or 0),
            "type": root.findtext("type") or "",
            "adult": root.findtext("adult") == "1",
            "copyrighted": root.findtext("copyrighted") == "1",
            "available": root.findtext("available") == "1",
            "positive_votes": int(root.findtext("positive_votes") or 0),
            "negative_votes": int(root.findtext("negative_votes") or 0),
            "password": root.findtext("password") == "1",
            "stripe": root.findtext("stripe") or "",
            "stripe_count": int(root.findtext("stripe_count") or 0),
        }

    # -- similar files ------------------------------------------------------

    def similar_files(self, ident: str) -> dict:
        root = self._authed("similar_files", {"ident": ident})
        result: dict[str, list[dict]] = {"subtitles": [], "next_episode": [], "similar": []}
        for section in ("subtitles", "next_episode", "similar"):
            node = root.find(section)
            if node is not None:
                result[section] = [_parse_file_el(el) for el in node.findall("file")]
        if not any(result.values()):
            result["similar"] = [_parse_file_el(el) for el in root.findall("file")]
        return result

    # -- tied files (bundled subtitles etc.) --------------------------------

    def tied_files(self, ident: str) -> list[dict]:
        root = self._authed("tied_files", {"ident": ident})
        return [_parse_file_el(el) for el in root.findall("file")]

    # -- comments -----------------------------------------------------------

    def file_comments(self, ident: str) -> list[dict]:
        root = self._authed("file_comments", {"ident": ident})
        comments = []
        for el in root.findall("comment"):
            comments.append({
                "ident": el.findtext("ident") or "",
                "body": el.findtext("body") or "",
                "username": el.findtext("username") or "",
                "created": el.findtext("created") or "",
                "positive_votes": int(el.findtext("positive_votes") or 0),
                "negative_votes": int(el.findtext("negative_votes") or 0),
            })
        return comments

    # -- suggest (autocomplete) ---------------------------------------------

    def suggest(self, query: str, limit: int = 10) -> list[str]:
        root = self._authed("suggest", {"what": query, "limit": limit})
        return [el.findtext("value") or "" for el in root.findall("suggestion") if el.findtext("value")]
