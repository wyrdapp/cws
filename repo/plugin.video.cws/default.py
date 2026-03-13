from __future__ import annotations

"""
Webshare Kodi addon – hlavní vstupní bod.

Routing přes URL parametry:
  (žádné)                    → hlavní menu
  action=search_movies       → vyhledat filmy
  action=search_series       → vyhledat seriály
  action=results_movies      → výsledky hledání filmů
  action=results_series      → výsledky hledání seriálů
  action=browse              → procházet (oblíbené / top / žánry)
  action=browse_list         → seznam filmů/seriálů z procházení
  action=seasons             → seznam sezón seriálu
  action=episodes            → seznam epizod sezóny
  action=select_stream       → výběr streamu (dialog nebo auto-best)
  action=play                → přehrát konkrétní Webshare soubor
"""

import os
import sys
from urllib.parse import parse_qsl, urlencode, quote_plus, unquote_plus

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

sys.path.insert(0, xbmcaddon.Addon().getAddonInfo("path") + "/lib")

try:
    from history import WatchHistory
    from webshare import WebshareClient
    from tmdb import TMDBClient
    from resolver import (
        calculate_score,
        matches_episode,
        matches_title,
        parse_description,
        parse_filename,
        stream_label,
    )
except Exception:
    import traceback
    _tb = traceback.format_exc()
    xbmc.log(f"[plugin.video.cws] IMPORT ERROR: {_tb}", xbmc.LOGERROR)
    xbmcgui.Dialog().textviewer("Stream Cinema - Import Error", _tb)
    raise

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

ADDON    = xbmcaddon.Addon()
BASE_URL = sys.argv[0]
HANDLE   = int(sys.argv[1])
ADDON_ID = ADDON.getAddonInfo("id")


def log(msg: str, level: int = xbmc.LOGINFO):
    xbmc.log(f"[{ADDON_ID}] {msg}", level=level)


def setting(key: str) -> str:
    return ADDON.getSetting(key).strip()


def url(**kwargs) -> str:
    return f"{BASE_URL}?{urlencode(kwargs)}"


def encode(s: str) -> str:
    return quote_plus(s)


def decode(s: str) -> str:
    return unquote_plus(s)


# ---------------------------------------------------------------------------
# Webshare + TMDB clients (lazy init)
# ---------------------------------------------------------------------------

_ws: WebshareClient | None = None
_tmdb: TMDBClient | None = None


watch_history = WatchHistory(xbmcaddon.Addon().getAddonInfo("profile"))


def _do_settings():
    """Open addon settings dialog (non-directory action)."""
    xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
    xbmcplugin.endOfDirectory(HANDLE, succeeded=False)


