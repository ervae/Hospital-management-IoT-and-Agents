from sqlmodel import Session


def login(client, u, p):
    r = client.post("/api/v1/auth/login", json={"username": u, "password": p})
    return {"Authorization": f"Bearer {r.json()['access_token']}"} if r.status_code == 200 else None


def first_room(client, h):
    return client.get("/api/v1/rooms", headers=h).json()[0]


def test_health_and_seed(client, admin):
    assert client.get("/api/v1/health").json()["status"] == "ok"
    rooms = client.get("/api/v1/rooms", headers=admin).json()
    assert len(rooms) == 24
    assert {r["ward"] for r in rooms} == {"Dahiliye", "Genel Cerrahi", "Kardiyoloji", "Nöroloji"}
    assert all(r["id"].startswith("room_") for r in rooms)
    assert all(r["state"] == "unknown" and not r["online"] for r in rooms)  # henüz veri yok
    assert sum(r["isolation"] for r in rooms) == 4


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


def room_by(client, h, ward="Dahiliye", **kw):
    for r in client.get("/api/v1/rooms", headers=h).json():
        if r["ward"] == ward and all(r[k] == v for k, v in kw.items()):
            return r
    raise AssertionError("oda yok")


def send(client, dh, **m):
    r = client.post("/api/v1/devices/me/telemetry", headers=dh, json={"metrics": m})
    assert r.status_code == 202, r.text
    return r


def test_device_flow_and_state_inference(client, admin):
    room = room_by(client, admin, sex="any", isolation=False)
    d = client.post("/api/v1/devices", headers=admin, json={
        "name": "ESP32-1", "room_id": room["id"],
        "capabilities": {"sensors": ["bed_load_kg", "motion", "door_events"], "actuators": ["led"],
                         "time_scale": 600}}).json()
    assert d["id"].startswith("dev_") and d["api_key"].startswith("dk_")
    dh = {"X-Device-Key": d["api_key"]}
    assert client.post("/api/v1/devices/me/telemetry", headers={"X-Device-Key": "sahte"},
                       json={"metrics": {"motion": 1}}).status_code == 401
    r = send(client, dh, bed_load_kg=72.5, motion=0, yeni_sensor=7)
    assert r.json()["accepted"] == 3
    get = lambda: client.get(f"/api/v1/rooms/{room['id']}", headers=admin).json()  # noqa: E731
    st = get()
    assert st["metrics"]["yeni_sensor"] == 7 and st["online"]
    assert st["state"] == "occupied" and st["state_source"] == "inference"
    send(client, dh, bed_load_kg=0.4, door_events=1)
    assert get()["state"] == "vacated"
    import time
    send(client, dh, bed_load_kg=0.3, motion=4)
    time.sleep(0.3)                      # time_scale=600: 120 sn -> 0,2 sn
    send(client, dh, bed_load_kg=0.3, motion=5)
    assert get()["state"] == "cleaning"
    time.sleep(1.1)                      # 600 sn sessizlik -> 1 sn
    send(client, dh, bed_load_kg=0.2, motion=0)
    st = get()
    assert st["state"] == "ready"
    assert st["hidden_free"]             # HBYS'de hâlâ hazır değil
    client.post(f"/api/v1/rooms/{room['id']}/his-state", headers=admin, json={"state": "ready"})
    assert not get()["hidden_free"]
    tr = client.get(f"/api/v1/rooms/{room['id']}/transitions?channel=iot", headers=admin).json()
    assert [t["to_state"] for t in tr] == ["occupied", "vacated", "cleaning", "ready"]
    # etiket (laboratuvar) kaydı
    assert client.post(f"/api/v1/rooms/{room['id']}/labels", headers=admin,
                       json={"state": "ready", "note": "temizlikçi çıktı"}).status_code == 201
    assert len(client.get(f"/api/v1/rooms/{room['id']}/labels", headers=admin).json()) == 1
    # dış sınıflandırıcı durumu doğrudan bildirebilir
    client.post("/api/v1/devices/me/state", headers=dh, json={"state": "cleaning", "confidence": 0.7})
    assert get()["state_source"] == "external"
    # elle komut (kapı LED'i) -> cihaz çeker -> onaylar
    c = client.post(f"/api/v1/rooms/{room['id']}/commands", headers=admin,
                    json={"actuator": "led", "value": 1}).json()
    assert c["device_id"] == d["id"]
    pending = client.get("/api/v1/devices/me/commands", headers=dh).json()
    assert [p["id"] for p in pending] == [c["id"]]
    assert client.get("/api/v1/devices/me/commands", headers=dh).json() == []  # bir kez teslim
    ack = client.post(f"/api/v1/devices/me/commands/{c['id']}/ack", headers=dh, json={"status": "done"})
    assert ack.json()["status"] == "done"


