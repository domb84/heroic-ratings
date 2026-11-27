"""IGDB (needs a Twitch developer key): scores, lengths, play styles, videos; the credentials."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config as cfg
from . import matching
from .config import MATCH_THRESHOLD
from .logs import log


class IGDB:
    """IGDB's time-to-beat data (needs a free Twitch developer app: client ID + secret).
    Games are found by their Steam app ID, or by name when there's no Steam match."""
    API = "https://api.igdb.com/v4/"
    TOKEN_URL = "https://id.twitch.tv/oauth2/token"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id, self.client_secret = client_id, client_secret
        self.token, self.token_expires = None, 0.0
        self.last = 0.0
        self.source_field = "external_game_source"   # newer name; older API used "category"

    def _get_token(self):
        q = urllib.parse.urlencode({"client_id": self.client_id, "client_secret": self.client_secret,
                                    "grant_type": "client_credentials"})
        with urllib.request.urlopen(urllib.request.Request(f"{self.TOKEN_URL}?{q}", data=b"", method="POST"),
                                    timeout=30) as r:
            j = json.load(r)
        self.token, self.token_expires = j["access_token"], time.time() + j.get("expires_in", 3600) - 300

    def query(self, endpoint: str, body: str) -> list:
        for attempt in range(2):
            if not self.token or time.time() > self.token_expires:
                self._get_token()
            wait = self.last + 0.3 - time.monotonic()      # IGDB allows 4 requests per second
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            req = urllib.request.Request(self.API + endpoint, data=body.encode(), method="POST", headers={
                "Client-ID": self.client_id, "Authorization": f"Bearer {self.token}", "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    self.token = None
                    continue
                raise
        return []

    def steam_to_igdb(self, appids: list[int]) -> dict[int, int]:
        """{Steam app ID: IGDB game ID} for up to 500 app IDs."""
        uids = ",".join(f'"{a}"' for a in appids)
        try:
            ext = self.query("external_games", f"fields game,uid; where uid = ({uids}) & {self.source_field} = 1; limit 500;")
        except urllib.error.HTTPError as e:
            if e.code != 400 or self.source_field == "category":
                raise
            self.source_field = "category"   # older API: Steam is category 1
            ext = self.query("external_games", f"fields game,uid; where uid = ({uids}) & category = 1; limit 500;")
        return {int(r["uid"]): r["game"] for r in ext if str(r.get("uid", "")).isdigit() and r.get("game")}

    EPIC_SOURCE, GOG_SOURCE = 26, 5
    EPIC_URL = "https://store.epicgames.com/en-US/p/"

    def games_by_store(self, epic_slugs: list[str], gog_ids: list[str]) -> dict[tuple[str, str], int]:
        """{("epic", slug) | ("gog", product ID): IGDB game ID}, from IGDB's store links, in
        bulk. (IGDB keeps Epic games under an internal ID, but with the store page's address.)"""
        out: dict[tuple[str, str], int] = {}
        epic = sorted({s for s in epic_slugs if s})
        for i in range(0, len(epic), 200):
            urls = ",".join(json.dumps(self.EPIC_URL + s) for s in epic[i:i + 200])
            for r in self.query("external_games", f"fields url,game; where {self.source_field} = {self.EPIC_SOURCE} "
                                                  f"& url = ({urls}); limit 500;"):
                if r.get("game") and str(r.get("url", "")).startswith(self.EPIC_URL):
                    out[("epic", r["url"][len(self.EPIC_URL):])] = r["game"]
        gog = sorted({g for g in gog_ids if str(g).isdigit()})
        for i in range(0, len(gog), 400):
            uids = ",".join(f'"{g}"' for g in gog[i:i + 400])
            for r in self.query("external_games", f"fields uid,game; where {self.source_field} = {self.GOG_SOURCE} "
                                                  f"& uid = ({uids}); limit 500;"):
                if r.get("game"):
                    out[("gog", str(r["uid"]))] = r["game"]
        return out

    def igdb_to_steam(self, game_ids: list[int]) -> dict[int, int]:
        """{IGDB game ID: Steam app ID} for games IGDB knows a Steam page of."""
        if not game_ids:
            return {}
        ext = self.query("external_games", f"fields game,uid; where game = ({','.join(map(str, game_ids))}) "
                                           f"& {self.source_field} = 1; limit 500;")
        return {r["game"]: int(r["uid"]) for r in ext if str(r.get("uid", "")).isdigit() and r.get("game")}

    def search_name(self, title: str) -> tuple[int, str, float] | None:
        """Best IGDB match for a title (also checks IGDB's alternative names): (id, name, score)."""
        best, best_sc = None, 0.0
        for q in matching.search_queries(title)[:2]:
            safe = q.replace('"', " ")
            for r in self.query("games", f'search "{safe}"; fields id,name,alternative_names.name; limit 10;'):
                names = [r.get("name", "")] + [a.get("name", "") for a in r.get("alternative_names") or []]
                sc = max(max(matching.similarity(title, n), matching.similarity(q, n)) for n in names if n) if any(names) else 0
                if sc > best_sc:
                    best, best_sc = r, sc
            if best_sc >= MATCH_THRESHOLD:
                break
        if not best or best_sc < MATCH_THRESHOLD:
            return None
        return best["id"], best.get("name", ""), round(best_sc, 3)

    DETAIL_FIELDS = ("name,rating,rating_count,aggregated_rating,aggregated_rating_count,game_modes.name,"
                     "player_perspectives.name,collections.name,franchises.name,similar_games,summary,cover.image_id,"
                     "first_release_date,genres.name,themes.name,involved_companies.developer,"
                     "involved_companies.company.name,multiplayer_modes.campaigncoop,multiplayer_modes.lancoop,"
                     "multiplayer_modes.offlinecoop,multiplayer_modes.offlinecoopmax,multiplayer_modes.offlinemax,"
                     "multiplayer_modes.onlinecoop,multiplayer_modes.onlinecoopmax,multiplayer_modes.onlinemax,"
                     "multiplayer_modes.splitscreen,videos.name,videos.video_id")

    def details(self, game_ids: list[int]) -> dict[int, dict]:
        """Everything the app uses about up to 500 games, including time to beat."""
        if not game_ids:
            return {}
        idl = ",".join(map(str, game_ids))
        rows = self.query("games", f"fields {self.DETAIL_FIELDS}; where id = ({idl}); limit 500;")
        ttb = {r["game_id"]: r for r in self.query(
            "game_time_to_beats", f"fields game_id,hastily,normally,completely; where game_id = ({idl}); limit 500;")
            if r.get("game_id")}
        names = lambda xs: [x["name"] for x in xs or [] if isinstance(x, dict) and x.get("name")]
        # 999h-style values are placeholders for endless games, not real lengths
        hrs = lambda v: round(v / 3600, 1) if v and v < 900 * 3600 else None
        out = {}
        for r in rows:
            mp = {}
            for m in r.get("multiplayer_modes") or []:
                for k, v in m.items():
                    if k == "id" or not v:
                        continue
                    mp[k] = max(mp.get(k, 0), v) if isinstance(v, int) and not isinstance(v, bool) else True
            t = ttb.get(r["id"], {})
            cover = (r.get("cover") or {}).get("image_id")
            rel = r.get("first_release_date")
            out[r["id"]] = {
                "id": r["id"], "name": r.get("name", ""),
                "user": round(r["rating"]) if r.get("rating") else None, "user_n": r.get("rating_count") or 0,
                "critic": round(r["aggregated_rating"]) if r.get("aggregated_rating") else None,
                "critic_n": r.get("aggregated_rating_count") or 0,
                "modes": names(r.get("game_modes")), "persp": names(r.get("player_perspectives")),
                "series": names(r.get("collections")) or names(r.get("franchises")),
                "similar": [x for x in r.get("similar_games") or [] if isinstance(x, int)],
                "summary": r.get("summary", ""),
                "cover": f"https://images.igdb.com/igdb/image/upload/t_cover_big/{cover}.jpg" if cover else None,
                "year": time.gmtime(rel).tm_year if rel else None,
                "devs": [c["company"]["name"] for c in r.get("involved_companies") or []
                         if c.get("developer") and (c.get("company") or {}).get("name")],
                "genres": names(r.get("genres")), "themes": names(r.get("themes")), "mp": mp,
                "main": hrs(t.get("hastily")), "extra": hrs(t.get("normally")), "comp": hrs(t.get("completely")),
                "videos": _igdb_videos(r.get("videos")),
            }
        return out

    def videos(self, game_id: int) -> list[dict]:
        rows = self.query("games", f"fields videos.name,videos.video_id; where id = {int(game_id)};")
        return _igdb_videos(rows[0].get("videos")) if rows else []


def _igdb_videos(vs) -> list[dict]:
    """IGDB's videos are YouTube IDs with a label ("Trailer", "Gameplay video", …)."""
    return [{"name": v.get("name") or "Video", "id": v["video_id"]} for v in vs or []
            if isinstance(v, dict) and re.fullmatch(r"[\w-]{6,20}", v.get("video_id") or "")]


TWITCH_ID_RE = re.compile(r"[A-Za-z0-9]{10,64}")


def twitch_settings() -> dict:
    """What the settings page may show about the Twitch/IGDB credentials (never the secret)."""
    if os.environ.get("HEROIC_RATINGS_TWITCH_CLIENT_ID") and os.environ.get("HEROIC_RATINGS_TWITCH_CLIENT_SECRET"):
        source = "env"
    elif (cfg.DATA_DIR / "twitch.json").exists():
        source = "file"
    else:
        source = None
    creds = twitch_credentials()
    return {"set": bool(creds), "source": source,
            "client_id_hint": (creds[0][:4] + "…" + creds[0][-3:]) if creds else ""}


def save_twitch_credentials(client_id: str, client_secret: str, check=True) -> dict:
    """Check the credentials with Twitch, then store them in <data>/twitch.json (readable only by you)."""
    client_id, client_secret = (client_id or "").strip(), (client_secret or "").strip()
    if not TWITCH_ID_RE.fullmatch(client_id) or not TWITCH_ID_RE.fullmatch(client_secret):
        return {"ok": False, "error": "That doesn't look like a Twitch client ID and secret (letters and digits only)."}
    if check:
        try:
            IGDB(client_id, client_secret)._get_token()
        except urllib.error.HTTPError as e:
            e.close()
            return {"ok": False, "error": f"Twitch didn't accept these credentials (HTTP {e.code}). Check the client ID and secret."}
        except (OSError, ValueError, KeyError) as e:
            return {"ok": False, "error": f"Couldn't reach Twitch to check them ({type(e).__name__}). Try again when online."}
    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = cfg.DATA_DIR / "twitch.json"
    tmp = f.with_name("twitch.json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump({"client_id": client_id, "client_secret": client_secret}, out)
    os.replace(tmp, f)
    log("Twitch/IGDB credentials saved")
    return {"ok": True, **twitch_settings()}


def remove_twitch_credentials() -> dict:
    if twitch_settings()["source"] == "env":
        return {"ok": False, "error": "These come from environment variables; remove them there."}
    (cfg.DATA_DIR / "twitch.json").unlink(missing_ok=True)
    log("Twitch/IGDB credentials removed")
    return {"ok": True, **twitch_settings()}


def test_twitch_credentials() -> dict:
    """Try the saved credentials: get a Twitch token and ask IGDB for one game."""
    creds = twitch_credentials()
    if not creds:
        return {"ok": False, "error": "No Twitch client ID and secret are set."}
    try:
        IGDB(*creds).query("games", "fields id; limit 1;")
    except urllib.error.HTTPError as e:
        e.close()
        what = "Twitch didn't accept the client ID and secret" if "twitch" in (e.url or "") else "IGDB refused the request"
        return {"ok": False, "error": f"{what} (HTTP {e.code})."}
    except (OSError, ValueError, KeyError, RuntimeError) as e:
        return {"ok": False, "error": f"Couldn't reach Twitch or IGDB ({type(e).__name__}). Check you're online."}
    return {"ok": True}


def twitch_credentials() -> tuple[str, str] | None:
    """Twitch developer app credentials for IGDB, from the environment or data/twitch.json."""
    cid, secret = os.environ.get("HEROIC_RATINGS_TWITCH_CLIENT_ID"), os.environ.get("HEROIC_RATINGS_TWITCH_CLIENT_SECRET")
    if not (cid and secret):
        try:
            j = json.loads((cfg.DATA_DIR / "twitch.json").read_text())
            cid, secret = j.get("client_id"), j.get("client_secret")
        except (OSError, ValueError):
            return None
    return (cid.strip(), secret.strip()) if cid and secret else None