def get_ws() -> WebshareClient | None:
    global _ws
    u, p = setting("webshare_username"), setting("webshare_password")
    if not u or not p:
        xbmcgui.Dialog().ok(
            "Webshare", "Vyplň přihlašovací údaje v nastavení addonu."
        )
        xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
        return None
    if _ws is None or _ws.username != u:
        _ws = WebshareClient(u, p)
        try:
            _ws.login()
        except Exception as e:
            log(f"Login failed: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().ok("Webshare", f"Přihlášení selhalo:\n{e}")
            return None
    return _ws


def get_tmdb() -> TMDBClient | None:
    global _tmdb
    key = setting("tmdb_api_key")
    if not key:
        xbmcgui.Dialog().ok(
            "Webshare", "Vyplň TMDB API klíč v nastavení addonu."
        )
        xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
        return None
    if _tmdb is None:
        _tmdb = TMDBClient(key)
    return _tmdb


# ---------------------------------------------------------------------------
# Helper: build ListItem for a movie/show
# ---------------------------------------------------------------------------

def make_media_item(info: dict, action: str) -> tuple:
    title  = info.get("title", "")
    year   = info.get("year", "")
    label  = f"{title} ({year})" if year else title
    imdb   = info.get("imdb_id", "")
    tmdb   = str(info.get("tmdb_id", ""))
    typ    = info.get("type", "movie")

    li = xbmcgui.ListItem(label)
    li.setArt({"poster": info.get("poster", ""), "fanart": info.get("fanart", ""),
                "thumb": info.get("poster", "")})

    infolabels: dict = {
        "title":      title,
        "year":       int(year) if year else 0,
        "plot":       info.get("plot", ""),
        "rating":     info.get("rating", 0),
        "votes":      str(info.get("votes", 0)),
        "genre":      ", ".join(info.get("genres", [])),
        "imdbnumber": imdb,
        "mediatype":  "movie" if typ == "movie" else "tvshow",
    }
    li.setInfo("video", infolabels)
    li.setProperty("IsPlayable", "false")

    if typ == "movie":
        target = url(action=action, type="movie",
                     imdb_id=encode(imdb), tmdb_id=tmdb,
                     title=encode(title), year=encode(year),
                     original_title=encode(info.get("original_title", title)))
    else:
        target = url(action="seasons", type="series",
                     imdb_id=encode(imdb), tmdb_id=tmdb,
                     title=encode(title), year=encode(year),
                     original_title=encode(info.get("original_title", title)))
    return target, li, True


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

def main_menu():
    items = [
        ("[B]Hledat filmy[/B]",                url(action="search_movies")),
        ("[B]Hledat seriály[/B]",              url(action="search_series")),
        ("Historie přehrávání",                 url(action="history")),
        ("Pokračovat v seriálech",              url(action="continue_series")),
        ("Populární filmy",                     url(action="browse_list", type="movie", category="popular", page="1")),
        ("Nejlépe hodnocené filmy",             url(action="browse_list", type="movie", category="top_rated", page="1")),
        ("Populární seriály",                   url(action="browse_list", type="series", category="popular", page="1")),
        ("Nejlépe hodnocené seriály",           url(action="browse_list", type="series", category="top_rated", page="1")),
        ("Filmy podle žánru",                   url(action="browse")),
    ]
    if setting("download_enabled") == "true":
        items.append(("Stažené soubory", url(action="downloads")))
    for label, target in items:
        li = xbmcgui.ListItem(label)
        li.setProperty("IsPlayable", "false")
        xbmcplugin.addDirectoryItem(HANDLE, target, li, isFolder=True)

    # Settings is special – context menu + own item with executebuiltin
    li = xbmcgui.ListItem("[I]Nastavení[/I]")
    li.setProperty("IsPlayable", "false")
    xbmcplugin.addDirectoryItem(
        HANDLE,
        url(action="settings"),
        li,
        isFolder=False,
    )
    xbmcplugin.endOfDirectory(HANDLE)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _suggest_query(prompt: str) -> str:
    """Show keyboard input, then offer Webshare suggestions if available."""
    query = xbmcgui.Dialog().input(prompt, type=xbmcgui.INPUT_ALPHANUM)
    log(f"_suggest_query: keyboard returned {query!r}")
    if not query:
        return ""
    ws = get_ws()
    if ws:
        try:
            suggestions = ws.suggest(query, limit=8)
            log(f"_suggest_query: suggestions={suggestions}")
            if suggestions:
                choices = [query] + [s for s in suggestions if s != query]
                idx = xbmcgui.Dialog().select(
                    "Vyberte hledaný výraz", choices
                )
                log(f"_suggest_query: user selected idx={idx}")
                if idx >= 0:
                    return choices[idx]
        except Exception as e:
            log(f"suggest error: {e}", xbmc.LOGWARNING)
    return query


def search_movies(params: dict):
    query = decode(params.get("query", ""))
    if not query:
        query = _suggest_query("Hledat filmy")
    log(f"search_movies: query={query!r}")
    if not query:
        log("search_movies: empty query, aborting")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    tmdb_c = get_tmdb()
    if not tmdb_c:
        log("search_movies: no TMDB client")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    page = int(params.get("page", 1))
    try:
        data = tmdb_c.search_movies(query, page=page)
        log(f"search_movies: TMDB returned {len(data.get('results', []))} results")
    except Exception as e:
        log(f"search_movies: TMDB error: {e}", xbmc.LOGERROR)
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    params["query"] = encode(query)
    _render_movie_list(tmdb_c, data, params, "select_stream")


def search_series(params: dict):
    query = decode(params.get("query", ""))
    if not query:
        query = _suggest_query("Hledat seriály")
    log(f"search_series: query={query!r}")
    if not query:
        log("search_series: empty query, aborting")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    tmdb_c = get_tmdb()
    if not tmdb_c:
        log("search_series: no TMDB client")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    page = int(params.get("page", 1))
    try:
        data = tmdb_c.search_tv(query, page=page)
        log(f"search_series: TMDB returned {len(data.get('results', []))} results")
    except Exception as e:
        log(f"search_series: TMDB error: {e}", xbmc.LOGERROR)
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    params["query"] = encode(query)
    _render_series_list(tmdb_c, data, params)


# ---------------------------------------------------------------------------
# Browse (library-friendly lists with full metadata)
# ---------------------------------------------------------------------------

def browse(params: dict):
    """Žánrový výběr."""
    tmdb = get_tmdb()
    if not tmdb:
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    genres = tmdb.movie_genres()
    for g in genres:
        li = xbmcgui.ListItem(g["name"])
        li.setProperty("IsPlayable", "false")
        target = url(action="browse_list", type="movie", category="genre",
                     genre_id=str(g["id"]), genre_name=encode(g["name"]), page="1")
        xbmcplugin.addDirectoryItem(HANDLE, target, li, isFolder=True)
    xbmcplugin.endOfDirectory(HANDLE)


def browse_list(params: dict):
    tmdb = get_tmdb()
    if not tmdb:
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    typ      = params.get("type", "movie")
    category = params.get("category", "popular")
    page     = int(params.get("page", 1))

    if typ == "movie":
        if category == "popular":
            data = tmdb.popular_movies(page=page)
        elif category == "top_rated":
            data = tmdb.top_rated_movies(page=page)
        elif category == "genre":
            data = tmdb.movies_by_genre(int(params.get("genre_id", 0)), page=page)
        else:
            data = tmdb.popular_movies(page=page)
        _render_movie_list(tmdb, data, params, "select_stream")
    else:
        if category == "popular":
            data = tmdb.popular_tv(page=page)
        elif category == "top_rated":
            data = tmdb.top_rated_tv(page=page)
        else:
            data = tmdb.popular_tv(page=page)
        _render_series_list(tmdb, data, params)


def _render_movie_list(tmdb: TMDBClient, data: dict, params: dict, stream_action: str):
    xbmcplugin.setContent(HANDLE, "movies")
    for m in data.get("results", []):
        info = tmdb.movie_result_to_info(m)
        target, li, is_folder = make_media_item(info, stream_action)
        xbmcplugin.addDirectoryItem(HANDLE, target, li, isFolder=is_folder)
    _add_next_page(data, params)
    xbmcplugin.endOfDirectory(HANDLE)


def _render_series_list(tmdb: TMDBClient, data: dict, params: dict):
    xbmcplugin.setContent(HANDLE, "videos")
    count = 0
    for s in data.get("results", []):
        try:
            info = tmdb.tv_result_to_info(s)
            target, li, is_folder = make_media_item(info, "select_stream")
            ok = xbmcplugin.addDirectoryItem(HANDLE, target, li, isFolder=is_folder)
            if ok:
                count += 1
            else:
                log(f"_render_series_list: addDirectoryItem returned False for {info.get('title')}")
        except Exception as e:
            log(f"_render_series_list item error: {e}", xbmc.LOGERROR)
    _add_next_page(data, params)
    log(f"_render_series_list: added {count} items, calling endOfDirectory")
    xbmcplugin.endOfDirectory(HANDLE, succeeded=True, updateListing=False, cacheToDisc=False)
    log("_render_series_list: endOfDirectory done")


def _add_next_page(data: dict, params: dict):
    page       = int(params.get("page", 1))
    total_pages = data.get("total_pages", 1)
    if page < total_pages:
        next_params = dict(params)
        next_params["page"] = str(page + 1)
        li = xbmcgui.ListItem(f"Další strana ({page + 1} / {total_pages})")
        li.setProperty("IsPlayable", "false")
        xbmcplugin.addDirectoryItem(HANDLE, url(**next_params), li, isFolder=True)


# ---------------------------------------------------------------------------
# Seasons & Episodes
# ---------------------------------------------------------------------------

def seasons(params: dict):
    tmdb_obj = get_tmdb()
    if not tmdb_obj:
        log("seasons: no TMDB client")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    tmdb_id = int(params.get("tmdb_id", 0))
    log(f"seasons: fetching detail for tmdb_id={tmdb_id}")
    try:
        detail = tmdb_obj.tv_detail(tmdb_id)
    except Exception as e:
        log(f"seasons: tv_detail error: {e}", xbmc.LOGERROR)
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    imdb_id = params.get("imdb_id", "")
    if not imdb_id:
        imdb_id = encode(detail.get("external_ids", {}).get("imdb_id", ""))

    all_seasons = detail.get("seasons", [])
    log(f"seasons: got {len(all_seasons)} seasons, imdb_id={imdb_id}")

    xbmcplugin.setContent(HANDLE, "videos")
    count = 0
    for s in all_seasons:
        num   = s.get("season_number", 0)
        if num == 0:
            continue
        air_year = (s.get("air_date") or "")[:4]
        label = f"Sezóna {num} ({air_year})" if air_year else f"Sezóna {num}"
        li    = xbmcgui.ListItem(label)
        li.setArt({"poster": s.get("poster_path") and
                   f"https://image.tmdb.org/t/p/w500{s['poster_path']}" or ""})
        li.setInfo("video", {
            "title": label,
            "season": num,
            "plot": s.get("overview", ""),
            "mediatype": "season",
        })
        li.setProperty("IsPlayable", "false")
        target = url(
            action="episodes",
            imdb_id=imdb_id,
            tmdb_id=str(tmdb_id),
            season=str(num),
            title=params.get("title", ""),
            original_title=params.get("original_title", ""),
            year=params.get("year", ""),
        )
        xbmcplugin.addDirectoryItem(HANDLE, target, li, isFolder=True)
        count += 1
    log(f"seasons: added {count} seasons")
    xbmcplugin.endOfDirectory(HANDLE, succeeded=True, updateListing=False, cacheToDisc=False)


def episodes(params: dict):
    tmdb_obj = get_tmdb()
    if not tmdb_obj:
        log("episodes: no TMDB client")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    tmdb_id = int(params.get("tmdb_id", 0))
    season  = int(params.get("season", 1))
    log(f"episodes: fetching tmdb_id={tmdb_id} season={season}")
    try:
        data = tmdb_obj.tv_season(tmdb_id, season)
    except Exception as e:
        log(f"episodes: tv_season error: {e}", xbmc.LOGERROR)
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    eps = data.get("episodes", [])
    log(f"episodes: got {len(eps)} episodes")

    xbmcplugin.setContent(HANDLE, "videos")
    count = 0
    for ep in eps:
        ep_num = ep.get("episode_number", 0)
        label  = f"S{season:02d}E{ep_num:02d} – {ep.get('name', '')}"
        li     = xbmcgui.ListItem(label)
        li.setArt({"thumb": ep.get("still_path") and
                   f"https://image.tmdb.org/t/p/w500{ep['still_path']}" or ""})
        li.setInfo("video", {
            "title": ep.get("name", ""),
            "season": season,
            "episode": ep_num,
            "plot": ep.get("overview", ""),
            "rating": ep.get("vote_average", 0),
            "mediatype": "video",
        })
        li.setProperty("IsPlayable", "false")
        target = url(
            action="select_stream",
            type="series",
            imdb_id=params.get("imdb_id", ""),
            tmdb_id=params.get("tmdb_id", ""),
            season=str(season),
            episode=str(ep_num),
            title=params.get("title", ""),
            original_title=params.get("original_title", ""),
            year=params.get("year", ""),
        )
        xbmcplugin.addDirectoryItem(HANDLE, target, li, isFolder=True)
        count += 1
    log(f"episodes: added {count} episodes")
    xbmcplugin.endOfDirectory(HANDLE, succeeded=True, updateListing=False, cacheToDisc=False)


# ---------------------------------------------------------------------------
# Stream selection
# ---------------------------------------------------------------------------

def _find_streams(params: dict) -> list[tuple[int, dict, dict]]:
    """Search Webshare and return scored list of (score, file_data, parsed)."""
    ws = get_ws()
    if not ws:
        return []

    content_type   = params.get("type", "movie")
    title          = decode(params.get("title", ""))
    original_title = decode(params.get("original_title", title))
    year           = decode(params.get("year", ""))
    imdb_id        = decode(params.get("imdb_id", ""))
    season         = int(params.get("season", 0)) or None
    episode        = int(params.get("episode", 0)) or None

    queries: list[str] = []
    if content_type == "movie":
        queries.append(f"{original_title} {year}".strip())
        queries.append(original_title)
        if title != original_title:
            queries.append(f"{title} {year}".strip())
    else:
        ep_tag = f" S{season:02d}E{episode:02d}" if season and episode else ""
        queries.append(f"{original_title}{ep_tag}")
        if title != original_title:
            queries.append(f"{title}{ep_tag}")

    log(f"_find_streams: type={content_type} title='{title}' original='{original_title}' "
        f"year='{year}' imdb='{imdb_id}' S={season} E={episode}")
    log(f"_find_streams: queries={queries}")

    all_files: list[dict] = []
    seen: set[str] = set()
    for q in queries:
        try:
            results = ws.search(q, limit=25)
            log(f"_find_streams: search('{q}') → {len(results)} results")
            for f in results:
                if f["ident"] not in seen:
                    seen.add(f["ident"])
                    all_files.append(f)
        except Exception as e:
            log(f"Webshare search error for '{q}': {e}", xbmc.LOGERROR)

    log(f"_find_streams: {len(all_files)} unique files")

    scored: list[tuple[int, dict, dict]] = []
    for fdata in all_files:
        if fdata.get("password"):
            continue
        parsed = parse_filename(fdata["name"])
        title_ok = matches_title(parsed, title, original_title, year)
        if not title_ok:
            continue
        if content_type == "series" and season and episode:
            ep_ok = matches_episode(parsed, season, episode)
            if not ep_ok:
                log(f"_find_streams: ep mismatch: '{fdata['name']}' parsed S={parsed.get('season')} E={parsed.get('episode')}")
                continue
        scored.append((calculate_score(fdata, parsed), fdata, parsed))

    log(f"_find_streams: {len(scored)} matched after filtering")
    scored.sort(key=lambda x: x[0], reverse=True)

    # Enrich top 2 results with file_info (additive only, never removes)
    for i, (score, fdata, parsed) in enumerate(scored[:2]):
        try:
            fi = ws.file_info(fdata["ident"])
            if not fi.get("name"):
                continue
            fdata["description"] = fi.get("description", "")
            fdata["stripe"] = fi.get("stripe", "") or fdata.get("stripe", "")
            fdata["stripe_count"] = fi.get("stripe_count", 0) or fdata.get("stripe_count", 0)

            desc_meta = parse_description(fdata["description"])
            if desc_meta.get("imdb_id") and imdb_id and desc_meta["imdb_id"] == imdb_id:
                scored[i] = (score + 50, fdata, parsed)
        except Exception as e:
            log(f"file_info enrichment error: {e}", xbmc.LOGWARNING)

    # Try similar_files on the best result (optional, non-blocking)
    if scored and scored[0][0] > 0:
        try:
            sim = ws.similar_files(scored[0][1]["ident"])
            for sf in sim.get("similar", []):
                if sf["ident"] in seen or sf.get("password"):
                    continue
                seen.add(sf["ident"])
                parsed = parse_filename(sf["name"])
                scored.append((calculate_score(sf, parsed), sf, parsed))
        except Exception as e:
            log(f"similar_files error: {e}", xbmc.LOGWARNING)

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def select_stream(params: dict):
    scored = _find_streams(params)
    if not scored:
        xbmcgui.Dialog().ok("Webshare", "Žádné streamy nenalezeny.")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    max_results = [10, 15, 25][int(ADDON.getSetting("max_results") or 1)]
    top = scored[:max_results]

    xbmcplugin.setContent(HANDLE, "videos")
    for _score, fdata, parsed in top:
        label = stream_label(parsed, fdata)
        li = xbmcgui.ListItem(label)
        li.setInfo("video", {"title": label, "mediatype": "video"})
        li.setProperty("IsPlayable", "true")

        art: dict[str, str] = {}
        stripe = fdata.get("stripe", "")
        img = fdata.get("img", "")
        if stripe:
            art["thumb"] = stripe
            art["fanart"] = stripe
        elif img:
            art["thumb"] = img
        if art:
            li.setArt(art)

        desc = fdata.get("description", "")
        if desc:
            li.setInfo("video", {"title": label, "plot": desc, "mediatype": "video"})

        dl_enabled = setting("download_enabled") == "true"
        if dl_enabled:
            dl_url = url(
                action="download",
                ident=fdata["ident"],
                filename=encode(fdata.get("name", "file")),
            )
            li.addContextMenuItems([
                ("Stáhnout pro offline", f"RunPlugin({dl_url})"),
            ])

        target = url(
            action="play",
            ident=fdata["ident"],
            type=params.get("type", "movie"),
            title=params.get("title", ""),
            original_title=params.get("original_title", ""),
            year=params.get("year", ""),
            tmdb_id=params.get("tmdb_id", ""),
            imdb_id=params.get("imdb_id", ""),
            season=params.get("season", ""),
            episode=params.get("episode", ""),
            stream_label=encode(label),
        )
        xbmcplugin.addDirectoryItem(HANDLE, target, li, isFolder=False)

        if dl_enabled:
            dl_li = xbmcgui.ListItem(f"[I][Stáhnout] {label}[/I]")
            dl_li.setInfo("video", {"title": f"Stáhnout: {label}", "mediatype": "video"})
            dl_li.setProperty("IsPlayable", "false")
            if art:
                dl_li.setArt(art)
            xbmcplugin.addDirectoryItem(HANDLE, dl_url, dl_li, isFolder=False)

    xbmcplugin.endOfDirectory(HANDLE)


_SUBTITLE_EXTS = {"srt", "sub", "ssa", "ass", "vtt"}


def _find_tied_subtitle_idents(ws_client, video_ident: str) -> list[str]:
    """Return idents of subtitle files tied to the given video."""
    sub_idents: list[str] = []
    try:
        tied = ws_client.tied_files(video_ident)
        for tf in tied:
            ext = (tf.get("type") or "").lower()
            if ext in _SUBTITLE_EXTS:
                sub_idents.append(tf["ident"])
    except Exception as e:
        log(f"tied_files error: {e}", xbmc.LOGWARNING)

    if not sub_idents:
        try:
            sim = ws_client.similar_files(video_ident)
            for sf in sim.get("subtitles", []):
                ext = (sf.get("type") or "").lower()
                if ext in _SUBTITLE_EXTS:
                    sub_idents.append(sf["ident"])
                    if len(sub_idents) >= 3:
                        break
        except Exception as e:
            log(f"similar_files subtitles error: {e}", xbmc.LOGWARNING)

    return sub_idents


def resolve_subtitle(params: dict):
    """Generate a fresh Webshare link for a subtitle ident and redirect Kodi to it."""
    ws_client = get_ws()
    if not ws_client:
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    ident = params.get("ident", "")
    try:
        link = ws_client.file_link(ident)
        li = xbmcgui.ListItem(path=link)
        xbmcplugin.setResolvedUrl(HANDLE, True, li)
    except Exception as e:
        log(f"subtitle resolve error: {e}", xbmc.LOGERROR)
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())