def make_ready(client, admin, room):
    client.post(f"/api/v1/rooms/{room['id']}/state", headers=admin, json={"state": "ready"})


def test_request_recommendation_and_constraints(client, admin):
    a = client.post("/api/v1/agents", headers=admin, json={"name": "Koordinatör v1", "agent_type": "coordinator"}).json()
    assert a["id"].startswith("agt_")
    ah = {"X-Agent-Key": a["api_key"]}
    ok_room = room_by(client, admin, sex="any", isolation=False)
    woman_room = room_by(client, admin, sex="F")
    other_ward = room_by(client, admin, ward="Kardiyoloji", sex="any", isolation=False)
    for r in (ok_room, woman_room, other_ward):
        make_ready(client, admin, r)
    req = client.post("/api/v1/agents/me/requests", headers=ah, json={
        "patient_ref": "H-1", "ward": "Dahiliye", "sex": "M", "urgency": 2}).json()
    assert req["id"].startswith("req_") and req["status"] == "waiting"
    state = client.get("/api/v1/agents/me/state", headers=ah).json()
    assert len(state["rooms"]) == 24 and state["requests"][0]["id"] == req["id"]
    # hepsi ihlalli -> blocked
    bad = client.post("/api/v1/agents/me/recommendations", headers=ah, json={
        "request_id": req["id"], "candidates": [{"room_id": woman_room["id"]}, {"room_id": other_ward["id"]}]}).json()
    assert bad["status"] == "blocked"
    assert any("Cinsiyet" in v for v in bad["candidates"][0]["violations"])
    assert any("Branş" in v for v in bad["candidates"][1]["violations"])
    # geçerli aday var -> pending
    rec = client.post("/api/v1/agents/me/recommendations", headers=ah, json={
        "request_id": req["id"], "rationale": "Dahiliye'de hazır oda",
        "candidates": [{"room_id": woman_room["id"]}, {"room_id": ok_room["id"], "reason": "hazır"}]}).json()
    assert rec["status"] == "pending" and rec["agent_name"] == "Koordinatör v1"
    # ihlalli aday onaylanamaz
    assert client.post(f"/api/v1/recommendations/{rec['id']}/approve", headers=admin,
                       json={"room_id": woman_room["id"]}).status_code == 409
    # izleyici onaylayamaz
    u = client.post("/api/v1/users", headers=admin, json={"username": "izleyici"}).json()
    vh = login(client, "izleyici", u["temporary_password"])
    assert client.post(f"/api/v1/recommendations/{rec['id']}/approve", headers=vh, json={}).status_code == 403
    ok = client.post(f"/api/v1/recommendations/{rec['id']}/approve", headers=admin, json={"note": "uygun"}).json()
    assert ok["status"] == "approved" and ok["chosen_room_id"] == ok_room["id"]
    room = client.get(f"/api/v1/rooms/{ok_room['id']}", headers=admin).json()
    assert room["reserved_for"] == req["id"]
    ov = client.get("/api/v1/overview", headers=admin).json()
    assert ov["kpis"]["ed_waiting"] == 0 and ov["requests"] == []
    # ayrılmış yatak başka talebe önerilemez
    req2 = client.post("/api/v1/requests", headers=admin, json={"ward": "Dahiliye", "sex": "M"}).json()
    r2 = client.post("/api/v1/agents/me/recommendations", headers=ah, json={
        "request_id": req2["id"], "candidates": [{"room_id": ok_room["id"]}]}).json()
    assert r2["status"] == "blocked"
    audit = [x["action"] for x in client.get("/api/v1/audit", headers=admin).json()]
    assert "recommendation.approve" in audit


