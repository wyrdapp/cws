from __future__ import annotations

"""
Watch history & series progress tracker.

Stores data in a JSON file inside Kodi's addon profile directory.
Uses xbmcvfs for all file operations to support Android and other
platforms where special:// paths are not accessible via os module.
"""

import json
import time
from typing import Any

import xbmcvfs

MAX_HISTORY = 100


class WatchHistory:
    def __init__(self, profile_dir: str):
        # Translate special:// path to a real filesystem path
        self._dir = xbmcvfs.translatePath(profile_dir)
        xbmcvfs.mkdirs(self._dir)
        self._path = self._dir.rstrip("/\\") + "/history.json"
        self._data = self._load()

    def _load(self) -> dict:
        if xbmcvfs.exists(self._path):
            try:
                f = xbmcvfs.File(self._path)
                content = f.read()
                f.close()
                return json.loads(content)
            except (ValueError, IOError):
                pass
        return {"items": [], "series": {}}

    def _save(self):
        f = xbmcvfs.File(self._path, "w")
        f.write(json.dumps(self._data, ensure_ascii=False, indent=1))
        f.close()

    # -- watch history ------------------------------------------------------

    def add(self, item: dict[str, Any]):
        """
        Add or update a history entry.

        item should contain:
          type, title, original_title, year, tmdb_id, imdb_id,
          poster, fanart, ident (webshare file ident),
          stream_label, season (opt), episode (opt)
        """
        entry = dict(item)
        entry["timestamp"] = time.time()

        items = self._data.setdefault("items", [])
        if entry.get("type") == "series" and entry.get("season") and entry.get("episode"):
            key = f"{entry.get('tmdb_id')}_{entry.get('season')}_{entry.get('episode')}"
        else:
            key = str(entry.get("tmdb_id", ""))
        items = [i for i in items if self._entry_key(i) != key]

        items.insert(0, entry)
        self._data["items"] = items[:MAX_HISTORY]

        if entry.get("type") == "series" and entry.get("season") and entry.get("episode"):
            self._update_series(entry)

        self._save()

    @staticmethod
    def _entry_key(entry: dict) -> str:
        if entry.get("type") == "series" and entry.get("season") and entry.get("episode"):
            return f"{entry.get('tmdb_id')}_{entry.get('season')}_{entry.get('episode')}"
        return str(entry.get("tmdb_id", ""))

    def get_history(self, limit: int = 30) -> list[dict]:
        return self._data.get("items", [])[:limit]

    def clear(self):
        self._data = {"items": [], "series": {}}
        self._save()

    # -- series progress ----------------------------------------------------

    def _update_series(self, entry: dict):
        series = self._data.setdefault("series", {})
        tmdb_id = str(entry.get("tmdb_id", ""))
        if not tmdb_id:
            return
        series[tmdb_id] = {
            "title": entry.get("title", ""),
            "original_title": entry.get("original_title", ""),
            "poster": entry.get("poster", ""),
            "fanart": entry.get("fanart", ""),
            "imdb_id": entry.get("imdb_id", ""),
            "tmdb_id": tmdb_id,
            "last_season": entry.get("season"),
            "last_episode": entry.get("episode"),
            "timestamp": time.time(),
        }
        self._save()

    def get_series_progress(self) -> list[dict]:
        """Return all tracked series sorted by last watched time."""
        series = self._data.get("series", {})
        items = list(series.values())
        items.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return items

    def get_next_episode(self, tmdb_id: str) -> tuple[int, int] | None:
        """Return (season, episode) for the next unwatched episode, or None."""
        series = self._data.get("series", {})
        info = series.get(str(tmdb_id))
        if not info:
            return None
        s = info.get("last_season", 1)
        e = info.get("last_episode", 0)
        return (s, e + 1)

    # -- persistent preferences --------------------------------------------
    # Settings stored here survive addon reinstalls (unlike Kodi's own settings)

    def save_prefs(self, prefs: dict):
        """Save user preferences to persistent storage."""
        self._data["prefs"] = prefs
        self._save()

    def load_prefs(self) -> dict:
        """Load previously saved preferences. Returns empty dict if none."""
        return dict(self._data.get("prefs", {}))
