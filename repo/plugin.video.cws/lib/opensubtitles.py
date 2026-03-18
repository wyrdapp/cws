from __future__ import annotations

import logging
import os
import tempfile

import requests

log = logging.getLogger(__name__)

_BASE    = "https://api.opensubtitles.com/api/v1"
_TIMEOUT = 15
_APP     = "Webkino v1.7"


class OpenSubtitlesClient:
    """
    OpenSubtitles REST API client (api.opensubtitles.com).

    Free tier: 5 downloads/day without login, 20/day with login.
    API key: https://www.opensubtitles.com/en/consumers
    """

    def __init__(self, api_key: str, username: str = "", password: str = ""):
        self._api_key  = api_key
        self._username = username
        self._password = password
        self._token: str = ""
        self._session  = requests.Session()
        self._session.headers.update({
            "Api-Key":      api_key,
            "Content-Type": "application/json",
            "User-Agent":   _APP,
        })

    # ------------------------------------------------------------------
    # Auth (optional — increases daily download limit)
    # ------------------------------------------------------------------

    def login(self) -> bool:
        if not self._username or not self._password:
            return False
        try:
            r = self._session.post(
                f"{_BASE}/login",
                json={"username": self._username, "password": self._password},
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            self._token = r.json().get("token", "")
            if self._token:
                self._session.headers["Authorization"] = f"Bearer {self._token}"
                log.info("OpenSubtitles: logged in as %s", self._username)
            return bool(self._token)
        except Exception as e:
            log.warning("OpenSubtitles login failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        imdb_id: str,
        languages: str = "cs,sk,en",
        media_type: str = "movie",
        season: int | None = None,
        episode: int | None = None,
    ) -> list[dict]:
        """
        Search subtitles by IMDB ID.
        Returns list of subtitle metadata dicts (up to 20).
        """
        # Strip 'tt' prefix — API accepts both but numeric is safer
        imdb_num = imdb_id.lstrip("t") if imdb_id.startswith("tt") else imdb_id

        params: dict = {
            "imdb_id":   imdb_num,
            "languages": languages,
            "order_by":  "download_count",
            "order_direction": "desc",
        }
        if media_type == "series" and season and episode:
            params["season_number"]  = season
            params["episode_number"] = episode

        try:
            r = self._session.get(
                f"{_BASE}/subtitles", params=params, timeout=_TIMEOUT
            )
            r.raise_for_status()
            data = r.json()
            items = data.get("data", [])
            log.info(
                "OpenSubtitles: found %d subtitles for imdb=%s lang=%s",
                len(items), imdb_id, languages,
            )
            return items
        except Exception as e:
            log.warning("OpenSubtitles search error: %s", e)
            return []

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def get_download_url(self, file_id: int) -> tuple[str, str]:
        """
        Request download URL for a subtitle file.
        Returns (url, filename). Raises on error.
        """
        r = self._session.post(
            f"{_BASE}/download",
            json={"file_id": file_id},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data     = r.json()
        dl_url   = data.get("link", "")
        filename = data.get("file_name", f"subtitle_{file_id}.srt")
        return dl_url, filename

    def download_subtitle(self, file_id: int, dest_dir: str) -> str | None:
        """
        Download subtitle to dest_dir.
        Returns local file path or None on failure.
        """
        try:
            dl_url, filename = self.get_download_url(file_id)
            if not dl_url:
                return None
            # Sanitize filename
            safe_name = "".join(c for c in filename if c not in r'\/:*?"<>|')
            dest = os.path.join(dest_dir, safe_name)
            resp = requests.get(dl_url, timeout=_TIMEOUT)
            resp.raise_for_status()
            with open(dest, "wb") as f:
                f.write(resp.content)
            log.info("OpenSubtitles: downloaded %s → %s", filename, dest)
            return dest
        except Exception as e:
            log.warning("OpenSubtitles download error: %s", e)
            return None

    # ------------------------------------------------------------------
    # Convenience: find + download best subtitle
    # ------------------------------------------------------------------

    def fetch_subtitles(
        self,
        imdb_id: str,
        languages: str = "cs,sk,en",
        media_type: str = "movie",
        season: int | None = None,
        episode: int | None = None,
        max_subs: int = 3,
    ) -> list[str]:
        """
        Search and download up to max_subs subtitles.
        Returns list of local file paths.
        """
        items = self.search(imdb_id, languages, media_type, season, episode)
        if not items:
            return []

        # Prefer hearing-impaired=False, then by download count (already sorted)
        items.sort(key=lambda x: (
            x.get("attributes", {}).get("hearing_impaired", False),
        ))

        dest_dir = tempfile.gettempdir()
        paths: list[str] = []
        for item in items[:max_subs]:
            attrs = item.get("attributes", {})
            files = attrs.get("files", [])
            if not files:
                continue
            file_id = files[0].get("file_id")
            if not file_id:
                continue
            lang = attrs.get("language", "?")
            log.info("OpenSubtitles: downloading file_id=%s lang=%s", file_id, lang)
            path = self.download_subtitle(file_id, dest_dir)
            if path:
                paths.append(path)

        return paths
