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
env.pop("TAVILY_API_KEY", None)
env.pop("BRAVE_SEARCH_API_KEY", None)
env["SEARCH_PROVIDER"] = "auto"

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
        "goal": 10000,
        "scope": "automatic"
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
    assert discovery["query"]["scope"] == "automatic"
    assert discovery["discovery"]["geographic_scope"] == "automatic"
    assert [item["geographic_stage"] for item in discovery["search_plan"]] == ["local", "state", "national", "worldwide"]
    assert discovery["provider_status"]["configured"] is False
    assert isinstance(discovery["search_plan"], list)
    assert len(discovery["search_plan"]) >= 1
    assert discovery["results"] == []
    assert discovery["discovery"]["verification_enabled"] is True
    assert discovery["verification_policy"]["eligibility_claims"] is False
    assert discovery["verification_policy"]["automatic_official_source_claims"] is False


    international_payload = json.dumps({
        "need": "Transportation",
        "location": "Toronto, Ontario, Canada",
        "goal": 10000,
        "scope": "national"
    }).encode("utf-8")
    international_request = Request(
        "http://127.0.0.1:8099/api/discover",
        data=international_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    status, international = read_json(international_request)
    assert status == 200
    assert international["query"]["scope"] == "national"
    assert international["discovery"]["geographic_scope"] == "national"
    assert all(item["geographic_stage"] == "national" for item in international["search_plan"])
    assert all("Canada national nationwide serves applicants" in item["query"] for item in international["search_plan"])
    assert all("Toronto" not in item["query"] for item in international["search_plan"])
    assert all("Ontario" not in item["query"] for item in international["search_plan"])
    assert all("United States national" not in item["query"] for item in international["search_plan"])

    invalid_scope = Request(
        "http://127.0.0.1:8099/api/discover",
        data=json.dumps({"need": "Transportation", "location": "Austin", "goal": 10000, "scope": "galaxy"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urlopen(invalid_scope, timeout=3)
        raise AssertionError("Unsupported scope should return HTTP 400")
    except HTTPError as error:
        assert error.code == 400

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

    # Static, no-credit source-page checks for service-area language.
    import importlib.util
    server_path = os.path.join(ROOT, "backend", "server.py")
    spec = importlib.util.spec_from_file_location("crowdfunding_server", server_path)
    crowdfunding_server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(crowdfunding_server)
    extract_page_signals = crowdfunding_server.extract_page_signals
    parse_location_parts = crowdfunding_server.parse_location_parts
    build_discovery_queries = crowdfunding_server.build_discovery_queries

    parsed = parse_location_parts("Austin, Texas, United States")
    assert parsed == {"raw": "Austin, Texas, United States", "city": "Austin", "region": "Texas", "country": "United States"}
    state_queries = build_discovery_queries("Transportation", "Austin, Texas, United States", "state")
    assert all("Texas, United States statewide" in item["query"] for item in state_queries)
    assert all("Austin" not in item["query"] for item in state_queries)
    national_queries = build_discovery_queries("Transportation", "Austin, Texas, United States", "national")
    assert all("United States national nationwide serves applicants" in item["query"] for item in national_queries)
    assert all("Austin" not in item["query"] and "Texas" not in item["query"] for item in national_queries)

    statewide_html = """
    <html><head><title>Transportation Help</title></head>
    <body>
      <h1>Transportation Assistance Program</h1>
      <p>Our statewide program is available statewide for residents who qualify.</p>
      <a href="/apply">Apply now</a>
    </body></html>
    """
    statewide = extract_page_signals(statewide_html, "https://example.org/help")
    assert statewide["service_area_language_found"] is True
    assert "available statewide" in statewide["service_area_evidence"]
    assert statewide["application_route_found"] is True

    neutral_html = """
    <html><body>
      <h1>Transportation Assistance Program</h1>
      <p>Read about our assistance options and eligibility requirements.</p>
      <a href="/contact">Contact us</a>
    </body></html>
    """
    neutral = extract_page_signals(neutral_html, "https://example.org/help")
    assert neutral["service_area_language_found"] is False
    assert neutral["service_area_evidence"] == []

    print("Integration smoke test passed: health, geographic expansion, discovery fallback, service-area evidence, and invalid input handling.")
finally:
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
