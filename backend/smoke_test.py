import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
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
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
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
            connector_status_code, connector_readiness = read_json("http://127.0.0.1:8099/api/automation/connectors")
            assert connector_status_code == 200
            assert connector_readiness["status"] == "ok"
            assert connector_readiness["automatic_distribution_enabled"] is False
            assert len(connector_readiness["connectors"]) == 1
            assert connector_readiness["connectors"][0]["mechanism"] == "sendgrid_mail_v3"
            assert connector_readiness["connectors"][0]["send_enabled"] is False
            assert connector_readiness["unregistered_routes_remain_manual"] is True
            break
        except Exception as error:
            last_error = error
            time.sleep(0.25)
    else:
        server_output = ""
        if process.poll() is not None and process.stdout is not None:
            server_output = process.stdout.read()
        raise RuntimeError(
            "Backend did not become healthy: "
            + repr(last_error)
            + ("\\nServer output:\\n" + server_output if server_output else "")
        )

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
    assert sorted(set(item["geographic_stage"] for item in audience["search_plan"])) == ["local", "national", "state", "worldwide"]
    assert len(audience["search_plan"]) == 16

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

    # Static UI checks. Literal backslash-n artifacts previously leaked into
    # HTML/statement boundaries. Keep targeted guards so intentional JS string
    # escapes can still be used safely in the future.
    live_discovery_path = os.path.join(ROOT, "backend", "static", "live-discovery.html")
    with open(live_discovery_path, "r", encoding="utf-8") as handle:
        live_discovery_html = handle.read()
    server_path = os.path.join(ROOT, "backend", "server.py")
    with open(server_path, "r", encoding="utf-8") as handle:
        server_source = handle.read()
    analyzer_path = os.path.join(ROOT, "analyzer.html")
    with open(analyzer_path, "r", encoding="utf-8") as handle:
        analyzer_html = handle.read()
    workspace_operations_path = os.path.join(ROOT, "backend", "static", "workspace-operations.html")
    with open(workspace_operations_path, "r", encoding="utf-8") as handle:
        workspace_operations_html = handle.read()
    assert 'id="actionQueue"' in workspace_operations_html
    assert "function actionQueue(limit=50)" in workspace_operations_html
    assert "function actionQueueCounts(items)" in workspace_operations_html
    assert "Math.min(50,Number(limit)||50)" in workspace_operations_html
    assert "record.status===\"closed\"" in workspace_operations_html
    assert "t>now+7*86400000" in workspace_operations_html
    assert 'const rank={overdue:0,due:1,upcoming:2}' in workspace_operations_html
    assert '.slice(0,max)' in workspace_operations_html
    assert 'renderActionQueue();' in workspace_operations_html
    assert "Institutional opportunity" in workspace_operations_html
    assert "Audience lead" in workspace_operations_html
    assert "action_queue_counts:actionQueueCounts(actionQueue(50))" in workspace_operations_html
    assert 'function restoreWorkspaceFromQuery()' in live_discovery_html
    assert 'get("workspace")' in live_discovery_html
    assert 'DOMContentLoaded", restoreWorkspaceFromQuery' in live_discovery_html
    assert '/live-discovery.html?workspace=' in workspace_operations_html
    assert 'encodeURIComponent(item.workspace_id)' in workspace_operations_html
    assert "function renderActionQueue()" in workspace_operations_html
    assert "function analyticsFreshnessLabel(value)" in workspace_operations_html
    assert "stale 7d+" in workspace_operations_html
    assert "stale 24h+" in workspace_operations_html
    assert "current <24h" in workspace_operations_html
    assert "Institutional analytics updated" in workspace_operations_html
    assert 'applicationRouteDetected' in live_discovery_html
    assert 'value="application_route"' in live_discovery_html
    assert 'value="contact_route"' in live_discovery_html
    assert 'value="program_evidence"' in live_discovery_html
    assert 'value="eligibility_language"' in live_discovery_html
    assert 'filter === "application_route"' in live_discovery_html
    assert 'filter === "contact_route"' in live_discovery_html
    assert 'filter === "program_evidence"' in live_discovery_html
    assert 'filter === "eligibility_language"' in live_discovery_html
    assert 'Eligibility language detected (unverified)' in live_discovery_html
    assert 'contactRouteDetected' in live_discovery_html
    assert 'eligibilityLanguageDetected' in live_discovery_html
    assert 'eligibility_language_detected_unverified' in live_discovery_html
    assert 'application_routes_detected' in live_discovery_html
    assert 'contact_routes_detected' in live_discovery_html
    assert 'eligibility-language signals (unverified)' in workspace_operations_html
    assert 'version:8' in workspace_operations_html
    assert "version:8" in workspace_operations_html
    assert "action_queue:actionQueue(50)" in workspace_operations_html
    assert "version:8" in workspace_operations_html
    assert "private_notes_included:false" in workspace_operations_html
    assert "response_details_included:false" in workspace_operations_html
    assert "outreach_draft_bodies_included:false" in workspace_operations_html
    assert "recipient_addresses_included:false" in workspace_operations_html
    assert 'id="workspaceOperationsButton"' in live_discovery_html
    assert 'id="exportCampaignProgressButton"' in live_discovery_html
    assert 'function institutionalProgressSnapshot()' in live_discovery_html
    assert 'id="dashReviewed"' in live_discovery_html
    assert 'id="dashClosed"' in live_discovery_html
    assert 'id="dashApplicationRoutes"' in live_discovery_html
    assert 'id="dashContactRoutes"' in live_discovery_html
    assert 'id="dashEligibilityLanguage"' in live_discovery_html
    assert 'record.status === "reviewed") counts.reviewed += 1' in live_discovery_html
    assert 'record.status === "closed") counts.closed += 1' in live_discovery_html
    assert 'counts.applicationRoutes += 1' in live_discovery_html
    assert 'counts.contactRoutes += 1' in live_discovery_html
    assert 'counts.eligibilityLanguage += 1' in live_discovery_html
    assert 'value="tracked_reviewed"' in live_discovery_html
    assert 'value="tracked_closed"' in live_discovery_html
    assert 'data-track="reviewed"' in live_discovery_html
    assert 'data-track="closed"' in live_discovery_html
    assert 'filter.startsWith("tracked_")' in live_discovery_html
    assert 'id="dashFollowupsOverdue"' in live_discovery_html
    assert 'id="dashFollowupsUpcoming"' in live_discovery_html
    assert 'value="followups_overdue"' in live_discovery_html
    assert 'value="followups_upcoming"' in live_discovery_html
    assert 'followups_overdue_24h' in live_discovery_html
    assert 'followups_upcoming_7d' in live_discovery_html
    assert 'function followupState(record, nowValue)' in live_discovery_html
    assert 'const followup = followupState(record, now);' in live_discovery_html
    assert 'followups_overdue_24h: snapshot.followups_overdue_24h' in live_discovery_html
    assert 'followups_upcoming_7d: snapshot.followups_upcoming_7d' in live_discovery_html
    assert 'function persistInstitutionalAnalyticsSnapshot(snapshot)' in live_discovery_html
    assert 'institutional_analytics_summary:' in live_discovery_html
    assert 'persistInstitutionalAnalyticsSnapshot(institutionalProgressSnapshot())' in live_discovery_html
    assert 'summary.institutional_analytics=profile.institutional_analytics_summary||null' in workspace_operations_html
    assert 'Institutional reviewed' in workspace_operations_html
    assert 'Institutional overdue 24h+' in workspace_operations_html
    assert 'Institutional analytics snapshot pending' in workspace_operations_html
    assert 'version:8' in workspace_operations_html
    assert 'function exportCampaignProgressSummary()' in live_discovery_html
    assert 'crowdfunding-deepsearch-campaign-progress-summary' in live_discovery_html
    assert 'institutional_opportunities: institutionalProgressSnapshot()' in live_discovery_html
    assert 'audience: audienceProgressSnapshot()' in live_discovery_html
    assert 'recipient_addresses_included:false' in live_discovery_html
    assert "/workspace-operations.html" in live_discovery_html
    assert 'href="/live-discovery.html"' in workspace_operations_html
    assert live_discovery_html.startswith("<!DOCTYPE html>\n<html")
    assert live_discovery_html.lower().count("<!doctype html>") == 1
    assert live_discovery_html.index("function submissionPacketIsCurrent") > live_discovery_html.index("<script>")
    assert live_discovery_html.index("function setAudienceHandoffRefreshQueue") > live_discovery_html.index("<script>")
    assert live_discovery_html.index("function setAudienceHandoffPreparedQueue") > live_discovery_html.index("<script>")
    assert '</option>\\n          <option' not in live_discovery_html
    assert '</button>\\n      <button' not in live_discovery_html
    assert ');\\n    set("audienceHandoffRefreshCount"' not in live_discovery_html
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
    # Audience cards must initialize score before dataset/priority expressions use it.
    score_init = live_discovery_html.index("const score = item.audience_relevance_score ?? 0;")
    score_use = live_discovery_html.index("card.dataset.audienceScore = String(score);")
    assert score_init != -1 and score_use != -1 and score_init < score_use
    assert 'function runAudiencePlan()' in live_discovery_html
    assert 'No automatic posting' in live_discovery_html
    assert 'id="audienceDiscoverButton"' in live_discovery_html
    assert 'id="automationReadinessChecklist"' in live_discovery_html
    assert '/api/automation/operational-readiness' in server_source
    assert '"authorization_granted": False' in server_source
    assert '"tracking_id_included": False' in server_source
    assert '"lead_name_included": False' in server_source
    assert '"recipient_address_included": False' in server_source
    assert '"message_body_included": False' in server_source
    assert '"blocker_count": len(blockers)' in server_source
    assert '"network_io": False' in server_source
    assert '"sent": False' in server_source
    assert 'id="automationOperationalReadiness"' in live_discovery_html
    assert 'function loadAutomationOperationalReadiness()' in live_discovery_html
    assert 'function automationBlockerCategory(code)' in live_discovery_html
    assert 'function groupedAutomationBlockers(codes)' in live_discovery_html
    assert 'updated.last_automation_dry_run = {' in live_discovery_html
    assert 'blockers.map(function(code) { return String(code).slice(0, 80); }).slice(0, 30)' in live_discovery_html
    assert 'const blockerSummary = await automationBlockerSummary(blockers);' in live_discovery_html
    assert 'const blockerSummary = await automationBlockerSummary(data.blockers);' in live_discovery_html
    assert 'Execution prerequisites: blocked — ' in live_discovery_html
    assert 'async function automationBlockerSummary(codes)' in live_discovery_html
    assert '"Permission / authorization"' in live_discovery_html
    assert '"Compliance / sender"' in live_discovery_html
    assert '"Duplicate protection"' in live_discovery_html
    assert '"Storage / quota"' in live_discovery_html
    assert '"Connector / deployment"' in live_discovery_html
    assert 'fetch(AUTOMATION_OPERATIONAL_READINESS_URL)' in live_discovery_html
    assert 'Diagnostic only — sent: no; network I/O: no; authorization granted: no.' in live_discovery_html
    assert 'SENDGRID_SERVER_READINESS_URL' in live_discovery_html
    assert '/api/automation/connectors/sendgrid/server-readiness' in live_discovery_html
    assert 'fetch(SENDGRID_SERVER_READINESS_URL)' in live_discovery_html
    assert 'No send is authorized by this check.' in live_discovery_html
    assert 'No send was attempted.' in live_discovery_html
    assert 'function renderAutomationReadinessChecklist(connector)' in live_discovery_html
    assert 'policy.registry_enabled === true' in live_discovery_html
    assert 'policy.deployment_enabled === true' in live_discovery_html
    assert 'policy.connector_opt_in === true' in live_discovery_html
    assert 'Checklist status never authorizes a send.' in live_discovery_html
    assert 'id="automationConnectorStatus"' in live_discovery_html
    assert 'AUTOMATION_CONNECTORS_URL' in live_discovery_html
    assert 'function loadAutomationConnectorStatus()' in live_discovery_html
    assert 'connector.mechanism === "sendgrid_mail_v3"' in live_discovery_html
    assert 'credentials not configured' in live_discovery_html
    assert 'live send disabled' in live_discovery_html
    assert 'AUTOMATION_DRY_RUN_URL' in live_discovery_html
    assert 'data-audience-automation-check="true"' in live_discovery_html
    assert 'Check automation readiness' in live_discovery_html
    assert 'Check execution prerequisites' in live_discovery_html
    assert 'data-audience-execution-check="true"' in live_discovery_html
    assert 'last_automation_execution_check' in live_discovery_html
    assert 'permission_review_current: permissionCurrent' in live_discovery_html
    assert 'deduplication_clear: !contactedAlready' in live_discovery_html
    assert 'workspace_id: currentCampaignWorkspaceId()' in live_discovery_html
    assert 'route: verifiedApplicationRouteLinks[0] || ""' in live_discovery_html
    assert 'function automationBlockerLabel(code)' in live_discovery_html
    assert 'no approved connector is registered for this mechanism' in live_discovery_html
    assert 'the approved connector is not configured' in live_discovery_html
    assert 'AUTOMATION_EXECUTION_CHECK_URL' in live_discovery_html
    assert 'SENDGRID_PREFLIGHT_URL' in live_discovery_html
    assert 'SENDGRID_SIMULATE_URL' in live_discovery_html
    assert 'Simulate SendGrid transport' in live_discovery_html
    assert 'data-audience-sendgrid-simulate="true"' in live_discovery_html
    assert 'network I/O: ' in live_discovery_html
    assert 'sender_verified: false' in live_discovery_html
    assert 'compliance_confirmed: false' in live_discovery_html
    assert 'unsubscribe_ready: false' in live_discovery_html
    assert 'sendgridPreflightStatus' in live_discovery_html
    assert 'function loadSendGridPreflightStatus()' in live_discovery_html
    assert 'No send is authorized by this check.' in live_discovery_html
    assert 'live sending remains disabled' in live_discovery_html
    assert 'explicit user authorization is required' in live_discovery_html
    assert 'the route permission review must be current' in live_discovery_html
    assert 'the lead must pass duplicate-contact protection' in live_discovery_html
    assert 'connector gate passed in dry-run only — nothing was sent.' in live_discovery_html
    assert 'unregistered routes remain manual' in live_discovery_html
    assert 'AUDIENCE_DISCOVER_URL' in live_discovery_html
    assert 'function runAudienceDiscovery()' in live_discovery_html
    assert 'function renderAudienceLead(item)' in live_discovery_html
    assert 'Automation eligibility:</strong>' in live_discovery_html
    assert 'Manual review only — automatic submission not enabled' in live_discovery_html
    assert 'card.dataset.automationEligible' in live_discovery_html
    assert 'card.dataset.verifiedApplicationRoutes = JSON.stringify(verifiedApplicationRouteLinks);' in live_discovery_html
    assert 'JSON.parse(card.dataset.verifiedApplicationRoutes || "[]")' in live_discovery_html
    assert 'Channel rules:</strong>' in live_discovery_html
    assert 'data-audience-track="saved"' in live_discovery_html
    assert 'data-audience-track="reviewed"' in live_discovery_html
    assert 'data-audience-track="contacted"' in live_discovery_html
    assert 'AUDIENCE_OUTREACH_DRAFT_URL' in live_discovery_html
    assert 'data-audience-draft="true"' in live_discovery_html
    assert 'Saved outreach draft — review before sending' in live_discovery_html
    assert 'function renderSavedAudienceDraft(card, trackingKey, draft)' in live_discovery_html
    assert 'outreach_draft: previous.outreach_draft || null' in live_discovery_html
    assert 'data-save-draft="true"' in live_discovery_html
    assert 'data-clear-draft="true"' in live_discovery_html
    assert 'Saved locally with this lead.' in live_discovery_html
    assert 'id="audienceDraftCount"' in live_discovery_html
    assert 'id="audienceReadyCount"' in live_discovery_html
    assert 'id="audienceAwaitingCount"' in live_discovery_html
    assert 'id="audienceBlockedCount"' in live_discovery_html
    assert 'function setAudienceAwaitingQueue()' in live_discovery_html
    assert 'function setAudienceRespondedQueue()' in live_discovery_html
    assert 'data-audience-response="true"' in live_discovery_html
    assert 'response: previous.response || null' in live_discovery_html
    assert 'function responseDetailsLabel(response)' in live_discovery_html
    assert 'function responseNextAction(response)' in live_discovery_html
    assert 'Record response details after the lead has been contacted.' in live_discovery_html
    assert 'id="audienceContactRate"' in live_discovery_html
    assert 'id="audienceResponseRate"' in live_discovery_html
    assert 'id="audienceReadyBacklog"' in live_discovery_html
    assert 'function trackingHasStatus(record, status)' in live_discovery_html
    assert 'counts.everContacted / reviewedBase' in live_discovery_html
    assert 'counts.everResponded / counts.everContacted' in live_discovery_html
    assert 'function currentCampaignTrackingProfile()' in live_discovery_html
    assert 'version: 5' in live_discovery_html
    assert 'campaign: currentCampaignTrackingProfile()' in live_discovery_html
    assert 'Invalid tracking backup' in live_discovery_html
    assert 'tracking records imported across available campaign workspaces.' in live_discovery_html
    assert 'id="campaignUrl"' in live_discovery_html
    assert 'id="campaignSummary"' in live_discovery_html
    assert 'document.getElementById("campaignUrl").value.trim()' in live_discovery_html
    assert 'document.getElementById("campaignSummary").value.trim()' in live_discovery_html
    assert live_discovery_html.count('campaign_url: document.getElementById("campaignUrl").value.trim()') >= 3
    assert 'campaign_summary": campaign_summary' in server_source
    assert 'Campaign URL must use http or https.' in server_source
    assert 'data-audience-permission="true"' in live_discovery_html
    assert 'permission_review: previous.permission_review || null' in live_discovery_html
    assert 'automatic_distribution: false' in live_discovery_html
    assert 'explicit route permission review confirmed' in live_discovery_html
    assert 'record.permission_review && record.permission_review.reviewed_at' in live_discovery_html
    assert 'id="audienceSubmissionReadyCount"' in live_discovery_html
    assert 'value="submission_ready"' in live_discovery_html
    assert 'function setAudienceSubmissionReadyQueue()' in live_discovery_html
    assert 'card.dataset.hasApplicationRoute === "true"' in live_discovery_html
    assert 'data-audience-packet="true"' in live_discovery_html
    assert 'Crowdfunding DeepSearch — Assisted Submission Packet' in live_discovery_html
    assert 'Automatic submission: disabled — complete the official route manually.' in live_discovery_html
    assert 'submission_packet: previous.submission_packet || null' in live_discovery_html
    assert 'window.open(applicationRoute' in live_discovery_html
    assert 'function applyImportedCampaignProfile(campaign)' in live_discovery_html
    assert 'function campaignProfilesDiffer(a, b)' in live_discovery_html
    assert 'Restore the backup campaign profile too?' in live_discovery_html
    assert 'Campaign profile restored.' in live_discovery_html
    assert 'Current campaign profile left unchanged.' in live_discovery_html
    assert 'function campaignTrackingNamespace()' in live_discovery_html
    assert 'function currentCampaignTrackingPrefix()' in live_discovery_html
    assert 'currentCampaignTrackingPrefix() + "audience:" + audienceTrackingId' in live_discovery_html
    assert 'currentCampaignTrackingPrefix() + "opportunity:" + opportunityTrackingId' in live_discovery_html
    assert 'const legacyTrackingKey = TRACKING_PREFIX + campaignTrackingId + ":" + opportunityTrackingId' in live_discovery_html
    assert 'localStorage.removeItem(legacyTrackingKey)' in live_discovery_html
    assert '"Permission reviewed at"' in live_discovery_html
    assert '"Submission packet prepared at"' in live_discovery_html
    assert '"Submission route"' in live_discovery_html
    assert 'trackingHasStatus(record, "contacted") ? "yes" : "no"' in live_discovery_html
    assert 'crowdfunding-deepsearch-audience-outreach-ledger.csv' in live_discovery_html
    assert 'draft_prepared_at: previous.draft_prepared_at || null' in live_discovery_html
    assert 'record.draft_prepared_at = record.draft_prepared_at || new Date().toISOString()' in live_discovery_html
    assert 'record.draft_prepared_at = null' in live_discovery_html
    assert '"Draft prepared at"' in live_discovery_html
    assert 'function audienceRouteFingerprint(routes)' in live_discovery_html
    assert 'function permissionReviewIsCurrent(record, routeFingerprint)' in live_discovery_html
    assert 'route_fingerprint: routeFingerprint' in live_discovery_html
    assert 'Permission review: route changed — review again' in live_discovery_html
    assert 'function sameSiteRoute(sourceUrl, routeUrl)' in live_discovery_html
    assert 'const verifiedApplicationRouteLinks = applicationRouteLinks.filter' in live_discovery_html
    assert 'sameSiteRoute(url, route)' in live_discovery_html
    assert 'const applicationRoute = verifiedApplicationRouteLinks[0] || ""' in live_discovery_html
    assert 'Same-site submission route + permission confirmed' in live_discovery_html
    assert 'deepSearchButton.dataset.campaignUrl = campaignUrl' in analyzer_html
    assert 'deepSearchButton.dataset.campaignSummary = rawDescription.slice(0, 1200)' in analyzer_html
    assert 'campaign_url: button.dataset.campaignUrl || ""' in analyzer_html
    assert 'campaign_summary: button.dataset.campaignSummary || ""' in analyzer_html
    assert 'id="audienceHandoffPreparedCount"' in live_discovery_html
    assert 'value="handoff_prepared"' in live_discovery_html
    assert 'function setAudienceHandoffPreparedQueue()' in live_discovery_html
    assert 'counts.handoffsPrepared += 1' in live_discovery_html
    assert 'function submissionPacketIsCurrent(record, routeFingerprint, verifiedApplicationRoutes)' in live_discovery_html
    assert 'route_fingerprint: routeFingerprint' in live_discovery_html
    assert 'submissionPacketIsCurrent(record, card.dataset.routeFingerprint || "", JSON.parse(card.dataset.verifiedApplicationRoutes || "[]"))' in live_discovery_html
    import_pos = live_discovery_html.index('function importTracking(event)')
    restore_pos = live_discovery_html.index('if (restore) profileRestored = applyImportedCampaignProfile(campaign);', import_pos)
    destination_pos = live_discovery_html.index('const destinationPrefix = currentCampaignTrackingPrefix();', import_pos)
    assert restore_pos < destination_pos
    namespace_start = live_discovery_html.index('function campaignTrackingNamespace()')
    namespace_end = live_discovery_html.index('function currentCampaignTrackingPrefix()', namespace_start)
    assert 'profile.goal' not in live_discovery_html[namespace_start:namespace_end]
    assert 'const CAMPAIGN_WORKSPACE_ID_KEY = "crowdfunding-deepsearch:active-workspace-id"' in live_discovery_html
    assert 'function currentCampaignWorkspaceId()' in live_discovery_html
    assert 'function setCurrentCampaignWorkspaceId(id)' in live_discovery_html
    assert 'return "campaign:" + currentCampaignWorkspaceId() + ":"' in live_discovery_html
    assert 'campaign_workspace_id: currentCampaignWorkspaceId()' in live_discovery_html
    assert 'JSON.stringify({version: 5, exported_at:' in live_discovery_html
    assert 'key.startsWith(TRACKING_PREFIX + "campaign:")' in live_discovery_html
    assert 'const preservesWorkspace = payload.version >= 5' in live_discovery_html
    assert 'tracking records imported across available campaign workspaces' in live_discovery_html
    assert 'status === "contact_ready"' in live_discovery_html
    contact_ready_start = live_discovery_html.index('status === "contact_ready"')
    assert 'permissionReviewIsCurrent(record, card.dataset.routeFingerprint || "")' in live_discovery_html[contact_ready_start:contact_ready_start + 600]
    assert 'const permissionReviewed = permissionReviewIsCurrent(previous, card.dataset.routeFingerprint || "");' in live_discovery_html
    assert 'const submissionReady = !!(record && record.status === "reviewed" && draft && permissionReviewIsCurrent(record, card.dataset.routeFingerprint || "")' in live_discovery_html
    assert 'function submissionPacketIsCurrent(record, routeFingerprint, verifiedApplicationRoutes)' in live_discovery_html
    assert 'card.dataset.verifiedApplicationRoutes = JSON.stringify(verifiedApplicationRouteLinks)' in live_discovery_html
    assert 'verifiedApplicationRoutes.some(function(route)' in live_discovery_html
    assert 'id="audienceHandoffRefreshCount"' in live_discovery_html
    assert 'value="handoff_refresh"' in live_discovery_html
    assert 'function setAudienceHandoffRefreshQueue()' in live_discovery_html
    assert 'id="audienceHandoffRefreshButton"' in live_discovery_html
    assert 'id="audiencePermissionCurrentCount"' in live_discovery_html
    assert 'id="audiencePermissionRefreshCount"' in live_discovery_html
    assert 'value="permission_refresh"' in live_discovery_html
    assert 'function setAudiencePermissionRefreshQueue()' in live_discovery_html
    assert 'id="audiencePermissionRefreshButton"' in live_discovery_html
    assert 'function persistAudienceAnalyticsSnapshot(snapshot)' in live_discovery_html
    assert 'analytics_summary:' in live_discovery_html
    assert 'analytics_updated_at: snapshot.exported_at' in live_discovery_html
    assert 'persistAudienceAnalyticsSnapshot(audienceProgressSnapshot())' in live_discovery_html
    assert 'permission_reviews_need_refresh: snapshot.permission_reviews_need_refresh' in live_discovery_html
    assert 'summary.audience_analytics=profile.analytics_summary||null' in workspace_operations_html
    assert 'Permission reviews need refresh' in workspace_operations_html
    assert 'Handoffs need refresh' in workspace_operations_html
    assert 'version:8' in workspace_operations_html
    assert 'status === "handoff_refresh"' in live_discovery_html
    assert 'status === "permission_refresh"' in live_discovery_html
    assert 'snapshot.permission_reviews_current += 1' in live_discovery_html
    assert 'snapshot.permission_reviews_need_refresh += 1' in live_discovery_html
    assert 'id="audienceDuplicatesMergedCount"' in live_discovery_html
    assert 'function updateAudienceDashboard(results, audienceMeta)' in live_discovery_html
    assert 'audienceMeta.duplicates_merged' in live_discovery_html
    assert 'duplicates_merged: Number(document.getElementById("audienceDuplicatesMergedCount")?.textContent || 0)' in live_discovery_html
    assert 'Duplicate discoveries merged' in workspace_operations_html
    assert 'duplicatesMerged' in workspace_operations_html
    assert 'version:8' in workspace_operations_html
    assert 'id="exportAudienceProgressButton"' in live_discovery_html
    assert 'function audienceProgressSnapshot()' in live_discovery_html
    assert 'function exportAudienceProgressSummary()' in live_discovery_html
    assert 'crowdfunding-deepsearch-audience-progress-summary.json' in live_discovery_html
    assert 'private_notes_included: false' in live_discovery_html
    assert 'response_details_included: false' in live_discovery_html
    assert 'outreach_draft_bodies_included: false' in live_discovery_html
    assert 'counts.handoffsRefresh += 1' in live_discovery_html
    assert 'workspace_registry: campaignWorkspaceRegistry()' in live_discovery_html
    assert 'payload.version >= 4 && payload.workspace_registry' in live_discovery_html
    assert '{\\\\n    saveCampaignWorkspaceProfile();' not in live_discovery_html
    assert 'if (campaign.campaign_workspace_id) setCurrentCampaignWorkspaceId(campaign.campaign_workspace_id)' in live_discovery_html
    assert 'function legacyCampaignTrackingPrefixes()' in live_discovery_html
    assert 'function migrateLegacyCampaignRecord(suffix, destinationKey)' in live_discovery_html
    assert 'migrateLegacyCampaignRecord("audience:" + audienceTrackingId, audienceTrackingKey)' in live_discovery_html
    assert 'migrateLegacyCampaignRecord("opportunity:" + opportunityTrackingId, trackingKey)' in live_discovery_html
    assert 'id="newCampaignWorkspaceButton"' in live_discovery_html
    assert 'function startNewCampaignWorkspace()' in live_discovery_html
    assert 'const newId = createCampaignWorkspaceId();' in live_discovery_html
    assert 'Existing tracking will stay saved under the current workspace' in live_discovery_html
    assert 'const CAMPAIGN_WORKSPACE_REGISTRY_KEY = "crowdfunding-deepsearch:workspace-registry"' in live_discovery_html
    assert 'function saveCampaignWorkspaceProfile()' in live_discovery_html
    assert 'function restoreCampaignWorkspace(id)' in live_discovery_html
    assert 'function chooseCampaignWorkspace()' in live_discovery_html
    assert 'id="switchCampaignWorkspaceButton"' in live_discovery_html
    for signature in ['function exportTracking() {', 'async function runDiscovery() {', 'async function runAudiencePlan() {', 'async function runAudienceDiscovery() {']:
        start = live_discovery_html.index(signature)
        assert 'saveCampaignWorkspaceProfile();' in live_discovery_html[start:start + 140]
    assert 'permissionReviewIsCurrent(record, card.dataset.routeFingerprint || "")' in live_discovery_html
    assert 'key.startsWith(TRACKING_PREFIX + "campaign:")' in live_discovery_html
    assert 'destinationPrefix + key.slice(TRACKING_PREFIX.length).replace(/^campaign:[^:]+:/, "")' in live_discovery_html
    assert 'value="draft_ready"' in live_discovery_html
    assert 'tracked === "reviewed" && !!(record && record.outreach_draft)' in live_discovery_html
    assert 'audience-reviewed-routes' in live_discovery_html
    assert 'Detected routes to review:' in live_discovery_html
    assert 'Before marking contacted, this lead must have a detected rules/submission route' in live_discovery_html
    assert 'rules["review_routes"]' in server_source
    assert 'audience-tracking-state' in live_discovery_html
    assert 'Recommended next action:</strong>' in live_discovery_html
    assert 'id="audienceDashboard"' in live_discovery_html
    assert 'id="audienceRestrictionCount"' in live_discovery_html
    assert 'function updateAudienceDashboard(results, audienceMeta)' in live_discovery_html
    assert 'function updateAudienceTrackingDashboard()' in live_discovery_html
    assert 'id="audienceSavedCount"' in live_discovery_html
    assert 'id="audienceReviewedCount"' in live_discovery_html
    assert 'id="audienceContactedCount"' in live_discovery_html
    assert 'currentCampaignTrackingPrefix() + "audience:"' in live_discovery_html
    assert '["saved", "reviewed", "contacted", "responded", "closed"]' in live_discovery_html
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
    assert 'id="audienceReviewQueueButton"' in live_discovery_html
    assert 'function setAudienceReviewQueue()' in live_discovery_html
    assert 'status.value = "actionable_review"' in live_discovery_html
    assert 'sort.value = "rules_first"' in live_discovery_html
    assert 'id="audienceFollowupQueueButton"' in live_discovery_html
    assert 'id="audienceOverdueQueueButton"' in live_discovery_html
    assert 'id="audienceUpcomingQueueButton"' in live_discovery_html
    assert 'value="followup_overdue"' in live_discovery_html
    assert 'value="followup_upcoming"' in live_discovery_html
    assert 'value="followup_soonest"' in live_discovery_html
    assert 'function setAudienceOverdueQueue()' in live_discovery_html
    assert 'function setAudienceUpcomingQueue()' in live_discovery_html
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
    protected_source = open(os.path.join(ROOT, "backend", "protected_app.py"), encoding="utf-8").read()
    assert '"/api/audience/discover": "audience"' in protected_source
    assert '"namespace": namespace' in protected_source
    assert 'key = protected_paths[path] + ":" + client' in protected_source
    server_source = open(os.path.join(ROOT, "backend", "server.py"), encoding="utf-8").read()
    assert '"credit_usage_known": not retrieval.get("configured", False)' in server_source
    assert 'does not claim zero credits' in server_source
    assert 'MAX_AUDIENCE_RULE_CHECKS = env_int("MAX_AUDIENCE_RULE_CHECKS", 4, 0, 12)' in server_source
    assert 'max_candidates = MAX_AUDIENCE_RULE_CHECKS' in server_source
    assert 'def deduplicate_audience_candidates(candidates):' in server_source
    assert 'results = deduplicate_audience_candidates(results)' in server_source
    assert 'discovered_in_lanes' in server_source
    assert 'discovered_in_stages' in server_source
    assert '"transport" in topic_lower or "vehicle" in topic_lower' in server_source
    assert 'transportation vehicle assistance resources' in server_source
    assert '"medical" in topic_lower or "health" in topic_lower' in server_source
    assert '"housing" in topic_lower or "rent" in topic_lower' in server_source
    assert 'commercial_or_irrelevant_noise:' in server_source
    assert 'fundraising_context:' in server_source
    assert '"advertise with us"' in server_source
    assert '"press release distribution"' in server_source
    audience_rule_fn = server_source.split("def enrich_audience_with_rule_checks", 1)[1].split("def ", 1)[0]
    assert "max_candidates = MAX_AUDIENCE_RULE_CHECKS" in audience_rule_fn
    assert "ignored_query_keys" in server_source
    assert '"utm_source"' in server_source
    assert '"fbclid"' in server_source
    assert "parse_qsl" in server_source
    assert '"candidate_count_before_dedup": before_dedup_count' in server_source
    assert '"candidate_count_after_dedup": after_dedup_count' in server_source
    assert '"duplicates_merged": max(0, before_dedup_count - after_dedup_count)' in server_source
    assert 'Duplicate discoveries merged:' in live_discovery_html
    assert '["saved", "reviewed", "contacted", "responded", "closed"]' in live_discovery_html
    assert '(status === "responded" || status === "closed") ? null' in live_discovery_html
    assert 'legacyAudienceTrackingKey' in live_discovery_html
    assert 'localStorage.removeItem(legacyAudienceTrackingKey)' in live_discovery_html
    assert 'result["configured"] and result.get("candidates")' in server_source
    assert 'configured_provider_returned_no_candidates' in server_source
    assert 'Configured providers returned no candidates.' in server_source
    workflow_path = os.path.join(ROOT, ".github", "workflows", "production-deployment.yml")
    with open(workflow_path, "r", encoding="utf-8") as workflow_file:
        production_workflow = workflow_file.read()
    assert 'workflows: ["Backend checks"]' in production_workflow
    assert "cancel-in-progress: true" in production_workflow
    assert "github.event.workflow_run.head_sha" in production_workflow
    assert "plan_by_query" in server_source
    assert "plan_by_lane.setdefault" in server_source
    assert "plan_by_query.get((lane, source_query))" in server_source
    assert 'item["_discovery_count"] = 1' in server_source
    assert 'current["_discovery_count"] = current.get("_discovery_count", 1) + 1' in server_source
    assert 'item.pop("_discovery_count", 1)' in server_source
    protected_path = os.path.join(ROOT, "backend", "protected_app.py")
    with open(protected_path, "r", encoding="utf-8") as protected_file:
        protected_source = protected_file.read()
    assert "MAX_TOTAL_DISCOVERY_REQUESTS = 7" in protected_source
    assert '"all-discovery:" + client' in protected_source
    assert "len(total_recent) >= MAX_TOTAL_DISCOVERY_REQUESTS" in protected_source
    assert 'MAX_AUDIENCE_SEARCH_QUERIES = env_int("MAX_AUDIENCE_SEARCH_QUERIES", 4, 1, 8)' in server_source
    assert 'len(full_plan) > budget' in server_source
    assert 'round(i * (len(full_plan) - 1) / (budget - 1))' in server_source
    assert "selected_stages = stages" in server_source
    assert "for geo_stage, geography in selected_stages:" in server_source
    assert "for lane, terms in lanes:" in server_source
    assert 'outreach_intent:' in server_source
    assert 'low_signal_page' in server_source
    assert 'cross_search_corroboration' in server_source
    assert '"boost": corroboration_boost' in server_source

    # Static, no-credit source-page checks for service-area language.
    import importlib.util
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
    automation_distribution_decision = crowdfunding_server.automation_distribution_decision
    automation_connector_status = crowdfunding_server.automation_connector_status
    automation_live_send_policy = crowdfunding_server.automation_live_send_policy
    validate_automation_connector_definition = crowdfunding_server.validate_automation_connector_definition
    build_automation_dry_run = crowdfunding_server.build_automation_dry_run
    validate_live_send_authorization = crowdfunding_server.validate_live_send_authorization
    automation_admin_endpoints_enabled = crowdfunding_server.automation_admin_endpoints_enabled
    automation_operational_tools_enabled = crowdfunding_server.automation_operational_tools_enabled
    prepare_automation_storage_path = crowdfunding_server.prepare_automation_storage_path
    automation_storage_status = crowdfunding_server.automation_storage_status
    sendgrid_server_preflight = crowdfunding_server.sendgrid_server_preflight
    check_only_auth = validate_live_send_authorization({
        "user_authorized_check": True,
        "permission_review_current": True,
        "deduplication_clear": True,
    })
    assert check_only_auth["allowed"] is False
    assert "live_send_authorization_required" in check_only_auth["blockers"]
    assert "dry_run_authorization_not_valid_for_send" in check_only_auth["blockers"]
    original_admin_flag = os.environ.pop("AUTOMATION_ADMIN_ENDPOINTS_ENABLED", None)
    try:
        assert automation_admin_endpoints_enabled() is False
        assert automation_operational_tools_enabled() is False
    finally:
        if original_admin_flag is not None:
            os.environ["AUTOMATION_ADMIN_ENDPOINTS_ENABLED"] = original_admin_flag
    prepared_storage = prepare_automation_storage_path()
    assert prepared_storage["prepared"] is True
    default_storage = automation_storage_status()
    assert default_storage["backend"] == "sqlite"
    assert default_storage["live_ready"] is False
    assert default_storage["path_prepared"] is True
    assert default_storage["backend"] == "sqlite"
    assert default_storage["supported_backend"] is True
    assert default_storage["persistence_evidence"] is False
    assert default_storage["ephemeral_path"] is True
    assert default_storage["reason"] == "durable_automation_storage_required"
    original_env = dict(os.environ)
    try:
        for env_name in (
            "SENDGRID_API_KEY", "SENDGRID_FROM_EMAIL", "SENDGRID_SENDER_VERIFIED",
            "SENDGRID_COMPLIANCE_CONFIRMED", "SENDGRID_UNSUBSCRIBE_READY",
            "AUTOMATION_LIVE_SEND_ENABLED", "AUTOMATION_SENDGRID_MAIL_V3_ENABLED",
        ):
            os.environ.pop(env_name, None)
        trusted_preflight = sendgrid_server_preflight()
        assert trusted_preflight["ready"] is False
        assert "sendgrid_api_key_not_configured" in trusted_preflight["blockers"]
        assert trusted_preflight["credentials_configured"] is False
    finally:
        os.environ.clear()
        os.environ.update(original_env)
    explicit_send_auth = validate_live_send_authorization({
        "user_authorized_send": True,
        "permission_review_current": True,
        "deduplication_clear": True,
    })
    assert explicit_send_auth["allowed"] is True
    validate_automation_execution_request = crowdfunding_server.validate_automation_execution_request
    build_live_sendgrid_execution_candidate = crowdfunding_server.build_live_sendgrid_execution_candidate
    automation_rate_limit_status = crowdfunding_server.automation_rate_limit_status
    automation_attempt_idempotency_key = crowdfunding_server.automation_attempt_idempotency_key
    automation_retry_decision = crowdfunding_server.automation_retry_decision
    validate_automation_retry_request = crowdfunding_server.validate_automation_retry_request
    automation_ledger_connection = crowdfunding_server.automation_ledger_connection
    consume_automation_rate_limit = crowdfunding_server.consume_automation_rate_limit
    automation_idempotency_key = crowdfunding_server.automation_idempotency_key
    automation_rate_limit_contract = crowdfunding_server.automation_rate_limit_contract
    build_sendgrid_mail_v3_payload = crowdfunding_server.build_sendgrid_mail_v3_payload
    sendgrid_connector_preflight = crowdfunding_server.sendgrid_connector_preflight
    build_disabled_sendgrid_execution_plan = crowdfunding_server.build_disabled_sendgrid_execution_plan
    redact_automation_plan = crowdfunding_server.redact_automation_plan
    automation_execution_record = crowdfunding_server.automation_execution_record
    execute_sendgrid_transport = crowdfunding_server.execute_sendgrid_transport
    persist_automation_execution_record = crowdfunding_server.persist_automation_execution_record
    find_automation_execution_record = crowdfunding_server.find_automation_execution_record
    prune_automation_execution_ledger = crowdfunding_server.prune_automation_execution_ledger
    automation_execution_duplicate_status = crowdfunding_server.automation_execution_duplicate_status
    reserve_live_automation_execution = crowdfunding_server.reserve_live_automation_execution
    transition_automation_execution = crowdfunding_server.transition_automation_execution
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
    assert len(audience_queries) == 16
    expected_audience_lanes = ["Community Forums", "Local Media", "Creators & Podcasts", "Directories & Newsletters"]
    assert [item["lane"] for item in audience_queries[:4]] == expected_audience_lanes
    assert [item["geographic_stage"] for item in audience_queries[::4]] == ["local", "state", "national", "worldwide"]
    assert all([item["lane"] for item in audience_queries[offset:offset + 4]] == expected_audience_lanes for offset in range(0, 16, 4))
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
    assert uninspected[0]["channel_rules"]["automation_eligibility"]["status"] == "manual_review_only"
    assert uninspected[0]["channel_rules"]["automation_eligibility"]["eligible"] is False
    assert uninspected[0]["channel_rules"]["automation_eligibility"]["send_enabled"] is False

    restricted_candidate = dict(ranked_audience)
    restricted_candidate["channel_rules"] = {"status": "restriction_detected"}
    restricted_action = audience_next_action(restricted_candidate)
    assert restricted_action["type"] == "do_not_contact_until_reviewed"
    assert restricted_action["blocked_by_rules"] is True
    assert restricted_action["automation_ready"] is False

    automation_candidate = dict(ranked_audience)
    automation_candidate["channel_rules"] = {
        "automation_eligibility": {
            "eligible": True,
            "send_enabled": True,
            "supported_mechanism": "untrusted-generic-form",
        }
    }
    automation_decision = automation_distribution_decision(automation_candidate)
    assert automation_decision["allowed"] is False
    assert automation_decision["supported"] is False
    assert automation_decision["reason"] == "mechanism_not_registered"
    assert automation_decision["blockers"] == ["mechanism_not_registered"]
    assert crowdfunding_server.SUPPORTED_AUTOMATION_MECHANISMS == frozenset({"sendgrid_mail_v3"})
    connector_status = automation_connector_status("untrusted-generic-form")
    assert connector_status["registered"] is False
    assert connector_status["configured"] is False
    assert connector_status["send_enabled"] is False
    invalid_connector = validate_automation_connector_definition("generic-form", {
        "action_type": "generic_web_form",
        "credential_env": "",
        "rate_limit_per_hour": 0,
        "send_enabled": True,
        "requires_user_authorization": False,
        "documentation_url": "http://example.invalid/docs",
    })
    assert invalid_connector["valid"] is False
    assert "unsupported_action_type" in invalid_connector["errors"]
    assert "credential_env_required" in invalid_connector["errors"]
    assert "bounded_rate_limit_required" in invalid_connector["errors"]
    assert "user_authorization_requirement_required" in invalid_connector["errors"]
    assert "official_documentation_url_required" in invalid_connector["errors"]
    valid_contract = validate_automation_connector_definition("official-test", {
        "action_type": "official_api",
        "credential_env": "OFFICIAL_TEST_TOKEN",
        "rate_limit_per_hour": 10,
        "send_enabled": False,
        "requires_user_authorization": True,
        "documentation_url": "https://example.com/official-api-docs",
    })
    assert valid_contract == {"valid": True, "errors": []}
    assert automation_decision["connector"]["registered"] is False
    dry_run = build_automation_dry_run(automation_candidate)
    assert dry_run["status"] == "blocked"
    assert dry_run["dry_run"] is True
    assert dry_run["sent"] is False
    assert dry_run["decision"]["allowed"] is False
    assert "mechanism_not_registered" in dry_run["next_step"]
    assert "assisted/manual review" in dry_run["next_step"]
    sendgrid_contract = validate_automation_connector_definition(
        "sendgrid_mail_v3", crowdfunding_server.AUTOMATION_CONNECTOR_REGISTRY["sendgrid_mail_v3"]
    )
    assert sendgrid_contract == {"valid": True, "errors": []}
    sendgrid_policy = automation_live_send_policy("sendgrid_mail_v3")
    assert sendgrid_policy["registry_enabled"] is False
    assert sendgrid_policy["deployment_enabled"] is False
    assert sendgrid_policy["connector_opt_in"] is False
    assert sendgrid_policy["live_send_enabled"] is False
    sendgrid_status = automation_connector_status("sendgrid_mail_v3")
    assert sendgrid_status["registered"] is True
    assert sendgrid_status["send_enabled"] is False
    assert sendgrid_status["requires_user_authorization"] is True
    assert sendgrid_status["requires_verified_sender"] is True
    assert sendgrid_status["requires_unsubscribe_compliance"] is True
    preflight = sendgrid_connector_preflight({})
    assert preflight["ready"] is False
    assert preflight["send_enabled"] is False
    assert "verified_sender_email_required" in preflight["blockers"]
    assert "verified_sender_identity_required" in preflight["blockers"]
    assert "email_compliance_confirmation_required" in preflight["blockers"]
    assert "unsubscribe_mechanism_required" in preflight["blockers"]
    disabled_plan = build_disabled_sendgrid_execution_plan({
        "lead": automation_candidate,
        "sendgrid_settings": {},
        "draft": {},
    })
    assert disabled_plan["status"] == "blocked"
    assert disabled_plan["allowed"] is False
    assert disabled_plan["sent"] is False
    assert disabled_plan["payload"] is None
    assert "connector_live_send_disabled" in disabled_plan["blockers"]
    assert "recipient_email_required" in disabled_plan["blockers"]
    original_ledger_path = crowdfunding_server.AUTOMATION_LEDGER_PATH
    crowdfunding_server.AUTOMATION_LEDGER_PATH = "/tmp/crowdfunding-deepsearch-smoke-ledger.sqlite3"
    try:
        if os.path.exists(crowdfunding_server.AUTOMATION_LEDGER_PATH):
            os.remove(crowdfunding_server.AUTOMATION_LEDGER_PATH)
        legacy_path = crowdfunding_server.AUTOMATION_LEDGER_PATH
        legacy_connection = __import__("sqlite3").connect(legacy_path)
        legacy_connection.execute(
            """CREATE TABLE automation_execution_ledger (
                idempotency_key TEXT PRIMARY KEY,
                mechanism TEXT NOT NULL,
                endpoint TEXT,
                outcome TEXT NOT NULL,
                sent INTEGER NOT NULL DEFAULT 0,
                provider_message_id TEXT,
                blockers_json TEXT NOT NULL DEFAULT '[]',
                recorded_at TEXT NOT NULL
            )"""
        )
        legacy_connection.execute(
            """INSERT INTO automation_execution_ledger
               (idempotency_key, mechanism, endpoint, outcome, sent, provider_message_id, blockers_json, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("legacy-key", "sendgrid_mail_v3", "https://api.sendgrid.com/v3/mail/send",
             "failed", 0, None, "[]", "2026-01-01T00:00:00+00:00"),
        )
        legacy_connection.commit()
        legacy_connection.close()
        migrated = automation_ledger_connection()
        migrated_info = migrated.execute("PRAGMA table_info(automation_execution_ledger)").fetchall()
        assert any(row["name"] == "ledger_key" and int(row["pk"]) == 1 for row in migrated_info)
        assert not any(row["name"] == "idempotency_key" and int(row["pk"]) > 0 for row in migrated_info)
        preserved = migrated.execute(
            "SELECT * FROM automation_execution_ledger WHERE idempotency_key = 'legacy-key'"
        ).fetchone()
        assert preserved["execution_mode"] == "live"
        assert preserved["updated_at"] == preserved["recorded_at"]
        assert preserved["resolved_at"] is None
        assert preserved["parent_idempotency_key"] is None
        assert preserved["attempt_number"] == 1
        migrated.close()
        os.remove(crowdfunding_server.AUTOMATION_LEDGER_PATH)
        ledger_record = automation_execution_record({
            "idempotency_key": "smoke-key-1",
            "mechanism": "sendgrid_mail_v3",
            "endpoint": "https://api.sendgrid.com/v3/mail/send",
            "blockers": ["connector_live_send_disabled"],
        }, "simulated", execution_mode="simulation")
        first_write = persist_automation_execution_record(ledger_record)
        second_write = persist_automation_execution_record(ledger_record)
        assert first_write["created"] is True
        assert second_write["created"] is False
        assert find_automation_execution_record("smoke-key-1") is None
        assert automation_execution_duplicate_status("smoke-key-1")["duplicate"] is False
        reservation_plan = {
            "idempotency_key": "smoke-key-1",
            "mechanism": "sendgrid_mail_v3",
            "endpoint": "https://api.sendgrid.com/v3/mail/send",
            "blockers": [],
        }
        reservation = reserve_live_automation_execution(reservation_plan)
        assert reservation["reserved"] is True
        assert reservation["reconciliation_required"] is True
        duplicate_reservation = reserve_live_automation_execution(reservation_plan)
        assert duplicate_reservation["duplicate"] is True
        assert automation_execution_duplicate_status("smoke-key-1")["reconciliation_required"] is True
        try:
            transition_automation_execution("smoke-key-1", "sent")
            raise AssertionError("Sent transition must require provider evidence")
        except ValueError:
            pass
        try:
            transition_automation_execution("smoke-key-1", "failed")
            raise AssertionError("Failed transition must require a reason")
        except ValueError:
            pass
        transitioned = transition_automation_execution(
            "smoke-key-1", "failed", resolution_reason="provider request was not attempted"
        )
        assert transitioned["outcome"] == "failed"
        assert transitioned["resolution_reason"] == "provider request was not attempted"
        assert transitioned["updated_at"]
        assert transitioned["resolved_at"]
        assert transitioned["resolved_at"] >= transitioned["recorded_at"]
        assert automation_execution_duplicate_status("smoke-key-1")["reconciliation_required"] is False
        retry = automation_retry_decision("smoke-key-1")
        assert retry["retry_allowed"] is True
        assert retry["next_attempt"] == 2
        assert retry["next_idempotency_key"] != "smoke-key-1"
        assert automation_attempt_idempotency_key("smoke-key-1", 1) == "smoke-key-1"
        assert automation_attempt_idempotency_key("smoke-key-1", 2) == retry["next_idempotency_key"]
        retry_without_consent = validate_automation_retry_request({
            "base_idempotency_key": "smoke-key-1",
            "permission_review_current": True,
        })
        assert retry_without_consent["allowed"] is False
        assert "explicit_retry_authorization_required" in retry_without_consent["blockers"]
        retry_with_consent = validate_automation_retry_request({
            "base_idempotency_key": "smoke-key-1",
            "user_authorized_retry": True,
            "permission_review_current": True,
        })
        assert retry_with_consent["allowed"] is True


        old_record = dict(ledger_record)
        old_record["idempotency_key"] = "smoke-old-key"
        old_record["ledger_key"] = "smoke-old-key"
        old_record["recorded_at"] = "2020-01-01T00:00:00+00:00"
        assert persist_automation_execution_record(old_record)["created"] is True
        prune_result = prune_automation_execution_ledger(7)
        assert prune_result["retention_days"] == 7
        assert prune_result["deleted"] >= 1
        assert find_automation_execution_record("smoke-old-key") is None
        with automation_ledger_connection() as connection:
            connection.execute(
                """INSERT INTO automation_rate_events
                   (mechanism, idempotency_key, consumed_at) VALUES (?, ?, ?)""",
                ("sendgrid_mail_v3", "quota-stale", "2020-01-01T00:00:00+00:00"),
            )
        quota_before = automation_rate_limit_status("sendgrid_mail_v3")
        assert quota_before["limit_per_hour"] == 20
        quota_consumed = consume_automation_rate_limit("sendgrid_mail_v3", "quota-smoke-1")
        assert quota_consumed["consumed"] is True
        assert quota_consumed["pruned_events"] >= 1
        with automation_ledger_connection() as connection:
            assert connection.execute(
                "SELECT 1 FROM automation_rate_events WHERE idempotency_key = ?",
                ("quota-stale",),
            ).fetchone() is None
        quota_duplicate = consume_automation_rate_limit("sendgrid_mail_v3", "quota-smoke-1")
        assert quota_duplicate["consumed"] is False
        assert automation_rate_limit_status("sendgrid_mail_v3")["used_last_hour"] == 1
        assert quota_consumed["reason"] == "quota_consumed"
        assert quota_duplicate["reason"] == "quota_already_consumed_for_execution"
        with automation_ledger_connection() as connection:
            now_iso = datetime.now(timezone.utc).isoformat()
            connection.executemany(
                """INSERT INTO automation_rate_events
                   (mechanism, idempotency_key, consumed_at) VALUES (?, ?, ?)""",
                [("sendgrid_mail_v3", "quota-fill-" + str(i), now_iso) for i in range(2, 21)],
            )
        quota_exhausted = consume_automation_rate_limit("sendgrid_mail_v3", "quota-over-limit")
        assert quota_exhausted["consumed"] is False
        assert quota_exhausted["allowed"] is False
        assert quota_exhausted["reason"] == "hourly_rate_limit_exhausted"
        assert automation_rate_limit_status("sendgrid_mail_v3")["used_last_hour"] == 20
        duplicate_status = automation_execution_duplicate_status("smoke-key-1")
        assert duplicate_status["duplicate"] is True
        assert duplicate_status["previous_outcome"] == "failed"
        assert duplicate_status["resolution_reason"] == "provider request was not attempted"
        assert automation_execution_duplicate_status("never-recorded")["duplicate"] is False
    finally:
        crowdfunding_server.AUTOMATION_LEDGER_PATH = original_ledger_path
    simulated_transport = execute_sendgrid_transport(disabled_plan, simulate=True)
    assert simulated_transport["status"] == "simulated"
    assert simulated_transport["sent"] is False
    assert simulated_transport["network_io"] is False
    assert simulated_transport["record"]["outcome"] == "simulated"
    assert simulated_transport["record"]["execution_mode"] == "simulation"
    blocked_transport = execute_sendgrid_transport(disabled_plan, simulate=False)
    assert blocked_transport["status"] == "blocked"
    assert blocked_transport["sent"] is False
    assert blocked_transport["network_io"] is False
    assert blocked_transport["record"]["outcome"] == "blocked"
    diagnostics = redact_automation_plan(disabled_plan)
    assert diagnostics["sent"] is False
    assert diagnostics["payload_present"] is False
    assert "payload" not in diagnostics
    assert "content" not in diagnostics
    payload = build_sendgrid_mail_v3_payload(
        "recipient@example.com", "sender@example.com", "Campaign introduction", "Hello", "reply@example.com"
    )
    assert payload["personalizations"][0]["to"][0]["email"] == "recipient@example.com"
    assert payload["from"]["email"] == "sender@example.com"
    assert payload["content"][0]["type"] == "text/plain"
    assert payload["reply_to"]["email"] == "reply@example.com"
    rate_contract = automation_rate_limit_contract("untrusted-generic-form")
    assert rate_contract["registered"] is False
    assert rate_contract["limit_per_hour"] is None
    assert rate_contract["enforcement_required"] is False
    key_one = automation_idempotency_key("workspace-1", "lead-1", "official-api", "https://example.com/submit/")
    key_two = automation_idempotency_key("workspace-1", "lead-1", "OFFICIAL-API", "https://example.com/submit")
    assert key_one == key_two
    assert len(key_one) == 64
    try:
        automation_idempotency_key("", "lead-1", "official-api", "https://example.com/submit")
        raise AssertionError("Missing workspace should fail idempotency key generation")
    except ValueError:
        pass
    execution_check = validate_automation_execution_request({"lead": automation_candidate})
    assert execution_check["allowed"] is False
    assert execution_check["sent"] is False
    assert "execution_check_authorization_required" in execution_check["blockers"]
    assert "current_permission_review_required" in execution_check["blockers"]
    assert "deduplication_clearance_required" in execution_check["blockers"]
    assert "verified_route_required" in execution_check["blockers"]
    assert "workspace_id_required" in execution_check["blockers"]
    assert execution_check["idempotency_key"] is None
    assert execution_check["rate_limit"]["registered"] is False

    draft_builder = crowdfunding_server.build_assisted_outreach_draft
    prepared_draft = draft_builder({"need": "Transportation", "location": "Austin, Texas", "campaign_url": "https://example.org/campaign", "lead": {"name": "Example Media", "type": "Local Media", "channel_rules": {"status": "rules_or_submission_route_found"}}})
    assert prepared_draft["status"] == "success"
    assert prepared_draft["send_enabled"] is False
    assert "https://example.org/campaign" in prepared_draft["draft"]["body"]
    assert prepared_draft["draft"]["subject"].startswith("Community story tip:")
    assert "not representing your organization" in prepared_draft["draft"]["body"]
    podcast_draft = draft_builder({"need": "Transportation", "location": "Austin, Texas", "goal": 10000, "campaign_summary": "Seeking reliable transportation to support stable work.", "lead": {"name": "Example Podcast", "type": "Creators & Podcasts", "channel_rules": {"status": "rules_or_submission_route_found"}}})
    assert podcast_draft["draft"]["subject"].startswith("Possible community story or interview:")
    assert "$10,000" in podcast_draft["draft"]["body"]
    assert "Seeking reliable transportation" in podcast_draft["draft"]["body"]
    directory_draft = draft_builder({"need": "Housing Assistance", "lead": {"name": "Example Directory", "type": "Directories & Newsletters", "channel_rules": {"status": "rules_or_submission_route_found"}}})
    assert directory_draft["draft"]["subject"].startswith("Resource submission for consideration:")
    blocked_draft = draft_builder({"need": "Transportation", "lead": {"name": "Restricted Forum", "type": "Community Forums", "channel_rules": {"status": "restriction_detected"}}})
    assert blocked_draft["status"] == "blocked"
    assert blocked_draft["automatic_distribution"] is False

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