def _play_ident(ident: str, meta: dict | None = None):
    ws = get_ws()
    if not ws:
        return
    try:
        link = ws.file_link(ident)
    except Exception as e:
        log(f"file_link error: {e}", xbmc.LOGERROR)
        xbmcgui.Dialog().ok("Webshare", f"Nelze získat odkaz:\n{e}")
        return

    li = xbmcgui.ListItem(path=link)
    li.setProperty("IsPlayable", "true")

    # Subtitles: pass plugin URLs (fresh link generated at load time, not now)
    sub_idents = _find_tied_subtitle_idents(ws, ident)
    if sub_idents:
        sub_urls = [url(action="subtitle", ident=si) for si in sub_idents]
        li.setSubtitles(sub_urls)
        log(f"Attached {len(sub_urls)} Webshare subtitle(s)")

    if meta:
        entry = dict(meta)
        entry["ident"] = ident
        watch_history.add(entry)

    xbmcplugin.setResolvedUrl(HANDLE, True, li)


def play_ident(params: dict):
    meta = None
    if params.get("title"):
        meta = {
            "type": params.get("type", "movie"),
            "title": decode(params.get("title", "")),
            "original_title": decode(params.get("original_title", "")),
            "year": decode(params.get("year", "")),
            "tmdb_id": params.get("tmdb_id", ""),
            "imdb_id": decode(params.get("imdb_id", "")),
            "poster": decode(params.get("poster", "")),
            "fanart": decode(params.get("fanart", "")),
            "stream_label": decode(params.get("stream_label", "")),
        }
        if params.get("season"):
            meta["season"] = int(params["season"])
        if params.get("episode"):
            meta["episode"] = int(params["episode"])
    _play_ident(params.get("ident", ""), meta=meta)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def show_history(params: dict):
    items = watch_history.get_history(limit=30)
    if not items:
        xbmcgui.Dialog().ok("Historie", "Zatím jsi nic nepřehrál.")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    xbmcplugin.setContent(HANDLE, "videos")
    for entry in items:
        title = entry.get("title", "")
        year  = entry.get("year", "")
        label = f"{title} ({year})" if year else title

        if entry.get("type") == "series" and entry.get("season") and entry.get("episode"):
            label += f" - S{entry['season']:02d}E{entry['episode']:02d}"

        sl = entry.get("stream_label", "")
        if sl:
            label += f"  [{sl}]"

        li = xbmcgui.ListItem(label)
        li.setArt({"poster": entry.get("poster", ""), "fanart": entry.get("fanart", ""),
                    "thumb": entry.get("poster", "")})
        li.setInfo("video", {"title": label, "mediatype": "video"})
        li.setProperty("IsPlayable", "true")

        ident = entry.get("ident", "")
        if ident:
            target = url(action="play", ident=ident)
        else:
            target = url(
                action="select_stream",
                type=entry.get("type", "movie"),
                tmdb_id=str(entry.get("tmdb_id", "")),
                imdb_id=encode(entry.get("imdb_id", "")),
                title=encode(title),
                original_title=encode(entry.get("original_title", title)),
                year=encode(year),
                season=str(entry.get("season", "")),
                episode=str(entry.get("episode", "")),
            )
        xbmcplugin.addDirectoryItem(HANDLE, target, li, isFolder=False)

    # clear history option
    li = xbmcgui.ListItem("[I]Vymazat historii[/I]")
    li.setProperty("IsPlayable", "false")
    xbmcplugin.addDirectoryItem(HANDLE, url(action="clear_history"), li, isFolder=False)

    xbmcplugin.endOfDirectory(HANDLE)


