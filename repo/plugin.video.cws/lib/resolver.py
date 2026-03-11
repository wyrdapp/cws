"""
Filename parsing (regex-based, no external deps), scoring and title matching.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_IMDB_RE = re.compile(r"(tt\d{7,})")
_YEAR_DESC = re.compile(r"\b(19[0-9]{2}|20[0-9]{2})\b")


def parse_description(description: str) -> dict:
    """Extract structured metadata from a Webshare file description."""
    result: dict = {}
    if not description:
        return result
    m = _IMDB_RE.search(description)
    if m:
        result["imdb_id"] = m.group(1)
    years = _YEAR_DESC.findall(description)
    if years:
        result["year"] = years[0]
    return result

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_RES = re.compile(r"\b(2160p|4[Kk]|1080[pi]|720p|576p|480p)\b", re.I)
_SRC = re.compile(
    r"\b(Blu-?ray|BDRip|BDRemux|REMUX|WEB-?DL|WEBRip|HDTV|DSR|DVDRip|DVD|"
    r"Telesync|TS(?=\b)|CAM|SCR|Screener)\b", re.I
)
_VCODEC = re.compile(r"\b(x265|x264|HEVC|H[\.\-]?265|H[\.\-]?264|AVC|XviD|DivX)\b", re.I)
_ACODEC = re.compile(
    r"\b(DTS-HD\s*MA|TrueHD|Atmos|DTS-HD|DTS|E-AC-?3|EAC3|AC-?3|"
    r"Dolby\s*Digital\s*Plus|Dolby\s*Digital|AAC|MP3)\b", re.I
)
_YEAR   = re.compile(r"\b(19[0-9]{2}|20[0-9]{2})\b")
# S01E01 / s01e01  nebo  1x01 / 01x01  nebo  Season 1 Episode 01
_SE     = re.compile(
    r"(?:[Ss](\d{1,2})[Ee](\d{1,2})"
    r"|(?<!\d)(\d{1,2})[xX](\d{1,2})(?!\d)"
    r"|[Ss]eason\s+(\d{1,2})\s+[Ee]pisode\s+(\d{1,2}))",
    re.I
)
_CZ     = re.compile(r"\b(CZ|czech|cze|dabing|dab|titulky|cs)\b", re.I)
_SK     = re.compile(r"\b(SK|slovak|svk)\b", re.I)
_NOISE  = re.compile(
    r"\b(cz|sk|czech|slovak|dabing|dab|titulky|tit|sub|subs|dubbed|dual|"
    r"multi|eng|hun|ger|fra|custom|hdtv|proper|repack|extended|imax|"
    r"remastered|edition|directors?\s*cut|unrated)\b", re.I
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_filename(name: str) -> dict:
    res     = _RES.search(name)
    src     = _SRC.search(name)
    vcodec  = _VCODEC.search(name)
    acodec  = _ACODEC.search(name)
    year    = _YEAR.search(name)
    se      = _SE.search(name)

    # Rozlišíme S01E01 (skupiny 1,2) vs 1x01 (skupiny 3,4) vs Season X Episode Y (skupiny 5,6)
    if se:
        if se.group(1) is not None:
            season_num, episode_num = int(se.group(1)), int(se.group(2))
        elif se.group(3) is not None:
            season_num, episode_num = int(se.group(3)), int(se.group(4))
        else:
            season_num, episode_num = int(se.group(5)), int(se.group(6))
    else:
        season_num = episode_num = None

    # Extract title: everything before the first known tag
    stop = re.search(
        r"\b(2160p|4[Kk]|1080[pi]|720p|576p|480p|Blu-?ray|WEB-?DL|WEBRip|"
        r"HDTV|DVDRip|DVD|x265|x264|HEVC|XviD|19[0-9]{2}|20[0-9]{2}|"
        r"[Ss]\d{1,2}[Ee]\d{1,2}|\d{1,2}[xX]\d{1,2})\b", name, re.I
    )
    if stop:
        raw_title = name[: stop.start()]
    else:
        raw_title = re.sub(r"\.[a-z]{2,4}$", "", name, flags=re.I)
    title = re.sub(r"[\.\-_]", " ", raw_title).strip()

    languages: list[str] = []
    if _CZ.search(name):
        languages.append("czech")
    if _SK.search(name):
        languages.append("slovak")

    return {
        "title": title,
        "year": int(year.group(1)) if year else None,
        "screen_size": res.group(1).upper().replace("4K", "2160p") if res else None,
        "source": src.group(1) if src else None,
        "video_codec": vcodec.group(1) if vcodec else None,
        "audio_codec": acodec.group(1) if acodec else None,
        "season": season_num,
        "episode": episode_num,
        "language": languages,
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

_RES_SCORE   = {"2160P": 100, "1080P": 75, "1080I": 70, "720P": 50, "576P": 30, "480P": 20}
_SRC_SCORE   = {
    "BDRemux": 55, "REMUX": 55, "Blu-ray": 50, "BluRay": 50, "Blu-Ray": 50,
    "BDRip": 45, "WEB-DL": 40, "WEBRip": 35, "HDTV": 25,
    "DVDRip": 15, "DVD": 12, "Telesync": -40, "TS": -40, "CAM": -50,
}
_VCODEC_SCORE = {"x265": 25, "HEVC": 25, "H.265": 25, "x264": 15, "H.264": 15, "AVC": 15, "XviD": 3}
_ACODEC_SCORE = {
    "DTS-HD MA": 25, "TrueHD": 25, "Atmos": 25, "DTS-HD": 20, "DTS": 15,
    "E-AC-3": 12, "EAC3": 12, "AC-3": 10, "Dolby Digital Plus": 12,
    "Dolby Digital": 10, "AAC": 7, "MP3": 3,
}


def calculate_score(file_data: dict, parsed: dict) -> int:
    score = 0
    score += _RES_SCORE.get((parsed.get("screen_size") or "").upper(), 15)
    score += _SRC_SCORE.get(parsed.get("source") or "", 8)
    score += _VCODEC_SCORE.get(parsed.get("video_codec") or "", 5)
    score += _ACODEC_SCORE.get(parsed.get("audio_codec") or "", 3)

    pos = file_data.get("positive_votes", 0)
    neg = file_data.get("negative_votes", 0)
    if pos + neg > 0:
        score += int((pos / (pos + neg)) * 30)

    size_gb = file_data.get("size", 0) / (1024 ** 3)
    score += min(int(size_gb * 4), 20)

    if "czech" in (parsed.get("language") or []):
        score += 40
    if "slovak" in (parsed.get("language") or []):
        score += 20

    if file_data.get("password"):
        score -= 1000

    return score


# ---------------------------------------------------------------------------
# Title matching
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _clean(title: str) -> str:
    return " ".join(_NOISE.sub(" ", title).split()).strip()


def matches_title(
    parsed: dict,
    title: str,
    original_title: str,
    year: str | None = None,
) -> bool:
    raw = parsed.get("title", "")
    if not raw:
        return False
    cleaned = _clean(raw)
    candidates = [title, original_title]

    matched = False
    for c in candidates:
        if _norm(c) in _norm(raw) or _norm(cleaned) in _norm(c) or _norm(c) in _norm(cleaned):
            matched = True
            break
        if _sim(raw, c) >= 0.65 or _sim(cleaned, c) >= 0.65:
            matched = True
            break
    if not matched:
        return False

    if year and parsed.get("year"):
        try:
            if abs(int(parsed["year"]) - int(year)) > 1:
                return False
        except (ValueError, TypeError):
            pass
    return True


def matches_episode(parsed: dict, season: int, episode: int) -> bool:
    ps, pe = parsed.get("season"), parsed.get("episode")
    return ps == season and pe == episode


# ---------------------------------------------------------------------------
# Stream label for UI
# ---------------------------------------------------------------------------

_LANG_TAG = {"czech": "[CZ]", "slovak": "[SK]"}


def stream_label(parsed: dict, file_data: dict) -> str:
    flags = " ".join(_LANG_TAG[l] for l in (parsed.get("language") or []) if l in _LANG_TAG)
    parts = []
    if parsed.get("screen_size"):
        parts.append(parsed["screen_size"])
    if parsed.get("source"):
        parts.append(parsed["source"])
    if parsed.get("video_codec"):
        parts.append(parsed["video_codec"])
    if parsed.get("audio_codec"):
        parts.append(parsed["audio_codec"])
    size = file_data.get("size", 0)
    if size > 1024 ** 3:
        parts.append(f"{size / 1024 ** 3:.1f} GB")
    elif size > 1024 ** 2:
        parts.append(f"{size / 1024 ** 2:.0f} MB")
    pos = file_data.get("positive_votes", 0)
    neg = file_data.get("negative_votes", 0)
    if pos + neg > 0:
        parts.append(f"+{pos}/-{neg}")
    tech = " | ".join(parts) if parts else file_data.get("name", "")
    return f"{flags}  {tech}" if flags else tech
