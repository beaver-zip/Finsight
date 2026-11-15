from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def test_compose_ports_bind_loopback_only():
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    ports = [(name, p) for name, svc in services.items() for p in svc.get("ports", [])]
    assert ports
    exposed = [(name, p) for name, p in ports if not str(p).startswith("127.0.0.1:")]
    assert exposed == []
