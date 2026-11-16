"""The hosted side end to end over HTTP, with a clock the test moves."""
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from freezerhost.app import create_app
from freezerhost.config import Settings
from freezerhost.world import WAVES

START = float(int(time.time()))


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now


@pytest.fixture
def host(tmp_path):
    clock = Clock()
    settings = Settings(_env_file=None, data_dir=tmp_path, site_password="site", admin_password="boss",
                        download_seconds=4, install_seconds=2, offline_return_seconds=30,
                        attack_delay_seconds=10, wave_interval_seconds=20, attack_retry_seconds=5,
                        legit_interval_seconds=5)
    app = create_app(settings, clock, background=False)
    client = TestClient(app)

    def run(seconds):
        for _ in range(int(seconds)):
            clock.now += 1
            app.state.world.tick(clock.now)

    client.run, client.world, client.settings = run, app.state.world, settings
    return client


def join(host, team="Team A"):
    host.post("/login", data={"password": "site"})
    created = host.post("/site/signup", json={"team": team}).json()
    store_id = created["endpoint"].rsplit("/", 1)[1]
    return f"/api/v1/stores/{store_id}", {"Authorization": f"Bearer {created['token']}"}, store_id


def roll_out(host, base, auth):
    """A patient client: tell every freezer to install until all run 2.0.0."""
    for _ in range(40):
        freezers = host.get(f"{base}/freezers", headers=auth).json()
        if all(f["software_version"] == "2.0.0" for f in freezers):
            return
        for freezer in freezers:
            if freezer["software_version"] != "2.0.0":
                host.post(f"{base}/freezers/{freezer['id']}/install", json={"version": "2.0.0"}, headers=auth)
        host.run(5)
    raise AssertionError("rollout did not finish")


def test_site_needs_the_password_and_gives_each_team_its_own_store(host):
    assert host.get("/", follow_redirects=False).status_code == 303
    assert host.post("/site/signup", json={"team": "x"}).status_code == 401
    base_a, auth_a, id_a = join(host, "Team A")
    base_b, auth_b, id_b = join(host, "Team B")
    assert id_a != id_b and id_a.startswith("ica-")
    assert host.post("/site/signup", json={"team": "team a"}).json()["detail"]["code"] == "team_taken"
    assert host.get(base_a, headers=auth_a).json()["freezer_count"] == 10
    assert host.get(f"{base_a}/", headers=auth_a).status_code == 200
    assert host.get(base_a).status_code == 401
    assert host.get(base_a, headers=auth_b).status_code == 403
    listing = host.get("/site/stores").json()
    assert listing["role"] == "visitor" and "token" not in listing["stores"][0]
    assert [w["name"] for w in listing["stores"][0]["waves"]] == ["Wave 1", "Wave 2", "Wave 3", "Wave 4"]
    assert host.post(f"/site/admin/stores/{id_a}/release-wave").status_code == 403


def test_cities_are_used_once(host):
    host.post("/login", data={"password": "site"})
    names = [host.post("/site/signup", json={"team": f"Team {n}"}).json() for n in range(51)]
    assert len({n["store"] for n in names[:50]}) == 50
    assert names[50]["detail"]["code"] == "no_city_left"


def test_install_obstacles_and_stage_one(host):
    base, auth, store_id = join(host)
    host.run(1)

    def install(freezer):
        return host.post(f"{base}/freezers/{freezer}/install", json={"version": "2.0.0"}, headers=auth)

    assert host.post(f"{base}/freezers/frys-01/install", json={"version": "9"}, headers=auth).status_code == 404
    assert install("frys-07").json()["detail"]["code"] == "offline"
    assert install("frys-05").json()["detail"]["code"] == "busy"       # started a defrost just now
    assert install("frys-09").json()["detail"]["code"] == "busy"       # door open
    assert install("frys-01").status_code == 202
    assert install("frys-01").json()["detail"]["code"] == "install_in_progress"
    assert install("frys-03").status_code == 202
    host.run(7)
    freezers = {f["id"]: f for f in host.get(f"{base}/freezers", headers=auth).json()}
    assert freezers["frys-01"]["software_version"] == "2.0.0" and freezers["frys-01"]["install"]["state"] == "installed"
    assert freezers["frys-03"]["install"] | {"finished_at": 0, "started_at": 0, "id": 0} == {
        "id": 0, "freezer_id": "frys-03", "version": "2.0.0", "state": "failed", "progress_pct": 60,
        "error": "checksum_mismatch", "started_at": 0, "finished_at": 0}
    assert install("frys-01").json()["detail"]["code"] == "already_installed"
    assert len(host.get(f"{base}/freezers/frys-01/readings", headers=auth).json()) >= 1

    roll_out(host, base, auth)
    host.run(1)
    overview = host.get("/site/stores").json()["stores"][0]
    assert (overview["stage"], overview["freezers_updated"]) == (2, 10)
    assert len(host.get(f"{base}/installs", headers=auth).json()) >= 11  # frys-03 needed two


def careful(key, request, skip=None):
    """A correct approver; `skip` leaves out one check, like the starting code of the teams does."""
    token = request["pass"]
    try:
        if skip == "signature" and jwt.get_unverified_header(token)["alg"] == "none":
            jwt.decode(token, options={"verify_signature": False})
        else:
            jwt.decode(token, key["public_key_pem"], algorithms=[key["algorithm"]], issuer=key["issuer"],
                       audience=key["audience"], leeway=3600,  # the test clock runs ahead of the real one
                       options={"verify_exp": skip != "expiry", "verify_aud": skip != "audience"})
    except jwt.InvalidTokenError:
        return "reject"
    low, high, name = ((-30, -12, "setpoint_c") if request["action"] == "set_setpoint"
                       else (5, 60, "duration_minutes"))
    return "approve" if skip == "limits" or low <= request["params"][name] <= high else "reject"