def continue_series(params: dict):
    series_list = watch_history.get_series_progress()
    if not series_list:
        xbmcgui.Dialog().ok("Seriály", "Zatím jsi nepřehrál žádný seriál.")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    xbmcplugin.setContent(HANDLE, "videos")
    for s in series_list:
        next_ep = watch_history.get_next_episode(s.get("tmdb_id", ""))
        if not next_ep:
            continue
        season, episode = next_ep
        label = f"{s.get('title', '')} - další: S{season:02d}E{episode:02d}"

        li = xbmcgui.ListItem(label)
        li.setArt({"poster": s.get("poster", ""), "fanart": s.get("fanart", "")})
        li.setInfo("video", {"title": label, "mediatype": "tvshow"})
        li.setProperty("IsPlayable", "false")

        target = url(
            action="select_stream",
            type="series",
            tmdb_id=s.get("tmdb_id", ""),
            imdb_id=encode(s.get("imdb_id", "")),
            title=encode(s.get("title", "")),
            original_title=encode(s.get("original_title", s.get("title", ""))),
            season=str(season),
            episode=str(episode),
        )
        xbmcplugin.addDirectoryItem(HANDLE, target, li, isFolder=True)

    xbmcplugin.endOfDirectory(HANDLE)


def clear_history(params: dict):
    if xbmcgui.Dialog().yesno("Historie", "Opravdu vymazat celou historii?"):
        watch_history.clear()
        xbmc.executebuiltin("Container.Refresh")
    xbmcplugin.endOfDirectory(HANDLE, succeeded=False)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Download for offline playback
