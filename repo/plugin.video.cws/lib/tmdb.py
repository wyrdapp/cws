"""Synchronous TMDB API client."""

import logging
from typing import Any

import requests

log = logging.getLogger(__name__)

TMDB_IMAGE = "https://image.tmdb.org/t/p"
POSTER_SIZE = "w500"
BACKDROP_SIZE = "w1280"


def poster_url(path: str | None) -> str:
    return f"{TMDB_IMAGE}/{POSTER_SIZE}{path}" if path else ""


def backdrop_url(path: str | None) -> str:
    return f"{TMDB_IMAGE}/{BACKDROP_SIZE}{path}" if path else ""


class TMDBClient:
    BASE = "https://api.themoviedb.org/3"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._session = requests.Session()
        self._cache: dict[str, Any] = {}

    def _get(self, path: str, params: dict | None = None) -> dict:
        p = {"api_key": self.api_key}
        if params:
            p.update(params)
        r = self._session.get(f"{self.BASE}{path}", params=p, timeout=15)
        r.raise_for_status()
        return r.json()

    # -- find by IMDB ID ----------------------------------------------------

    def find_by_imdb(self, imdb_id: str) -> dict | None:
        if imdb_id in self._cache:
            return self._cache[imdb_id]
        data = self._get(f"/find/{imdb_id}", {"external_source": "imdb_id"})
        result = None
        if data.get("movie_results"):
            result = self._movie_info(data["movie_results"][0])
        elif data.get("tv_results"):
            result = self._tv_info(data["tv_results"][0])
        if result:
            self._cache[imdb_id] = result
        return result

    # -- search -------------------------------------------------------------

    def search_movies(self, query: str, page: int = 1) -> dict:
        return self._get("/search/movie", {"query": query, "page": page, "language": "cs-CZ"})

    def search_tv(self, query: str, page: int = 1) -> dict:
        return self._get("/search/tv", {"query": query, "page": page, "language": "cs-CZ"})

    # -- browse (for library) -----------------------------------------------

    def popular_movies(self, page: int = 1) -> dict:
        return self._get("/movie/popular", {"page": page, "language": "cs-CZ"})

    def top_rated_movies(self, page: int = 1) -> dict:
        return self._get("/movie/top_rated", {"page": page, "language": "cs-CZ"})

    def popular_tv(self, page: int = 1) -> dict:
        return self._get("/tv/popular", {"page": page, "language": "cs-CZ"})

    def top_rated_tv(self, page: int = 1) -> dict:
        return self._get("/tv/top_rated", {"page": page, "language": "cs-CZ"})

    def movie_genres(self) -> list[dict]:
        return self._get("/genre/movie/list", {"language": "cs-CZ"}).get("genres", [])

    def movies_by_genre(self, genre_id: int, page: int = 1) -> dict:
        return self._get("/discover/movie", {
            "with_genres": genre_id,
            "sort_by": "popularity.desc",
            "page": page,
            "language": "cs-CZ",
        })

    # -- detail -------------------------------------------------------------

    def movie_detail(self, tmdb_id: int) -> dict:
        return self._get(f"/movie/{tmdb_id}", {
            "language": "cs-CZ",
            "append_to_response": "external_ids,credits",
        })

    def tv_detail(self, tmdb_id: int) -> dict:
        return self._get(f"/tv/{tmdb_id}", {
            "language": "cs-CZ",
            "append_to_response": "external_ids,credits",
        })

    def tv_season(self, tmdb_id: int, season: int) -> dict:
        return self._get(f"/tv/{tmdb_id}/season/{season}", {"language": "cs-CZ"})

    # -- helpers ------------------------------------------------------------

    def _movie_info(self, m: dict) -> dict:
        imdb_id = m.get("imdb_id") or self._movie_imdb(m["id"])
        return {
            "type": "movie",
            "tmdb_id": m["id"],
            "imdb_id": imdb_id,
            "title": m.get("title", ""),
            "original_title": m.get("original_title", m.get("title", "")),
            "year": (m.get("release_date") or "")[:4],
            "plot": m.get("overview", ""),
            "rating": m.get("vote_average", 0),
            "votes": m.get("vote_count", 0),
            "poster": poster_url(m.get("poster_path")),
            "fanart": backdrop_url(m.get("backdrop_path")),
            "genres": [g["name"] for g in m.get("genres", [])],
        }

    def _tv_info(self, s: dict) -> dict:
        imdb_id = self._tv_imdb(s["id"])
        return {
            "type": "series",
            "tmdb_id": s["id"],
            "imdb_id": imdb_id,
            "title": s.get("name", ""),
            "original_title": s.get("original_name", s.get("name", "")),
            "year": (s.get("first_air_date") or "")[:4],
            "plot": s.get("overview", ""),
            "rating": s.get("vote_average", 0),
            "votes": s.get("vote_count", 0),
            "poster": poster_url(s.get("poster_path")),
            "fanart": backdrop_url(s.get("backdrop_path")),
            "genres": [g["name"] for g in s.get("genres", [])],
            "seasons": s.get("number_of_seasons", 1),
        }

    def _movie_imdb(self, tmdb_id: int) -> str:
        try:
            d = self._get(f"/movie/{tmdb_id}", {"append_to_response": "external_ids"})
            return d.get("imdb_id") or d.get("external_ids", {}).get("imdb_id", "")
        except Exception:
            return ""

    def _tv_imdb(self, tmdb_id: int) -> str:
        try:
            d = self._get(f"/tv/{tmdb_id}/external_ids")
            return d.get("imdb_id", "")
        except Exception:
            return ""

    # -- convert raw search result to info dict ----------------------------

    def movie_result_to_info(self, m: dict) -> dict:
        return {
            "type": "movie",
            "tmdb_id": m["id"],
            "imdb_id": "",
            "title": m.get("title", ""),
            "original_title": m.get("original_title", m.get("title", "")),
            "year": (m.get("release_date") or "")[:4],
            "plot": m.get("overview", ""),
            "rating": m.get("vote_average", 0),
            "votes": m.get("vote_count", 0),
            "poster": poster_url(m.get("poster_path")),
            "fanart": backdrop_url(m.get("backdrop_path")),
            "genres": [],
        }

    def tv_result_to_info(self, s: dict) -> dict:
        return {
            "type": "series",
            "tmdb_id": s["id"],
            "imdb_id": "",
            "title": s.get("name", ""),
            "original_title": s.get("original_name", s.get("name", "")),
            "year": (s.get("first_air_date") or "")[:4],
            "plot": s.get("overview", ""),
            "rating": s.get("vote_average", 0),
            "votes": s.get("vote_count", 0),
            "poster": poster_url(s.get("poster_path")),
            "fanart": backdrop_url(s.get("backdrop_path")),
            "genres": [],
            "seasons": s.get("number_of_seasons", 1),
        }