def answer(host, base, auth, seconds, skip=None):
    key = host.get(f"{base}/technician-pass-key", headers=auth).json()
    for _ in range(int(seconds)):
        host.run(1)
        for request in host.get(f"{base}/maintenance-requests?status=pending", headers=auth).json():
            host.post(f"{base}/maintenance-requests/{request['id']}/decision",
                      json={"decision": careful(key, request, skip)}, headers=auth)


def test_correct_approver_repels_all_waves_and_reaches_stage_three(host):
    base, auth, store_id = join(host)
    roll_out(host, base, auth)
    answer(host, base, auth, 100)
    overview = host.get("/site/stores").json()["stores"][0]
    assert [w["state"] for w in overview["waves"]] == ["repelled"] * 4
    assert (overview["stage"], overview["legit_rejected"], overview["warm_freezers"]) == (3, 0, 0)
    assert overview["detail"] == "Energy data not used yet"
    rows = host.get(f"{base}/energy?hours=2", headers=auth).json()
    assert len(rows) == 20 and rows[0].keys() == {"freezer_id", "hour", "energy_kwh", "door_openings",
                                                   "avg_temperature_c"}
    host.post("/login", data={"password": "boss"})
    assert host.get("/site/stores").json()["stores"][0]["detail"] == "Energy data in use"
    assert host.post(f"/site/admin/stores/{store_id}/stage3-done").json() == {"done": True}
    assert host.get("/site/stores").json()["stores"][0]["stage"] == 4


@pytest.mark.parametrize("wave,skip", list(enumerate(["signature", "expiry", "audience", "limits"])))
def test_each_flaw_lets_exactly_its_own_wave_through(host, wave, skip):
    base, auth, _ = join(host)
    roll_out(host, base, auth)
    answer(host, base, auth, 100, skip)
    world_store = next(iter(host.world.stores.values()))
    states = [w.state for w in world_store.waves]
    assert states == ["breached" if n == wave else "repelled" for n in range(len(WAVES))]
    assert world_store.stage2_done_at is None
    assert any(f.breached_by == wave for f in world_store.freezers)
    answer(host, base, auth, 60)                                     # the flaw is fixed
    assert [w.state for w in world_store.waves] == ["repelled"] * 4
    answer(host, base, auth, 200)
    assert world_store.stage2_done_at and max(f.temperature_c for f in world_store.freezers) < 0


def test_undecided_requests_expire_and_no_running_service_is_not_a_defence(host):
    base, auth, store_id = join(host)
    roll_out(host, base, auth)
    host.run(150)
    requests = host.get(f"{base}/maintenance-requests", headers=auth).json()
    assert {"expired", "pending"} == {r["status"] for r in requests}
    expired = next(r for r in requests if r["status"] == "expired")
    late = host.post(f"{base}/maintenance-requests/{expired['id']}/decision", json={"decision": "reject"},
                     headers=auth)
    assert late.json()["detail"]["code"] == "already_decided"
    overview = host.get("/site/stores").json()["stores"][0]
    assert overview["stage"] == 2 and "repelled" not in [w["state"] for w in overview["waves"]]


def test_organiser_controls_and_restart(host, tmp_path):
    base, auth, store_id = join(host)
    host.post("/login", data={"password": "boss"})
    admin = f"/site/admin/stores/{store_id}"
    assert host.post(f"{admin}/release-wave").json()["detail"]["code"] == "stage1_not_done"
    assert host.post(f"{admin}/force-stage1").status_code == 200
    host.run(2)
    assert host.post(f"{admin}/release-wave").json() == {"released": 1}
    listing = host.get("/site/stores").json()["stores"][0]
    assert listing["stage"] == 2 and listing["token"] and listing["endpoint"].endswith(store_id)

    host.world.save(tmp_path / "state.json")
    again = TestClient(create_app(host.settings, lambda: START, background=False))
    assert again.get(base, headers=auth).json()["id"] == store_id

    assert host.delete(admin).status_code == 200
    assert host.get(base, headers=auth).status_code == 401


def test_a_breach_thaws_the_freezer_and_shows_on_the_map(host):
    base, auth, _ = join(host)
    roll_out(host, base, auth)
    host.settings.legit_interval_seconds = 10_000   # nobody genuine resets the setpoint meanwhile
    for _ in range(120):                            # the starting code of the teams: approves everything
        host.run(1)
        for request in host.get(f"{base}/maintenance-requests?status=pending", headers=auth).json():
            host.post(f"{base}/maintenance-requests/{request['id']}/decision", json={"decision": "approve"},
                      headers=auth)
    overview = host.get("/site/stores").json()["stores"][0]
    assert overview["warm_freezers"] >= 1 and overview["hottest_c"] > 40
    assert overview["waves"][0]["state"] == "breached"


def test_team_page_gets_each_brief_when_its_stage_is_reached(host):
    base, auth, store_id = join(host)

    def stages():
        page = host.get("/site/team", headers=auth).json()
        assert page["id"] == store_id and page["endpoint"].endswith(store_id) and "token" not in page
        return [brief["stage"] for brief in page["briefs"]]

    assert host.get("/site/team", headers={"Authorization": "Bearer nope"}).json()["detail"]["code"] == "unknown_token"
    assert stages() == [1]
    roll_out(host, base, auth)
    host.run(5)
    assert stages() == [1]            # all updated, the attack has not started
    host.run(10)
    assert stages() == [2, 1]         # first wave released
    answer(host, base, auth, 100)
    assert stages() == [3, 2, 1]
    newest = host.get("/site/team", headers=auth).json()["briefs"][0]
    assert newest["title"] == "Stage 3: an energy and doors screen" and "Hand this" not in newest["markdown"]
