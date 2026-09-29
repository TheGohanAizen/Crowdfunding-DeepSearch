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


    audience_payload = json.dumps({
        "need": "Transportation",
        "location": "Austin, Texas, United States",
        "scope": "automatic"
    }).encode("utf-8")
    audience_request = Request(
        "http://127.0.0.1:8099/api/audience/plan",
        data=audience_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    status, audience = read_json(audience_request)
    assert status == 200
    assert audience["status"] == "success"
    assert audience["audience"]["stage"] == "audience-planning-v1"
    assert audience["audience"]["live_search"] is False
    assert audience["audience"]["credits_used"] == 0
    assert audience["audience"]["automatic_distribution"] is False
    assert audience["safety_policy"]["review_required"] is True
    assert audience["safety_policy"]["automatic_posting"] is False
    assert [item["geographic_stage"] for item in audience["search_plan"]] == ["local", "state", "national", "worldwide"]

    audience_preview_request = Request(
        "http://127.0.0.1:8099/api/audience/preview",
        data=json.dumps({
            "need": "Transportation",
            "location": "Austin, Texas, United States",
            "scope": "automatic",
            "candidates": [{
                "lane": "Community Forums",
                "title": "Austin Transportation Community",
                "url": "https://example.org/austin-transportation",
                "snippet": "Austin vehicle mobility community resources"
            }]
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    status, audience_preview = read_json(audience_preview_request)
    assert status == 200
    assert audience_preview["audience"]["stage"] == "audience-normalization-v1"
    assert audience_preview["audience"]["credits_used"] == 0
    assert len(audience_preview["results"]) == 1
    assert audience_preview["results"][0]["tracking_id"].startswith("audience_")
    assert audience_preview["results"][0]["permission_verified"] is False
    assert audience_preview["results"][0]["automatic_distribution"] is False

    audience_discovery_request = Request(
        "http://127.0.0.1:8099/api/audience/discover",
        data=json.dumps({
            "need": "Transportation",
            "location": "Austin, Texas, United States",
            "scope": "automatic"
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    status, audience_discovery = read_json(audience_discovery_request)
    assert status == 200
    assert audience_discovery["audience"]["stage"] == "audience-discovery-v1"
    assert audience_discovery["audience"]["live_search"] is False
    assert audience_discovery["provider_status"]["configured"] is False
    assert audience_discovery["results"] == []
    assert audience_discovery["safety_policy"]["automatic_posting"] is False

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

    # Static, no-credit UI checks. Literal backslash-n sequences previously broke
    # CSS/JavaScript even though the Python backend smoke test still passed.
    live_discovery_path = os.path.join(ROOT, "backend", "static", "live-discovery.html")
    with open(live_discovery_path, "r", encoding="utf-8") as handle:
        live_discovery_html = handle.read()
    assert "\\n" not in live_discovery_html
    assert 'id="resultFilter"' in live_discovery_html
    assert 'value="confirmed_area"' in live_discovery_html
    assert 'value="possible_area"' in live_discovery_html
    assert 'card.dataset.serviceArea' in live_discovery_html
    assert 'id="outreachDashboard"' in live_discovery_html
    assert 'id="dashFollowups"' in live_discovery_html
    assert 'value="followups_due"' in live_discovery_html
    assert 'data-followup="tomorrow"' in live_discovery_html
    assert 'data-followup="week"' in live_discovery_html
    assert 'data-notes="edit"' in live_discovery_html
    assert 'function updateOutreachDashboard()' in live_discovery_html
    assert 'id="audiencePlanButton"' in live_discovery_html
    assert 'id="audienceResults"' in live_discovery_html
    assert 'AUDIENCE_PLAN_URL' in live_discovery_html
    assert 'function runAudiencePlan()' in live_discovery_html
    assert 'No automatic posting' in live_discovery_html
    assert 'id="audienceDiscoverButton"' in live_discovery_html
    assert 'AUDIENCE_DISCOVER_URL' in live_discovery_html
    assert 'function runAudienceDiscovery()' in live_discovery_html
    assert 'function renderAudienceLead(item)' in live_discovery_html
    assert 'Automatic distribution:</strong> Disabled.' in live_discovery_html
    assert 'Channel rules:</strong>' in live_discovery_html
    assert 'data-audience-track="saved"' in live_discovery_html
    assert 'data-audience-track="reviewed"' in live_discovery_html
    assert 'data-audience-track="contacted"' in live_discovery_html
    assert 'audience-tracking-state' in live_discovery_html
    assert 'Recommended next action:</strong>' in live_discovery_html
    assert 'id="audienceDashboard"' in live_discovery_html
    assert 'id="audienceRestrictionCount"' in live_discovery_html
    assert 'function updateAudienceDashboard(results)' in live_discovery_html
    assert 'function updateAudienceTrackingDashboard()' in live_discovery_html
    assert 'id="audienceSavedCount"' in live_discovery_html
    assert 'id="audienceReviewedCount"' in live_discovery_html
    assert 'id="audienceContactedCount"' in live_discovery_html
    assert 'TRACKING_PREFIX + "audience:"' in live_discovery_html
    assert '["saved", "reviewed", "contacted", "responded"]' in live_discovery_html
    assert 'id="audienceStatusFilter"' in live_discovery_html
    assert 'id="audienceChannelFilter"' in live_discovery_html
    assert 'function applyAudienceFilters()' in live_discovery_html
    assert 'data-audience-note="true"' in live_discovery_html
    assert 'Private note: none' in live_discovery_html
    assert 'id="audienceReviewProgress"' in live_discovery_html
    assert 'value="followup_due"' in live_discovery_html
    assert 'data-audience-followup="tomorrow"' in live_discovery_html
    assert 'data-audience-followup="week"' in live_discovery_html
    assert 'data-audience-followup="clear"' in live_discovery_html
    assert 'id="audienceFollowupCount"' in live_discovery_html
    assert 'audience-history-state' in live_discovery_html
    assert 'trackingHistoryLabel(existingAudienceTracking)' in live_discovery_html
    assert 'value="high_score"' in live_discovery_html
    assert 'id="audienceSort"' in live_discovery_html
    assert 'value="rules_first"' in live_discovery_html
    assert 'value="restrictions_first"' in live_discovery_html
    assert 'id="exportAudienceCsvButton"' in live_discovery_html
    assert 'function exportAudienceCsv()' in live_discovery_html
    assert 'crowdfunding-deepsearch-audience-leads.csv' in live_discovery_html
    assert 'id="audienceReviewQueueButton"' in live_discovery_html
    assert 'function setAudienceReviewQueue()' in live_discovery_html
    assert 'status.value = "actionable_review"' in live_discovery_html
    assert 'sort.value = "rules_first"' in live_discovery_html
    assert 'id="audienceFollowupQueueButton"' in live_discovery_html
    assert 'function setAudienceFollowupQueue()' in live_discovery_html
    assert 'id="audienceRestrictedQueueButton"' in live_discovery_html
    assert 'function setAudienceRestrictedQueue()' in live_discovery_html
    assert 'value="high_priority"' in live_discovery_html
    assert 'audience-priority-state' in live_discovery_html
    assert 'card.dataset.audiencePriority' in live_discovery_html
    assert 'value="contact_ready"' in live_discovery_html
    assert 'id="audienceContactReadyButton"' in live_discovery_html
    assert 'function setAudienceContactReadyQueue()' in live_discovery_html
    assert 'audience-permission-state' in live_discovery_html
    assert 'human review required' in live_discovery_html
    assert 'Not verified — verify before outreach' in live_discovery_html
    assert 'data-audience-track="responded"' in live_discovery_html
    assert 'value="responded"' in live_discovery_html
    assert 'id="audienceRespondedCount"' in live_discovery_html
    assert 'audience-next-state' in live_discovery_html
    assert 'review response and decide next action' in live_discovery_html
    assert 'await response or follow up when due' in live_discovery_html
    assert 'data-audience-track="closed"' in live_discovery_html
    assert 'value="closed"' in live_discovery_html
    assert 'id="audienceClosedCount"' in live_discovery_html
    assert 'closed — no further outreach scheduled' in live_discovery_html
    assert 'record.status !== "closed"' in live_discovery_html
    protected_source = (root / "protected_app.py").read_text(encoding="utf-8")
    assert '"/api/audience/discover": "audience"' in protected_source
    assert '"namespace": namespace' in protected_source
    assert 'protected_paths[path] + ":" + _client_key(environ)' in protected_source
    server_source = (root / "server.py").read_text(encoding="utf-8")
    assert '"credit_usage_known": not retrieval.get("configured", False)' in server_source
    assert 'does not claim zero credits' in server_source

    # Static, no-credit source-page checks for service-area language.
    import importlib.util
    server_path = os.path.join(ROOT, "backend", "server.py")
    spec = importlib.util.spec_from_file_location("crowdfunding_server", server_path)
    crowdfunding_server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(crowdfunding_server)
    extract_page_signals = crowdfunding_server.extract_page_signals
    parse_location_parts = crowdfunding_server.parse_location_parts
    build_discovery_queries = crowdfunding_server.build_discovery_queries
    build_audience_queries = crowdfunding_server.build_audience_queries
    audience_channel_tracking_id = crowdfunding_server.audience_channel_tracking_id
    normalize_audience_candidate = crowdfunding_server.normalize_audience_candidate
    audience_relevance_signals = crowdfunding_server.audience_relevance_signals
    detect_audience_channel_rules = crowdfunding_server.detect_audience_channel_rules
    enrich_audience_with_rule_checks = crowdfunding_server.enrich_audience_with_rule_checks
    audience_next_action = crowdfunding_server.audience_next_action
    finalize_audience_actions = crowdfunding_server.finalize_audience_actions
    campaign_tracking_id = crowdfunding_server.campaign_tracking_id
    opportunity_tracking_id = crowdfunding_server.opportunity_tracking_id

    first_campaign_id = campaign_tracking_id("Transportation", "Austin, Texas, United States", 10000)
    second_campaign_id = campaign_tracking_id("Transportation", "Austin, Texas, United States", 10000)
    assert first_campaign_id == second_campaign_id
    assert first_campaign_id.startswith("campaign_")
    changed_campaign_id = campaign_tracking_id("Housing Assistance", "Austin, Texas, United States", 10000)
    assert changed_campaign_id != first_campaign_id

    opportunity = {"url": "https://example.org/help", "name": "Example Help", "type": "Vehicle Assistance"}
    assert opportunity_tracking_id(opportunity) == opportunity_tracking_id(dict(opportunity))
    assert opportunity_tracking_id(opportunity).startswith("opportunity_")

    parsed = parse_location_parts("Austin, Texas, United States")
    assert parsed == {"raw": "Austin, Texas, United States", "city": "Austin", "region": "Texas", "country": "United States"}
    state_queries = build_discovery_queries("Transportation", "Austin, Texas, United States", "state")
    assert all("Texas, United States statewide" in item["query"] for item in state_queries)
    assert all("Austin" not in item["query"] for item in state_queries)
    national_queries = build_discovery_queries("Transportation", "Austin, Texas, United States", "national")
    assert all("United States national nationwide serves applicants" in item["query"] for item in national_queries)
    assert all("Austin" not in item["query"] and "Texas" not in item["query"] for item in national_queries)

    audience_queries = build_audience_queries("Transportation", "Austin, Texas, United States", "automatic")
    assert [item["lane"] for item in audience_queries] == ["Community Forums", "Local Media", "Creators & Podcasts", "Directories & Newsletters"]
    assert [item["geographic_stage"] for item in audience_queries] == ["local", "state", "national", "worldwide"]
    assert all(item["discovery_kind"] == "audience" for item in audience_queries)
    assert all(item["action_mode"] == "review_required" for item in audience_queries)
    audience_candidate = {"url": "https://example.org/community", "name": "Example Community", "type": "Community Forums"}
    assert audience_channel_tracking_id(audience_candidate).startswith("audience_")

    normalized_audience = normalize_audience_candidate(
        {"url": "https://example.org/austin-transportation", "title": "Austin Transportation Community", "snippet": "Austin vehicle and mobility resources"},
        "Community Forums", "Austin transportation community", geographic_stage="local"
    )
    ranked_audience = audience_relevance_signals(normalized_audience, "Transportation", "Austin, Texas, United States")
    assert ranked_audience["discovery_kind"] == "audience"
    assert ranked_audience["audience_relevance_score"] >= 55
    assert ranked_audience["permission_verified"] is False
    assert ranked_audience["automatic_distribution"] is False

    restricted_rules = detect_audience_channel_rules({
        "page_title": "Community Rules",
        "page_text_excerpt": "Please read our community rules. No crowdfunding or self-promotion is permitted."
    })
    assert restricted_rules["status"] == "restriction_detected"
    assert restricted_rules["permission_verified"] is False
    assert restricted_rules["requires_review"] is True

    submission_rules = detect_audience_channel_rules({
        "page_title": "Story Tips",
        "page_text_excerpt": "Read our submission guidelines and send us your story tip."
    })
    assert submission_rules["status"] == "rules_or_submission_route_found"
    assert submission_rules["automatic_distribution"] is False

    # Source-rule enrichment is tested without network access by setting the bounded check budget to zero.
    uninspected = enrich_audience_with_rule_checks([ranked_audience], max_candidates=0)
    assert uninspected[0]["channel_rules"]["status"] == "not_checked"
    assert uninspected[0]["channel_rules"]["permission_verified"] is False
    assert uninspected[0]["channel_rules"]["automatic_distribution"] is False

    restricted_candidate = dict(ranked_audience)
    restricted_candidate["channel_rules"] = {"status": "restriction_detected"}
    restricted_action = audience_next_action(restricted_candidate)
    assert restricted_action["type"] == "do_not_contact_until_reviewed"
    assert restricted_action["blocked_by_rules"] is True
    assert restricted_action["automation_ready"] is False

    finalized_audience = finalize_audience_actions(uninspected)
    assert finalized_audience[0]["outreach_readiness"]["permission_verified"] is False
    assert finalized_audience[0]["outreach_readiness"]["safe_for_automatic_distribution"] is False

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

    unsafe_links_html = """
    <html><body>
      <a href="/apply">Apply here</a>
      <a href="https://evil.example/apply">External application</a>
      <a href="javascript:alert(1)">Application</a>
      <a href="mailto:help@example.org">Contact us</a>
      <a href="/contact">Contact us</a>
    </body></html>
    """
    safe_links = extract_page_signals(unsafe_links_html, "https://example.org/help")
    assert safe_links["application_links"] == ["https://example.org/apply"]
    assert safe_links["contact_links"] == ["https://example.org/contact"]

    print("Integration smoke test passed: health, geographic expansion, discovery fallback, service-area evidence, safe action links, stable tracking IDs, discovery UI safeguards, and invalid input handling.")
finally:
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
