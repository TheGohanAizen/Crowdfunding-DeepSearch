import json
import os
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = os.environ.copy()
env["HOST"] = "127.0.0.1"
env["PORT"] = "8099"
env.pop("GOOGLE_CSE_API_KEY", None)
env.pop("GOOGLE_CSE_ID", None)

process = subprocess.Popen(
    [sys.executable, os.path.join(ROOT, "backend", "server.py")],
    env=env,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

def read_json(request):
    with urlopen(request, timeout=3) as response:
        return response.status, json.loads(response.read().decode("utf-8"))

try:
    last_error = None
    for _ in range(30):
        try:
            status, health = read_json("http://127.0.0.1:8099/api/health")
            assert status == 200
            assert health["status"] == "ok"
            assert health["service"] == "Crowdfunding DeepSearch Backend"
            break
        except Exception as error:
            last_error = error
            time.sleep(0.25)
    else:
        raise RuntimeError("Backend did not become healthy: " + str(last_error))

    payload = json.dumps({
        "need": "Transportation",
        "location": "Austin, Texas, United States",
        "goal": 10000
    }).encode("utf-8")
    request = Request(
        "http://127.0.0.1:8099/api/discover",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    status, discovery = read_json(request)
    assert status == 200
    assert discovery["status"] == "success"
    assert discovery["query"]["need"] == "Transportation"
    assert discovery["provider_status"]["configured"] is False
    assert isinstance(discovery["search_plan"], list)
    assert len(discovery["search_plan"]) >= 1
    assert discovery["results"] == []
    assert discovery["discovery"]["verification_enabled"] is True
    assert discovery["verification_policy"]["eligibility_claims"] is False
    assert discovery["verification_policy"]["automatic_official_source_claims"] is False

    bad = Request(
        "http://127.0.0.1:8099/api/discover",
        data=b"not-json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urlopen(bad, timeout=3)
        raise AssertionError("Invalid JSON should return HTTP 400")
    except HTTPError as error:
        assert error.code == 400

    bad_goal = Request(
        "http://127.0.0.1:8099/api/discover",
        data=json.dumps({"need": "Transportation", "location": "Austin", "goal": "not-a-number"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urlopen(bad_goal, timeout=3)
        raise AssertionError("Invalid goal should return HTTP 400")
    except HTTPError as error:
        assert error.code == 400

    oversized = Request(
        "http://127.0.0.1:8099/api/discover",
        data=json.dumps({"need": "x" * 501, "location": "Austin", "goal": 10000}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urlopen(oversized, timeout=3)
        raise AssertionError("Oversized search field should return HTTP 400")
    except HTTPError as error:
        assert error.code == 400

    negative_goal = Request(
        "http://127.0.0.1:8099/api/discover",
        data=json.dumps({"need": "Transportation", "location": "Austin", "goal": -1}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urlopen(negative_goal, timeout=3)
        raise AssertionError("Negative goal should return HTTP 400")
    except HTTPError as error:
        assert error.code == 400

    print("Integration smoke test passed: health, discovery fallback, and invalid input handling.")
finally:
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
