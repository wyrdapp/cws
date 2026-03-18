from __future__ import annotations

"""Torrentio stream scraper.

Fetches torrent info hashes for movies/series by IMDB ID.
Public API, no auth required.
"""

import logging
import re

import requests

log = logging.getLogger(__name__)

_BASE    = "https://torrentio.strem.fun"
_TIMEOUT = 15

# Quality keywords to extract from Torrentio stream titles
_QUALITY_RE = re.compile(
    r"\b(2160p|4K|UHD|1080p|720p|480p|HDR|DV|BluRay|BDRip|WEBRip|WEB-DL|HDTV|DVDRip)\b",
    re.IGNORECASE,
)
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(GB|MB)", re.IGNORECASE)


def get_streams(
    imdb_id: str,
    media_type: str,
    season: int | None = None,
    episode: int | None = None,
) -> list[dict]:
    """
    Fetch torrent streams from Torrentio for a given IMDB ID.
    Returns list of dicts: {hash, file_idx, quality, size_gb, title, seeders}
    Only returns items with a valid infoHash.
    """
    if media_type == "series" and season and episode:
        path = f"/stream/series/{imdb_id}:{season}:{episode}.json"
    else:
        path = f"/stream/movie/{imdb_id}.json"

    try:
        r = requests.get(f"{_BASE}{path}", timeout=_TIMEOUT)
        r.raise_for_status()
        raw_streams = r.json().get("streams", [])
    except Exception as e:
        log.warning("Torrentio fetch error for %s: %s", imdb_id, e)
        return []

    results = []
    for s in raw_streams:
        info_hash = s.get("infoHash", "")
        if not info_hash:
            continue

        title    = s.get("title", "")
        name     = s.get("name", "")
        file_idx = s.get("fileIdx") or 0

        # Parse quality from name + title
        combined = f"{name} {title}"
        quality_matches = _QUALITY_RE.findall(combined)
        quality = " / ".join(dict.fromkeys(q.upper() for q in quality_matches)) or "?"

        # Parse size
        size_gb = 0.0
        m = _SIZE_RE.search(title)
        if m:
            val  = float(m.group(1))
            unit = m.group(2).upper()
            size_gb = val if unit == "GB" else val / 1024

        # Parse seeders (Torrentio puts 👤 N in title)
        seeders = 0
        m2 = re.search(r"👤\s*(\d+)", title)
        if m2:
            seeders = int(m2.group(1))

        results.append({
            "hash":     info_hash.lower(),
            "file_idx": file_idx,
            "quality":  quality,
            "size_gb":  size_gb,
            "title":    title,
            "name":     name,
            "seeders":  seeders,
        })

    log.debug("Torrentio returned %d streams for %s", len(results), imdb_id)
    return results
