"""HTTP API and the search page. Auth rides on Immich: the user's Immich session cookie
(same origin when served under Immich's address at /vsearch) or an Immich API key."""
import asyncio
import hashlib
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import config, db, immich_search, search
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
    """The caller's Immich credentials, in any of the forms Immich accepts."""
    if key := request.headers.get("x-api-key"):
        return {"x-api-key": key}
    for name in ("x-immich-user-token", "x-immich-session-token"):  # Immich mobile apps
        if token := request.headers.get(name):
            return {name: token}
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
    # lib.js is inlined: some browsers/blockers refuse separate script files from a tailnet
    # host (seen as ERR_BLOCKED_BY_CLIENT), and then the page stays blank.
    lib = re.sub(r"^export ", "", (STATIC / "lib.js").read_text(), flags=re.M)
    html = (STATIC / "index.html").read_text().replace(
        "import { sha1File, dateFromName, MEDIA } from './lib.js';", lib)
    return Response(html, media_type="text/html", headers=NO_CACHE)


@api.get("/lib.js")
async def lib_js():
    return FileResponse(STATIC / "lib.js", media_type="text/javascript", headers=NO_CACHE)


@api.get("/icon.svg")
async def icon():
    return FileResponse(STATIC / "icon.svg", media_type="image/svg+xml")


@api.get("/sw.js")
async def service_worker():
    # Needed for "Install app". Scope is /vsearch/ only, so it never touches Immich's pages.
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@api.get("/icon-{size}.png")
async def icon_png(size: int):
    if size not in (192, 512):
        raise HTTPException(404, "Not found")
    return FileResponse(STATIC / f"icon-{size}.png", media_type="image/png")


@api.get("/api/stats")
async def stats(request: Request):
    user = await current_user(request)

    def collect():
        with services.pool.connection() as conn:
            return db.stats(conn, user["id"])

    result = await run_in_threadpool(collect)
    try:
        st = os.statvfs("/data")
        result["disk"] = {"total": st.f_blocks * st.f_frsize, "free": st.f_bavail * st.f_frsize}
    except OSError:
        result["disk"] = None
    result["indexer"] = {k: services.indexer.state.get(k) for k in ("phase", "current", "error")} if services.indexer else None
    return result


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
    return {"id": user["id"], "name": user["name"], "email": user["email"], "immichUrl": config.IMMICH_PUBLIC_URL}


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


HOOK_TIMEOUT = 8  # seconds
FORWARD_HEADERS = ("authorization", "cookie", "x-api-key", "x-immich-user-token", "x-immich-session-token",
                   "x-immich-share-key", "x-immich-share-slug", "user-agent", "accept-language", "x-correlation-id")


@api.post("/hook/smart-search")
@api.post("/hook/smart-search/{rest:path}")
async def immich_smart_search(request: Request, rest: str = ""):
    """Stands in for Immich's POST /api/search/smart (routed here by tailscale serve):
    asks Immich, then blends in video moments. Any problem -> Immich's answer untouched."""
    raw = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARD_HEADERS}
    headers["content-type"] = "application/json"
    upstream = await immich.post("/api/search/smart", content=raw, headers=headers)
    passthrough = Response(upstream.content, status_code=upstream.status_code,
                           media_type=upstream.headers.get("content-type", "application/json"))
    if upstream.status_code != 200:
        return passthrough
    try:
        body = json.loads(raw or b"{}")
        if not immich_search.mergeable(body) or _auth_headers(request) is None:
            return passthrough
        user = await current_user(request)
        data = upstream.json()
        items = data["assets"]["items"]
        page = int(body.get("page") or 1)
        size = int(body.get("size") or len(items) or 100)
        # Immich's search bar must never wait on us: past the limit, plain Immich results.
        ours = await asyncio.wait_for(run_in_threadpool(search.run, services.pool, services.ml, services.embedder,
                                                        user["id"], body["query"], search.KINDS, max(size, 40)),
                                      timeout=HOOK_TIMEOUT)
        if not ours:
            return passthrough
        ours_ids = {r["assetId"] for r in ours}
        if page > 1:
            # Those videos were already moved up onto page 1; don't show them twice.
            data["assets"]["items"] = [i for i in items if i["id"] not in ours_ids]
        else:
            by_id = {i["id"]: i for i in items}
            missing = [r["assetId"] for r in ours if r["assetId"] not in by_id]
            fetched = await asyncio.wait_for(asyncio.gather(
            *(immich.get(f"/api/assets/{a}", headers=user["headers"]) for a in missing)), timeout=HOOK_TIMEOUT)
            by_id.update({r.json()["id"]: r.json() for r in fetched if r.status_code == 200})
            order = [a for a in immich_search.blend(items, ours) if a in by_id]
            data["assets"]["items"] = [by_id[a] for a in order]
        data["assets"]["count"] = len(data["assets"]["items"])
        return JSONResponse(data)
    except Exception as e:  # never break Immich's own search
        log.warning("smart search merge skipped: %r", e)
        return passthrough


