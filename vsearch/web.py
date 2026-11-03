"""HTTP API and the search page. Auth rides on Immich: the user's Immich session cookie
(same origin when served under Immich's address at /vsearch) or an Immich API key."""
import hashlib
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import config, db, search
from .services import services

log = logging.getLogger("vsearch.web")
STATIC = Path(__file__).parent / "static"
AUTH_TTL = 120
COOKIE_AGE = 400 * 24 * 3600
NO_CACHE = {"Cache-Control": "no-cache"}

_auth_cache: dict[str, tuple[float, dict]] = {}
_owner_cache: dict[str, str] = {}
immich: httpx.AsyncClient


@asynccontextmanager
async def lifespan(_):
    global immich
    immich = httpx.AsyncClient(base_url=config.IMMICH_URL, timeout=httpx.Timeout(60, connect=10))
    yield
    await immich.aclose()


api = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


class StripPrefix:
    """Tailscale serve mounts the page at /vsearch; accept paths with or without it."""

    def __init__(self, app, prefix: str = "/vsearch"):
        self.app, self.prefix = app, prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope["path"]
            if path == self.prefix or path.startswith(self.prefix + "/"):
                scope = dict(scope, path=path[len(self.prefix):] or "/")
        await self.app(scope, receive, send)


app = StripPrefix(api)


def _auth_headers(request: Request) -> dict | None:
    if key := request.headers.get("x-api-key"):
        return {"x-api-key": key}
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return {"Authorization": auth}
    if token := request.cookies.get("immich_access_token"):
        return {"Authorization": f"Bearer {token}"}
    return None


async def current_user(request: Request) -> dict:
    headers = _auth_headers(request)
    if not headers:
        raise HTTPException(401, "Not logged in")
    key = hashlib.sha256(repr(sorted(headers.items())).encode()).hexdigest()
    hit = _auth_cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    resp = await immich.get("/api/users/me", headers=headers)
    if resp.status_code != 200:
        raise HTTPException(401, "Immich session expired, log in again")
    me = resp.json()
    user = {"id": me["id"], "name": me.get("name") or me.get("email"), "email": me.get("email"), "headers": headers}
    _auth_cache[key] = (time.monotonic() + AUTH_TTL, user)
    return user


def _owner_of(asset_id: str) -> str | None:
    if asset_id not in _owner_cache:
        with services.pool.connection() as conn:
            row = conn.execute('select "ownerId" from asset where id = %s and "deletedAt" is null', (asset_id,)).fetchone()
        if not row:
            return None
        _owner_cache[asset_id] = str(row[0])
    return _owner_cache[asset_id]


async def require_owner(user: dict, asset_id: UUID) -> None:
    if await run_in_threadpool(_owner_of, str(asset_id)) != user["id"]:
        raise HTTPException(404, "Not found")


@api.get("/health")
async def health():
    state = services.indexer.state if services.indexer else {}
    return {"ok": True, "indexer": state.get("phase"), "error": state.get("error")}


@api.get("/")
async def page():
    return FileResponse(STATIC / "index.html", headers=NO_CACHE)


@api.get("/icon.svg")
async def icon():
    return FileResponse(STATIC / "icon.svg", media_type="image/svg+xml")


@api.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


class Login(BaseModel):
    email: str
    password: str


@api.post("/api/login")
async def login(body: Login, request: Request):
    resp = await immich.post("/api/auth/login", json={"email": body.email, "password": body.password})
    if resp.status_code not in (200, 201):
        raise HTTPException(401, "Wrong email or password")
    token = resp.json()["accessToken"]
    out = JSONResponse({"ok": True})
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    # Same cookies Immich's own web app sets, so logging in here logs into Immich too.
    out.set_cookie("immich_access_token", token, max_age=COOKIE_AGE, httponly=True, secure=secure, samesite="lax")
    out.set_cookie("immich_auth_type", "password", max_age=COOKIE_AGE, httponly=True, secure=secure, samesite="lax")
    out.set_cookie("immich_is_authenticated", "true", max_age=COOKIE_AGE, secure=secure, samesite="lax")
    return out


@api.get("/api/me")
async def me(request: Request):
    user = await current_user(request)
    return {"name": user["name"], "email": user["email"], "immichUrl": config.IMMICH_PUBLIC_URL}


@api.get("/api/status")
async def status(request: Request):
    user = await current_user(request)

    def counts():
        with services.pool.connection() as conn:
            return db.status_counts(conn, user["id"])

    try:
        result = await run_in_threadpool(counts)
    except Exception:  # schema not created yet on a fresh install
        result = {"videos": 0, "indexed": 0, "failed": 0, "pending": 0}
    result["indexer"] = {k: services.indexer.state.get(k) for k in ("phase", "current")} if services.indexer else None
    return result


@api.get("/api/search")
async def do_search(request: Request, q: str = "", kind: str = "all"):
    user = await current_user(request)
    kinds = search.KINDS if kind == "all" else tuple(k for k in kind.split(",") if k in search.KINDS)
    started = time.perf_counter()
    results = await run_in_threadpool(search.run, services.pool, services.ml, services.embedder,
                                      user["id"], q, kinds)
    return {"query": q, "took_ms": round((time.perf_counter() - started) * 1000), "results": results}


@api.get("/api/frame/{asset_id}/{ms}.webp")
async def frame(asset_id: UUID, ms: int, request: Request):
    await require_owner(await current_user(request), asset_id)
    path = Path(config.STATE_DIR) / "frames" / str(asset_id) / f"{ms}.webp"
    if not path.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})


@api.get("/api/play/{asset_id}")
async def play(asset_id: UUID, request: Request):
    """Streams the video through Immich's own playback endpoint (handles transcodes, seeking)."""
    user = await current_user(request)
    await require_owner(user, asset_id)
    headers = dict(user["headers"])
    if rng := request.headers.get("range"):
        headers["Range"] = rng
    upstream = await immich.send(immich.build_request("GET", f"/api/assets/{asset_id}/video/playback",
                                                      headers=headers), stream=True)
    keep = ("content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified")
    passthrough = {k: v for k, v in upstream.headers.items() if k.lower() in keep}

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(body(), status_code=upstream.status_code, headers=passthrough)


class Album(BaseModel):
    name: str
    assetIds: list[UUID]


@api.post("/api/album")
async def album(body: Album, request: Request):
    user = await current_user(request)
    for asset_id in body.assetIds:
        await require_owner(user, asset_id)
    resp = await immich.post("/api/albums", headers=user["headers"],
                             json={"albumName": body.name[:100], "assetIds": [str(a) for a in body.assetIds]})
    if resp.status_code not in (200, 201):
        raise HTTPException(502, f"Immich refused to create the album ({resp.status_code})")
    return {"id": resp.json()["id"]}
