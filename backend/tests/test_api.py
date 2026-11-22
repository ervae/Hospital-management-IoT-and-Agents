from sqlmodel import Session


def login(client, u, p):
    r = client.post("/api/v1/auth/login", json={"username": u, "password": p})
    return {"Authorization": f"Bearer {r.json()['access_token']}"} if r.status_code == 200 else None


def first_room(client, h):
    return client.get("/api/v1/rooms", headers=h).json()[0]


def test_health_and_seed(client, admin):
    assert client.get("/api/v1/health").json()["status"] == "ok"
    rooms = client.get("/api/v1/rooms", headers=admin).json()
    assert len(rooms) == 8
    assert rooms[0]["type"] == "icu"  # öncelik sırası: yoğun bakım önce
    assert all(r["id"].startswith("room_") for r in rooms)


def test_auth_required_and_no_public_signup(client):
    assert client.get("/api/v1/overview").status_code == 401
    assert client.post("/api/v1/users", json={"username": "x", "password": "12345678"}).status_code == 401
    assert login(client, "admin", "yanlis") is None


def test_admin_creates_users_with_server_ids(client, admin):
    r = client.post("/api/v1/users", headers=admin, json={"username": "eva", "role": "operator"})
    assert r.status_code == 201
    u = r.json()
    assert u["id"].startswith("usr_") and u["temporary_password"]
    eva = login(client, "eva", u["temporary_password"])
    assert eva is not None
    # operatör kullanıcı oluşturamaz
    assert client.post("/api/v1/users", headers=eva, json={"username": "baska"}).status_code == 403
    # kullanıcı istemciden id veremez (alan yok sayılır)
    r = client.post("/api/v1/users", headers=admin, json={"username": "cengiz", "id": "usr_benim"})
    assert r.json()["id"] != "usr_benim"
    # pasifleştirilen kullanıcı giremez
    client.patch(f"/api/v1/users/{u['id']}", headers=admin, json={"active": False})
    assert client.get("/api/v1/auth/me", headers=eva).status_code == 401


def test_admin_cannot_demote_self(client, admin):
    me = client.get("/api/v1/auth/me", headers=admin).json()
    assert client.patch(f"/api/v1/users/{me['id']}", headers=admin, json={"role": "viewer"}).status_code == 400


def test_device_flow(client, admin):
    room = first_room(client, admin)
    d = client.post("/api/v1/devices", headers=admin, json={
        "name": "ESP32-1", "room_id": room["id"],
        "capabilities": {"sensors": ["co2_ppm"], "actuators": ["fan"]}}).json()
    assert d["id"].startswith("dev_") and d["api_key"].startswith("dk_")
    dh = {"X-Device-Key": d["api_key"]}
    assert client.post("/api/v1/devices/me/telemetry", headers={"X-Device-Key": "sahte"},
                       json={"metrics": {"co2_ppm": 1}}).status_code == 401
    r = client.post("/api/v1/devices/me/telemetry", headers=dh,
                    json={"metrics": {"co2_ppm": 1250, "occupancy": 2, "yeni_sensor": 7}})
    assert r.json()["accepted"] == 3
    st = client.get(f"/api/v1/rooms/{room['id']}", headers=admin).json()
    assert st["metrics"]["co2_ppm"] == 1250 and st["metrics"]["yeni_sensor"] == 7
    assert st["status"] == "critical"  # yoğun bakım kritik eşiği 1000
    # operatör/yönetici elle komut -> cihaz çeker -> onaylar
    c = client.post(f"/api/v1/rooms/{room['id']}/commands", headers=admin,
                    json={"actuator": "fan", "value": 90}).json()
    assert c["device_id"] == d["id"]
    pending = client.get("/api/v1/devices/me/commands", headers=dh).json()
    assert [p["id"] for p in pending] == [c["id"]]
    assert client.get("/api/v1/devices/me/commands", headers=dh).json() == []  # bir kez teslim
    ack = client.post(f"/api/v1/devices/me/commands/{c['id']}/ack", headers=dh, json={"status": "done"})
    assert ack.json()["status"] == "done"


def test_agent_flow(client, admin):
    room = first_room(client, admin)
    a = client.post("/api/v1/agents", headers=admin, json={"name": "Oda agent v1", "agent_type": "room"}).json()
    assert a["id"].startswith("agt_")
    ah = {"X-Agent-Key": a["api_key"]}
    state = client.get("/api/v1/agents/me/state", headers=ah).json()
    assert len(state["rooms"]) == 8
    r = client.post("/api/v1/agents/me/decisions", headers=ah, json={
        "summary": "CO2 yüksek, fan açılıyor", "room_id": room["id"], "level": "warning",
        "actions": [{"room_id": room["id"], "actuator": "fan", "value": 80}]})
    assert r.status_code == 201 and len(r.json()["command_ids"]) == 1
    feed = client.get("/api/v1/overview", headers=admin).json()["decisions"]
    assert feed[0]["agent_name"] == "Oda agent v1"
    assert client.get("/api/v1/overview", headers=admin).json()["agents_online"] == 1
    # geçersiz eyleyici reddedilir, karar yarım kaydedilmez
    bad = client.post("/api/v1/agents/me/decisions", headers=ah, json={
        "summary": "x", "actions": [{"room_id": room["id"], "actuator": "klima", "value": 10}]})
    assert bad.status_code == 422
    # durdurulan agent erişemez
    client.patch(f"/api/v1/agents/{a['id']}", headers=admin, json={"active": False})
    assert client.get("/api/v1/agents/me/state", headers=ah).status_code == 401


def test_newer_command_supersedes_pending(client, admin):
    room = first_room(client, admin)
    c1 = client.post(f"/api/v1/rooms/{room['id']}/commands", headers=admin, json={"actuator": "fan", "value": 10}).json()
    client.post(f"/api/v1/rooms/{room['id']}/commands", headers=admin, json={"actuator": "fan", "value": 90})
    cmds = client.get(f"/api/v1/rooms/{room['id']}/commands", headers=admin).json()
    assert next(c for c in cmds if c["id"] == c1["id"])["status"] == "rejected"


def test_viewer_cannot_command(client, admin):
    u = client.post("/api/v1/users", headers=admin, json={"username": "izleyici"}).json()
    vh = login(client, "izleyici", u["temporary_password"])
    room = first_room(client, admin)
    assert client.post(f"/api/v1/rooms/{room['id']}/commands", headers=vh,
                       json={"actuator": "fan", "value": 50}).status_code == 403


def test_simulator_and_rule_agents(client, admin):
    from app.adapters.rule_agent import RuleAgents
    from app.adapters.simulator import Simulator
    from app.db import get_engine
    sim, agents = Simulator(), RuleAgents()
    with Session(get_engine()) as s:
        for _ in range(5):
            sim.tick(s)
            agents.tick(s)
    ov = client.get("/api/v1/overview", headers=admin).json()
    assert all(r["status"] != "offline" for r in ov["rooms"])
    assert ov["energy"]["battery_pct"] is not None
    assert ov["decisions"], "temel agent karar üretmeli"


def test_physical_device_takes_over_room_from_simulator(client, admin):
    from app.adapters.simulator import Simulator
    from app.db import get_engine
    room = first_room(client, admin)
    client.post("/api/v1/devices", headers=admin, json={"name": "ESP32", "room_id": room["id"]})
    sim = Simulator()
    with Session(get_engine()) as s:
        sim.tick(s)
    assert room["id"] not in sim.rooms and len(sim.rooms) == 7