class PendriveProgress(BaseModel):
    name: str
    album_id: UUID | None = None
    files_total: int | None = None
    uploaded: int | None = None
    skipped: int | None = None
    failed: int | None = None
    bytes_total: int | None = None
    bytes_done: int | None = None
    status: str | None = None
    last_error: str | None = None


@api.get("/api/pendrives")
async def list_pendrives(request: Request):
    user = await current_user(request)

    def rows():
        with services.pool.connection() as conn:
            return db.pendrives(conn, user["id"])

    return {"pendrives": await run_in_threadpool(rows)}


@api.post("/api/pendrives")
async def pendrive_progress(body: PendriveProgress, request: Request):
    user = await current_user(request)
    name = " ".join(body.name.split())[:80]
    if not name:
        raise HTTPException(400, "Give the pendrive a name")
    fields = body.model_dump(exclude_none=True, exclude={"name"})
    if "album_id" in fields:
        fields["album_id"] = str(fields["album_id"])

    def save():
        with services.pool.connection() as conn:
            db.save_pendrive(conn, user["id"], name, fields)

    await run_in_threadpool(save)
    return {"ok": True}


# The page talks to Immich's own API at /api/... (same origin under Tailscale serve). When the
# page is opened directly on its own port instead, these few routes pass those calls through.
IMMICH_PASSTHROUGH = ["/api/assets", "/api/assets/bulk-upload-check", "/api/albums", "/api/albums/{album_id}/assets",
                      "/api/server/ping"]


async def _to_immich(request: Request):
    headers = {k: v for k, v in request.headers.items()
               if k.lower() in FORWARD_HEADERS or k.lower() in ("content-type", "content-length")}
    upstream = await immich.send(immich.build_request(
        request.method, request.url.path, params=request.query_params, headers=headers,
        content=request.stream()), stream=True)

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    keep = ("content-type", "content-length", "content-encoding")
    return StreamingResponse(body(), status_code=upstream.status_code,
                             headers={k: v for k, v in upstream.headers.items() if k.lower() in keep})


for _path in IMMICH_PASSTHROUGH:
    api.add_api_route(_path, _to_immich, methods=["GET", "POST", "PUT"], include_in_schema=False)


# Immich's web app, passed through so its pages can carry our sidebar entries.
# Tailscale serve sends "/" here (as /immich-web/...), while /api and /_app stay direct to
# Immich, so uploads, websockets and assets never touch this path.
INJECT = b"<script data-vsearch-sidebar>" + (STATIC / "sidebar.js").read_bytes() + b"</script>"  # inline, see page()
HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding", "upgrade"}


@api.api_route("/immich-web", methods=["GET", "HEAD", "POST"])
@api.api_route("/immich-web/{path:path}", methods=["GET", "HEAD", "POST"])
async def immich_web(request: Request, path: str = ""):
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS | {"host", "accept-encoding"}}
    upstream = await immich.send(immich.build_request(
        request.method, "/" + path, params=request.query_params, headers=headers,
        content=await request.body() if request.method == "POST" else None), stream=True)
    out_headers = [(k, v) for k, v in upstream.headers.multi_items() if k.lower() not in HOP_HEADERS]
    if "text/html" in upstream.headers.get("content-type", ""):
        html = await upstream.aread()
        await upstream.aclose()
        if b"data-vsearch-sidebar" not in html:
            html = html.replace(b"</head>", INJECT + b"</head>", 1)
        response = Response(html, status_code=upstream.status_code)
    else:
        async def body():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()
        response = StreamingResponse(body(), status_code=upstream.status_code)
    for k, v in out_headers:
        response.headers.append(k, v)
    return response


@api.get("/sidebar.js")
async def sidebar_js():
    return FileResponse(STATIC / "sidebar.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


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
