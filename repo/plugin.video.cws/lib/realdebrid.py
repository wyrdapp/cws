from __future__ import annotations

"""Real-Debrid API client.

Authentication: paste your API key from https://real-debrid.com/apitoken
"""

import logging
import time

import requests

log = logging.getLogger(__name__)

_BASE    = "https://api.real-debrid.com/rest/1.0"
_TIMEOUT = 15
_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".ts", ".m4v", ".wmv", ".flv", ".webm"}


class RealDebridClient:
    def __init__(self, api_key: str):
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {api_key}"

    def check_user(self) -> dict:
        """Verify token is valid. Raises on error."""
        r = self._session.get(f"{_BASE}/user", timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def is_cached(self, torrent_hash: str) -> bool:
        """
        Quick cache check: add magnet, select files, check status.
        Returns True if torrent is instantly available (status=downloaded quickly).
        Cleans up the torrent afterwards regardless.
        """
        torrent_id = None
        try:
            magnet = f"magnet:?xt=urn:btih:{torrent_hash}"
            torrent_id = self.add_magnet(magnet)
            self.select_files(torrent_id, "all")
            for _ in range(4):  # max 4 seconds
                info = self.get_torrent_info(torrent_id)
                status = info.get("status", "")
                if status == "downloaded":
                    return True
                if status in ("error", "dead", "magnet_error", "virus",
                              "compressing", "uploading"):
                    return False
                time.sleep(1)
            return False
        except Exception as e:
            log.warning("RD is_cached error for %s: %s", torrent_hash, e)
            return False
        finally:
            if torrent_id:
                try:
                    self._session.delete(
                        f"{_BASE}/torrents/delete/{torrent_id}", timeout=5
                    )
                except Exception:
                    pass

    def add_magnet(self, magnet: str) -> str:
        """Add a magnet link, returns RD torrent ID."""
        r = self._session.post(
            f"{_BASE}/torrents/addMagnet",
            data={"magnet": magnet},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["id"]

    def select_files(self, torrent_id: str, file_ids: str = "all"):
        """Select which files to download (use 'all' or comma-separated IDs)."""
        self._session.post(
            f"{_BASE}/torrents/selectFiles/{torrent_id}",
            data={"files": file_ids},
            timeout=10,
        )

    def get_torrent_info(self, torrent_id: str) -> dict:
        r = self._session.get(f"{_BASE}/torrents/info/{torrent_id}", timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def unrestrict_link(self, link: str) -> str:
        """Unrestrict a hoster link, returns direct download URL."""
        r = self._session.post(
            f"{_BASE}/unrestrict/link",
            data={"link": link},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["download"]

    def get_stream_url(self, torrent_hash: str, file_idx: int = 0) -> str:
        """
        Full flow: add magnet → select files → wait for ready → unrestrict.
        file_idx: 0-based index from Torrentio (converted to 1-based for RD).
        Cached torrents are usually ready in < 3 seconds.
        """
        magnet = f"magnet:?xt=urn:btih:{torrent_hash}"
        torrent_id = self.add_magnet(magnet)

        # Select only the needed file if we know its index, else all
        if file_idx > 0:
            self.select_files(torrent_id, str(file_idx))
        else:
            self.select_files(torrent_id, "all")

        # Wait for torrent to reach "downloaded" status (cached = fast)
        for attempt in range(15):
            info = self.get_torrent_info(torrent_id)
            status = info.get("status", "")
            if status == "downloaded":
                break
            if status in ("error", "dead", "magnet_error"):
                raise RuntimeError(f"RD torrent error: {status}")
            time.sleep(1)
        else:
            raise RuntimeError("Torrent not ready after 15 s")

        links = info.get("links", [])
        if not links:
            raise RuntimeError("RD returned no links")

        # Pick the video file: prefer the specific index, else the largest video link
        if file_idx > 0 and file_idx <= len(links):
            chosen_link = links[file_idx - 1]
        else:
            chosen_link = _pick_video_link(links)

        return self.unrestrict_link(chosen_link)


def _pick_video_link(links: list[str]) -> str:
    """Return the most likely video link from a list (by extension or first)."""
    for link in links:
        ext = "." + link.split("?")[0].rsplit(".", 1)[-1].lower()
        if ext in _VIDEO_EXTS:
            return link
    return links[0]
