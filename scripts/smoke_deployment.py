"""Exercise a deployed demo without an API key or paid model calls."""

import argparse
import json
import urllib.request


def check(base_url: str) -> None:
    def request(path: str, payload: dict | None = None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            base_url.rstrip("/") + path,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    health = request("/api/health")
    assert health["status"] == "ok", "Web health failed"
    assert health["database"] == "postgresql", "Expected PostgreSQL"
    assert health["external_risk_mcp"]["state"] == "connected", "MCP is not connected"
    assert health["document_chunks"] > 0, "Documents are not seeded"
    report = request("/api/demo", {"user_email": "noah.east@controltower.demo"})
    assert "SS-CRITICAL-001" in report["output"]["answer"], "Expected seeded scenario evidence"
    assert report["output"]["citations"], "No citations returned"
    events = report["execution_trace"]
    assert events[-1]["node"] == "request" and events[-1]["status"] == "completed"
    assert not any(event["status"] == "failed" for event in events), "Trace contains failures"
    print("Deployment verified: PostgreSQL, seeded documents, MCP, offline answer and trace.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    check(parser.parse_args().base_url)
