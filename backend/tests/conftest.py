import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("HASTANE_SECRET_KEY", "test-secret-key-test-secret-key-123456")
os.environ["HASTANE_ADMIN_PASSWORD"] = "admin-parola-123"


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    from app.db import reset_engine
    from app.main import create_app
    from app.services import bus
    reset_engine(f"sqlite:///{tmp_path / 'test.db'}")
    bus.unsubscribe_all()
    with TestClient(create_app(start_adapters=False)) as c:
        yield c


@pytest.fixture()
def admin(client):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin-parola-123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