# ---------------------------------------------------------------------------

def download_file(params: dict):
    """Download a Webshare file to the user-configured folder."""
    import requests as _requests

    dl_folder = setting("download_folder")
    if not dl_folder:
        xbmcgui.Dialog().ok(
            "Stahování",
            "Nastav složku pro stahování v nastavení addonu.",
        )
        xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
        return

    ws = get_ws()
    if not ws:
        return

    ident = params.get("ident", "")
    filename = decode(params.get("filename", "file"))
    if not ident:
        log("download_file: missing ident", xbmc.LOGERROR)
        return

    try:
        link = ws.file_download_link(ident)
    except Exception as e:
        log(f"download_file: link error: {e}", xbmc.LOGERROR)
        xbmcgui.Dialog().ok("Stahování", f"Nelze získat odkaz:\n{e}")
        return

    dest = os.path.join(dl_folder, filename)
    if xbmcvfs.exists(dest):
        if not xbmcgui.Dialog().yesno(
            "Stahování", f"Soubor už existuje:\n{filename}\n\nPřepsat?"
        ):
            return

    log(f"download_file: {filename} → {dest}")
    progress = xbmcgui.DialogProgress()
    progress.create("Stahování", f"Stahuji: {filename}")

    try:
        resp = _requests.get(link, stream=True, timeout=30)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        chunk_size = 64 * 1024

        with open(xbmcvfs.translatePath(dest), "wb") as f:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if progress.iscanceled():
                    log("download_file: cancelled by user")
                    f.close()
                    xbmcvfs.delete(dest)
                    xbmcgui.Dialog().notification(
                        "Stahování", "Stahování zrušeno", xbmcgui.NOTIFICATION_WARNING
                    )
                    return
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = int(downloaded * 100 / total)
                    mb_done = downloaded / (1024 * 1024)
                    mb_total = total / (1024 * 1024)
                    progress.update(pct, f"Stahuji: {filename}",
                                    f"{mb_done:.1f} / {mb_total:.1f} MB")
    except Exception as e:
        log(f"download_file: error: {e}", xbmc.LOGERROR)
        progress.close()
        xbmcgui.Dialog().ok("Stahování", f"Chyba při stahování:\n{e}")
        return

    progress.close()
    xbmcgui.Dialog().notification(
        "Stahování", f"Staženo: {filename}", xbmcgui.NOTIFICATION_INFO
    )
    log(f"download_file: done, {downloaded} bytes")


