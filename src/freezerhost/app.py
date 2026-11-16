"""HTTP: the store API (bearer token per store) and the site (shared password): sign-up and map."""
import asyncio
import secrets
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Form, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from .config import Settings
from .passes import PassIssuer
from .world import ApiError, Store, World

STATIC = Path(__file__).parent / "static"


class InstallBody(BaseModel):
    version: str


class DecisionBody(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = None


class SignupBody(BaseModel):
    team: str


def create_app(settings: Settings | None = None, clock: Callable[[], float] = time.time,
               background: bool = True) -> FastAPI:
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    state_path = settings.data_dir / "state.json"
    world = World.load(state_path, settings, PassIssuer(settings.data_dir / "pass-key.pem", settings.pass_ttl_seconds))

    secret_path = settings.data_dir / "session-secret"
    if not settings.session_secret and not secret_path.exists():
        secret_path.write_text(secrets.token_urlsafe(32))
    session_secret = settings.session_secret or secret_path.read_text()

    async def run() -> None:
        saved = 0.0
        while True:
            now = clock()
            world.tick(now)
            if now - saved >= 5:
                world.save(state_path)
                saved = now
            await asyncio.sleep(settings.tick_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(run()) if background else None
        yield
        if task:
            task.cancel()
        world.save(state_path)

    app = FastAPI(title="Freezer workshop host", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.world = world
    app.add_middleware(SessionMiddleware, secret_key=session_secret, max_age=7 * 24 * 3600, same_site="lax")

    @app.exception_handler(ApiError)
    async def api_error(_: Request, error: ApiError) -> JSONResponse:
        return JSONResponse({"detail": {"code": error.code, "message": error.message}}, status_code=error.status)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    # ---- store API -----------------------------------------------------------------------

    async def store_of(store_id: str, authorization: Annotated[str | None, Header()] = None) -> Store:
        scheme, _, token = (authorization or "").partition(" ")
        store = world.by_token(token.strip()) if scheme.lower() == "bearer" and token else None
        if store is None:
            raise ApiError(401, "unauthorized", "Send 'Authorization: Bearer <store token>'.")
        if store.id != store_id:
            raise ApiError(403, "wrong_store", "This token belongs to another store.")
        store.last_call_at = clock()
        return store

    StoreDep = Annotated[Store, Depends(store_of)]
    api = APIRouter(prefix="/api/v1/stores/{store_id}")

    @api.get("")
    @api.get("/")
    async def get_store(store: StoreDep) -> dict:
        return {"id": store.id, "name": store.name, "city": store.city, "team": store.team,
                "freezer_count": len(store.freezers), "current_software": settings.current_version}

    @api.get("/freezers")
    async def freezers(store: StoreDep) -> list[dict]:
        return [freezer.view() for freezer in store.freezers]

    @api.get("/freezers/{freezer_id}")
    async def freezer(store: StoreDep, freezer_id: str) -> dict:
        return store.freezer(freezer_id).view()

    @api.get("/freezers/{freezer_id}/readings")
    async def readings(store: StoreDep, freezer_id: str, since: datetime | None = None,
                       limit: Annotated[int, Query(ge=1, le=1000)] = 200) -> list[dict]:
        store.freezer(freezer_id)
        after = (since if since.tzinfo else since.replace(tzinfo=UTC)).timestamp() if since else 0.0
        rows = [row for row in world.readings[store.id, freezer_id] if row["at"] > after]
        return [{key: value for key, value in row.items() if key != "at"} for row in rows[:limit]]

    @api.get("/software")
    async def software(store: StoreDep) -> list[dict]:
        return world.software()

    @api.post("/freezers/{freezer_id}/install", status_code=202)
    async def install(store: StoreDep, freezer_id: str, body: InstallBody) -> dict:
        return world.install(store, freezer_id, body.version, clock()).view()

    @api.get("/installs")
    async def installs(store: StoreDep) -> list[dict]:
        return [item.view() for item in store.installs]

    @api.get("/maintenance-requests")
    async def maintenance_requests(
            store: StoreDep, status: Literal["pending", "approved", "rejected", "expired"] | None = None) -> list[dict]:
        return [r.view() for r in store.requests if status is None or r.status == status][-200:]

    @api.post("/maintenance-requests/{request_id}/decision")
    async def decision(store: StoreDep, request_id: str, body: DecisionBody) -> dict:
        return world.decide(store, request_id, body.decision, body.reason, clock()).view()

    @api.get("/technician-pass-key")
    async def technician_pass_key(store: StoreDep) -> dict:
        return world.pass_key(store)

    @api.get("/energy")
    async def energy(store: StoreDep, hours: Annotated[int, Query(ge=1, le=168)] = 24) -> list[dict]:
        return world.energy(store, hours, clock())

    app.include_router(api)

    # ---- site: login, sign-up, map ---------------------------------------------------------

    def role(request: Request) -> str | None:
        return request.session.get("role")

    async def visitor(request: Request) -> str:
        if role(request) is None:
            raise ApiError(401, "login_required", "Log in first.")
        return role(request)

    async def admin(request: Request) -> str:
        if role(request) != "admin":
            raise ApiError(403, "organisers_only", "Log in with the organiser password.")
        return "admin"

    def page(request: Request, name: str):
        if role(request) is None:
            return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
        return FileResponse(STATIC / name)

    @app.get("/static/site.css")  # the only file served without the password; the pages are not
    async def stylesheet() -> FileResponse:
        return FileResponse(STATIC / "site.css")

    @app.get("/login")
    async def login_page() -> FileResponse:
        return FileResponse(STATIC / "login.html")

    @app.post("/login")
    async def login(request: Request, password: Annotated[str, Form()], next: Annotated[str, Form()] = "/"):
        given = password.encode()
        if secrets.compare_digest(given, settings.admin_password.encode()):
            request.session["role"] = "admin"
        elif secrets.compare_digest(given, settings.site_password.encode()):
            request.session["role"] = "visitor"
        else:
            return RedirectResponse("/login?failed=1", status_code=303)
        return RedirectResponse(next if next.startswith("/") and not next.startswith("//") else "/", status_code=303)

    @app.get("/logout")
    async def logout(request: Request) -> RedirectResponse:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/")
    async def signup_page(request: Request):
        return page(request, "signup.html")

    @app.get("/map")
    async def map_page(request: Request):
        return page(request, "map.html")

    site = APIRouter(prefix="/site")

    def endpoint(request: Request, store: Store) -> str:
        base = settings.public_url or str(request.base_url)
        return f"{base.rstrip('/')}/api/v1/stores/{store.id}"

    @site.post("/signup", status_code=201, dependencies=[Depends(visitor)])
    async def signup(request: Request, body: SignupBody) -> dict:
        store = world.signup(body.team, clock())
        world.save(state_path)
        return {"store": store.name, "city": store.city, "team": store.team, "freezers": len(store.freezers),
                "endpoint": endpoint(request, store), "token": store.token}

    @site.get("/team", dependencies=[Depends(visitor)])
    async def team(request: Request, authorization: Annotated[str | None, Header()] = None) -> dict:
        """What a team sees on its own page: where it stands and the briefs it has reached."""
        scheme, _, token = (authorization or "").partition(" ")
        store = world.by_token(token.strip()) if scheme.lower() == "bearer" and token else None
        if store is None:
            raise ApiError(401, "unknown_token", "No store has that token.")
        data = world.overview(store)
        return {key: data[key] for key in ("id", "name", "team", "stage", "stage_label", "detail", "waves",
                                           "warm_freezers", "hottest_c")} | {
            "endpoint": endpoint(request, store), "briefs": world.briefs(store)}

    @site.get("/stores")
    async def stores(request: Request, who: Annotated[str, Depends(visitor)]) -> dict:
        rows = []
        for store in sorted(world.stores.values(), key=lambda s: s.created_at):
            row = world.overview(store, with_token=who == "admin")
            if who == "admin":
                row["endpoint"] = endpoint(request, store)
            rows.append(row)
        return {"role": who, "now": datetime.fromtimestamp(clock(), UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "stores": rows}

    def found(store_id: str) -> Store:
        if store_id not in world.stores:
            raise ApiError(404, "unknown_store", f"No store {store_id}.")
        return world.stores[store_id]

    @site.post("/admin/stores/{store_id}/release-wave", dependencies=[Depends(admin)])
    async def release_wave(store_id: str) -> dict:
        return {"released": world.release_next_wave(found(store_id), clock())}

    @site.post("/admin/stores/{store_id}/force-stage1", dependencies=[Depends(admin)])
    async def force_stage1(store_id: str) -> dict:
        world.force_stage1(found(store_id))
        return {"ok": True}

    @site.post("/admin/stores/{store_id}/stage3-done", dependencies=[Depends(admin)])
    async def stage3_done(store_id: str) -> dict:
        store = found(store_id)
        store.stage3_done_at = None if store.stage3_done_at else clock()  # press again to undo
        return {"done": store.stage3_done_at is not None}

    @site.delete("/admin/stores/{store_id}", dependencies=[Depends(admin)])
    async def delete_store(store_id: str) -> dict:
        found(store_id)
        del world.stores[store_id]
        world.save(state_path)
        return {"ok": True}

    app.include_router(site)
    return app
