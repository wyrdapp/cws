from __future__ import annotations

"""Hellspy.to API client - no authentication required."""

import logging

import requests

log = logging.getLogger(__name__)

_BASE = "https://api.hellspy.to/gw"
_TIMEOUT = 10


class HellspyClient:
    def search(self, query: str, limit: int = 30) -> list[dict]:
        """Search Hellspy for video files. Returns list of file dicts."""
        try:
            r = requests.get(
                f"{_BASE}/search",
                params={"query": query, "offset": 0, "limit": limit},
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            items = r.json().get("items", [])
            return [i for i in items if i.get("objectType") == "GWSearchVideo"]
        except Exception as e:
            log.warning("Hellspy search error for '%s': %s", query, e)
            return []

    def stream_urls(self, file_id: int | str, file_hash: str) -> list[dict]:
        """
        Get available stream URLs for a file.
        Returns list of dicts: {quality: '1080p', url: '...'}
        sorted best quality first.
        """
        try:
            r = requests.get(
                f"{_BASE}/video/{file_id}/{file_hash}",
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
            conversions = data.get("conversions", {})
            streams = []
            for quality, stream_url in conversions.items():
                try:
                    q_int = int(quality)
                except ValueError:
                    q_int = 0
                streams.append({"quality": f"{quality}p", "q_int": q_int, "url": stream_url})
            # Sort best quality first
            streams.sort(key=lambda x: x["q_int"], reverse=True)
            # Fallback to direct download link
            if not streams and data.get("download"):
                streams.append({"quality": "original", "q_int": 0, "url": data["download"]})
            return streams
        except Exception as e:
            log.warning("Hellspy stream_urls error for %s/%s: %s", file_id, file_hash, e)
            return []