def browse_downloads(params: dict):
    """List downloaded files for offline playback."""
    dl_folder = setting("download_folder")
    if not dl_folder:
        xbmcgui.Dialog().ok(
            "Stažené soubory",
            "Nastav složku pro stahování v nastavení addonu.",
        )
        xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    real_path = xbmcvfs.translatePath(dl_folder)
    if not os.path.isdir(real_path):
        xbmcgui.Dialog().ok("Stažené soubory", "Složka neexistuje.")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    video_exts = {".mkv", ".avi", ".mp4", ".m4v", ".mov", ".wmv", ".ts", ".flv", ".webm"}
    files = []
    for fname in os.listdir(real_path):
        ext = os.path.splitext(fname)[1].lower()
        if ext in video_exts:
            fpath = os.path.join(real_path, fname)
            size_mb = os.path.getsize(fpath) / (1024 * 1024)
            files.append((fname, fpath, size_mb))
    files.sort(key=lambda x: os.path.getmtime(x[1]), reverse=True)

    if not files:
        xbmcgui.Dialog().ok("Stažené soubory", "Žádné stažené soubory.")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    xbmcplugin.setContent(HANDLE, "videos")
    for fname, fpath, size_mb in files:
        label = f"{fname}  [{size_mb:.0f} MB]"
        li = xbmcgui.ListItem(label)
        li.setProperty("IsPlayable", "true")
        li.setInfo("video", {"title": fname, "mediatype": "video"})
        li.addContextMenuItems([
            ("Smazat soubor", f"RunPlugin({url(action='delete_download', path=encode(fpath))})"),
        ])
        xbmcplugin.addDirectoryItem(HANDLE, fpath, li, isFolder=False)
    xbmcplugin.endOfDirectory(HANDLE, succeeded=True, cacheToDisc=False)


