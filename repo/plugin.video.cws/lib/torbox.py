from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

_BASE    = "https://api.torbox.app/v1/api"
_TIMEOUT = 15
_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".ts", ".m4v", ".wmv", ".flv", ".webm"}


class TorBoxClient:
    """
    TorBox debrid API client.
    Docs: https://api-docs.torbox.app/
    """

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Webkino/1.0",
        })

    # ------------------------------------------------------------------
    # Cache check (works! unlike RD's disabled endpoint)
    # ------------------------------------------------------------------

    def check_cached(self, hashes: list[str]) -> set[str]:
        """
        Check which hashes are instantly cached in TorBox.
        Returns set of cached hash strings (lowercase).
        """
        if not hashes:
            return set()
        # API accepts up to 100 at a time as comma-separated
        chunk = hashes[:100]
        try:
            r = self._session.get(
                f"{_BASE}/torrents/checkcached",
                params={"hash": ",".join(h.lower() for h in chunk), "format": "list"},
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
            # format=list returns {"data": ["hash1", "hash2", ...]} or {"data": null}
            cached = data.get("data") or []
            if isinstance(cached, list):
                return {h.lower() for h in cached}
            return set()
        except Exception as e:
            log.warning("TorBox checkcached error: %s", e)
            return set()

    # ------------------------------------------------------------------
    # Add torrent
    # ------------------------------------------------------------------

    def create_torrent(self, torrent_hash: str) -> int:
        """
        Add torrent by hash (as magnet). Returns torrent_id.
        """
        magnet = f"magnet:?xt=urn:btih:{torrent_hash}"
        r = self._session.post(
            f"{_BASE}/torrents/createtorrent",
            data={"magnet": magnet},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        torrent_id = data.get("data", {}).get("torrent_id")
        if not torrent_id:
            raise ValueError(f"TorBox: no torrent_id in response: {data}")
        return int(torrent_id)

    # ------------------------------------------------------------------
    # Get torrent info from user's list
    # ------------------------------------------------------------------

    def get_torrent(self, torrent_id: int) -> dict:
        """Fetch torrent info by ID from user's list."""
        r = self._session.get(
            f"{_BASE}/torrents/mylist",
            params={"id": str(torrent_id), "bypass_cache": "true"},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("data", {})

    # ------------------------------------------------------------------
    # Request download / stream link
    # ------------------------------------------------------------------

    def request_link(self, torrent_id: int, file_id: int) -> str:
        """
        Get a CDN download/stream link for a specific file.
        Returns direct URL.
        """
        r = self._session.get(
            f"{_BASE}/torrents/requestdl",
            params={
                "token":       self._api_key,
                "torrent_id":  str(torrent_id),
                "file_id":     str(file_id),
            },
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        link = data.get("data", "")
        if not link:
            raise ValueError(f"TorBox: no link in response: {data}")
        return link

    # ------------------------------------------------------------------
    # Delete torrent (cleanup)
    # ------------------------------------------------------------------

    def delete_torrent(self, torrent_id: int):
        try:
            self._session.post(
                f"{_BASE}/torrents/controltorrent",
                json={"torrent_id": torrent_id, "operation": "delete"},
                timeout=5,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # High-level: get stream URL from hash
    # ------------------------------------------------------------------

    def get_stream_url(self, torrent_hash: str, file_idx: int = 0) -> str:
        """
        Full flow: add magnet → wait for cached status → get stream URL.
        For cached torrents this completes in ~2-3 seconds.
        """
        torrent_id = None
        try:
            torrent_id = self.create_torrent(torrent_hash)
            log.info("TorBox: created torrent_id=%d for hash=%s", torrent_id, torrent_hash)

            # Poll until status is "cached" or "completed" (max 20s)
            for attempt in range(10):
                info = self.get_torrent(torrent_id)
                status = info.get("download_state", "")
                log.info("TorBox: attempt=%d status=%s", attempt, status)

                if status in ("cached", "completed", "uploading"):
                    files = info.get("files", [])
                    video_files = [
                        f for f in files
                        if any(f.get("name", "").lower().endswith(ext) for ext in _VIDEO_EXTS)
                    ]
                    if not video_files:
                        video_files = files
                    if not video_files:
                        raise ValueError("TorBox: no files found in torrent")

                    # Pick by index, fallback to largest
                    video_files.sort(key=lambda x: x.get("size", 0), reverse=True)
                    idx = min(file_idx, len(video_files) - 1)
                    chosen = video_files[idx]
                    file_id = chosen.get("id")
                    log.info("TorBox: picking file_id=%s name=%s", file_id, chosen.get("name"))
                    return self.request_link(torrent_id, file_id)

                if status in ("error", "dead", "stalled (no seeds)"):
                    raise ValueError(f"TorBox: torrent in bad state: {status}")

                time.sleep(2)

            raise ValueError("TorBox: timeout waiting for cached status")

        except Exception:
            if torrent_id:
                self.delete_torrent(torrent_id)
            raise