def test_agent_decision_log(client, admin):
    a = client.post("/api/v1/agents", headers=admin, json={"name": "Acil ajanı", "agent_type": "ed"}).json()
    ah = {"X-Agent-Key": a["api_key"]}
    assert client.post("/api/v1/agents/me/decisions", headers=ah, json={"summary": "2 hasta yatış bekliyor"}).status_code == 201
    ov = client.get("/api/v1/overview", headers=admin).json()
    assert ov["decisions"][0]["agent_name"] == "Acil ajanı" and ov["agents_online"] == 1
    client.patch(f"/api/v1/agents/{a['id']}", headers=admin, json={"active": False})
    assert client.get("/api/v1/agents/me/state", headers=ah).status_code == 401


def test_viewer_cannot_command(client, admin):
    u = client.post("/api/v1/users", headers=admin, json={"username": "izleyici"}).json()
    vh = login(client, "izleyici", u["temporary_password"])
    room = room_by(client, admin)
    assert client.post(f"/api/v1/rooms/{room['id']}/commands", headers=vh,
                       json={"actuator": "led", "value": 1}).status_code == 403
    assert client.post(f"/api/v1/rooms/{room['id']}/state", headers=vh, json={"state": "ready"}).status_code == 403


def test_simulator_and_baseline_agent(client, admin, monkeypatch):
    from app.adapters.auto_approve import AutoApprove
    from app.adapters.rule_agent import RuleAgents
    from app.adapters.simulator import Simulator
    from app.config import settings
    from app.db import get_engine
    monkeypatch.setattr(settings, "sim_ed_rate", 20.0)   # bol talep
    monkeypatch.setattr(settings, "auto_approve_s", 0.0)
    sim, agent, auto = Simulator(seed=7), RuleAgents(), AutoApprove()
    with Session(get_engine()) as s:
        for _ in range(60):
            sim.tick(s)
            agent.tick(s)
            auto.tick(s)
    ov = client.get("/api/v1/overview", headers=admin).json()
    assert all(r["online"] for r in ov["rooms"])
    assert {r["state"] for r in ov["rooms"]} - {"unknown"}
    assert ov["kpis"]["sim_clock"].startswith("Gün")
    assert ov["recommendations"], "temel ajan öneri üretmeli"
    statuses = {r["status"] for r in client.get("/api/v1/recommendations", headers=admin).json()}
    assert "approved" in statuses
    labels = client.get(f"/api/v1/rooms/{ov['rooms'][0]['id']}/labels", headers=admin).json()
    assert labels and labels[0]["labeled_by"] == "simulator"


def test_physical_device_takes_over_room_from_simulator(client, admin):
    from app.adapters.simulator import Simulator
    from app.db import get_engine
    room = room_by(client, admin)
    client.post("/api/v1/devices", headers=admin, json={"name": "ESP32", "room_id": room["id"]})
    sim = Simulator()
    with Session(get_engine()) as s:
        sim.tick(s)
    assert room["id"] not in sim.rooms and len(sim.rooms) == 23


def test_state_machine_unit():
    from datetime import datetime, timedelta, timezone

    from app.models import BedState
    from app.services import room_state as rs
    p = rs.params({})
    t0 = datetime(2026, 12, 1, 10, tzinfo=timezone.utc)
    seq = [(0, {"bed_load_kg": 70}), (60, {"bed_load_kg": 0, "door_events": 1}),
           (300, {"bed_load_kg": 0, "motion": 3}), (480, {"bed_load_kg": 0, "motion": 4}),
           (900, {"bed_load_kg": 0, "motion": 0}), (1100, {"bed_load_kg": 0, "motion": 0})]
    state, ctx, out = BedState.unknown, {}, []
    for sec, m in seq:
        res = rs.step(state, ctx, m, t0 + timedelta(seconds=sec), p)
        state, ctx = res.state, res.ctx
        out.append(state.value)
    assert out == ["occupied", "vacated", "vacated", "cleaning", "cleaning", "ready"]