def delete_download(params: dict):
    """Delete a downloaded file."""
    fpath = decode(params.get("path", ""))
    if not fpath:
        return
    fname = os.path.basename(fpath)
    if xbmcgui.Dialog().yesno("Smazat", f"Opravdu smazat?\n{fname}"):
        try:
            xbmcvfs.delete(fpath)
            xbmcgui.Dialog().notification(
                "Stažené soubory", f"Smazáno: {fname}", xbmcgui.NOTIFICATION_INFO
            )
            xbmc.executebuiltin("Container.Refresh")
        except Exception as e:
            log(f"delete_download error: {e}", xbmc.LOGERROR)


def router(params: dict):
    action = params.get("action", "main")
    log(f"action={action} params={params}")
    dispatch = {
        "main":           lambda: main_menu(),
        "search_movies":  lambda: search_movies(params),
        "search_series":  lambda: search_series(params),
        "browse":         lambda: browse(params),
        "browse_list":    lambda: browse_list(params),
        "seasons":        lambda: seasons(params),
        "episodes":       lambda: episodes(params),
        "select_stream":  lambda: select_stream(params),
        "play":           lambda: play_ident(params),
        "subtitle":       lambda: resolve_subtitle(params),
        "history":        lambda: show_history(params),
        "continue_series": lambda: continue_series(params),
        "clear_history":  lambda: clear_history(params),
        "download":       lambda: download_file(params),
        "downloads":      lambda: browse_downloads(params),
        "delete_download": lambda: delete_download(params),
        "settings":       lambda: _do_settings(),
    }
    handler = dispatch.get(action)
    if handler:
        handler()
    else:
        log(f"Unknown action: {action}", xbmc.LOGWARNING)
        main_menu()


if __name__ == "__main__":
    try:
        params = dict(parse_qsl(sys.argv[2][1:])) if len(sys.argv) > 2 else {}
        router(params)
    except Exception:
        import traceback
        tb = traceback.format_exc()
        try:
            xbmc.log(f"[plugin.video.cws] FATAL: {tb}", xbmc.LOGERROR)
        except Exception:
            pass
        try:
            xbmcgui.Dialog().textviewer("Stream Cinema - Chyba", tb)
        except Exception:
            pass
