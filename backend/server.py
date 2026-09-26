import json
import os
import re
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlencode, urlparse, urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from urllib.error import HTTPError, URLError


def env_int(name, default, minimum, maximum):
    """Read a bounded integer environment setting without crashing at import time."""
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


HOST = os.environ.get("HOST", "0.0.0.0")
PORT = env_int("PORT", 8080, 1, 65535)
MAX_SOURCE_CHECKS = env_int("MAX_SOURCE_CHECKS", 8, 0, 20)
MAX_DISCOVERY_RESULTS = env_int("MAX_DISCOVERY_RESULTS", 25, 1, 100)
MAX_QUERY_LENGTH = env_int("MAX_QUERY_LENGTH", 500, 100, 2000)
MAX_SEARCH_LANES = env_int("MAX_SEARCH_LANES", 4, 1, 4)
MAX_RESULTS_PER_LANE = env_int("MAX_RESULTS_PER_LANE", 4, 1, 10)
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
ALLOWED_ORIGINS = {origin.strip() for origin in ALLOWED_ORIGIN.split(",") if origin.strip()}


def build_discovery_queries(need, location):
    """Build targeted search lanes without claiming that a result is verified."""
    location_term = location if location and location != "Location not specified" else ""
    lanes = {
        "Transportation": [
            ("Vehicle Assistance", "vehicle assistance donated car reliable transportation nonprofit"),
            ("Transportation Assistance", "transportation assistance emergency financial assistance nonprofit"),
            ("Community Action", "community action transportation assistance"),
            ("Local Charities", "charity transportation assistance vehicle repair assistance"),
        ],
        "Medical Assistance": [
            ("Patient Assistance", "patient financial assistance nonprofit"),
            ("Medical Relief", "medical bill assistance charity"),
            ("Community Health", "community health financial assistance"),
        ],
        "Housing Assistance": [
            ("Emergency Housing", "emergency housing rental assistance nonprofit"),
            ("Utility Assistance", "utility bill assistance community action"),
            ("Housing Stability", "housing stability emergency assistance charity"),
        ],
        "Education Assistance": [
            ("Education Assistance", "education financial assistance scholarship nonprofit"),
            ("Community Programs", "community education assistance program"),
        ],
        "Community / Nonprofit Funding": [
            ("Foundation Funding", "foundation grants nonprofit community program"),
            ("Corporate Giving", "corporate community giving program nonprofit"),
        ],
        "General Financial Assistance": [
            ("Emergency Assistance", "emergency financial assistance nonprofit"),
            ("Community Assistance", "community assistance charity financial help"),
        ],
    }

    selected = lanes.get(need, lanes["General Financial Assistance"])
    queries = []

    for lane, terms in selected:
        query = " ".join(part for part in [location_term, terms] if part).strip()
        queries.append({
            "lane": lane,
            "query": query,
            "search_url": "https://www.google.com/search?q=" + quote_plus(query),
            "status": "ready_for_provider"
        })

    return queries


def normalize_candidate(item, lane, query, source="web-search"):
    """Normalize provider output into the internal candidate schema."""
    url = item.get("link") or item.get("url") or ""
    title = item.get("title") or "Untitled result"
    snippet = item.get("snippet") or item.get("description") or item.get("content") or ""
    return {
        "name": title,
        "type": lane,
        "url": url,
        "snippet": snippet,
        "source": source,
        "source_query": query,
        "verification": "discovered_unverified",
        "score": None
    }


def deduplicate_candidates(candidates):
    seen = set()
    unique = []
    for item in candidates:
        key = (item.get("url") or item.get("name") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def retrieve_google_candidates(search_plan, per_lane=5):
    api_key = os.environ.get("GOOGLE_CSE_API_KEY")
    engine_id = os.environ.get("GOOGLE_CSE_ID")
    if not api_key or not engine_id:
        return {
            "provider": "google-custom-search",
            "configured": False,
            "message": "Google search credentials are not configured.",
            "errors": [],
            "candidates": []
        }

    candidates = []
    errors = []

    def fetch_lane(plan):
        params = urlencode({
            "key": api_key,
            "cx": engine_id,
            "q": plan["query"],
            "num": min(max(int(per_lane), 1), 10)
        })
        request = Request(
            "https://www.googleapis.com/customsearch/v1?" + params,
            headers={"User-Agent": "CrowdfundingDeepSearch/1.1"}
        )
        try:
            with urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
            lane_candidates = [
                normalize_candidate(item, plan["lane"], plan["query"], source="google-custom-search")
                for item in payload.get("items", [])
            ]
            return lane_candidates, None
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            return [], {"lane": plan["lane"], "error": str(error)}

    workers = max(1, min(len(search_plan), 4))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch_lane, plan) for plan in search_plan]
        for future in as_completed(futures):
            lane_candidates, error = future.result()
            candidates.extend(lane_candidates)
            if error:
                errors.append(error)

    return {
        "provider": "google-custom-search",
        "configured": True,
        "message": "Live retrieval completed." if not errors else "Live retrieval completed with some provider errors.",
        "errors": errors,
        "candidates": deduplicate_candidates(candidates)
    }


def retrieve_brave_candidates(search_plan, per_lane=5):
    """Retrieve broad-web candidates from Brave Search API."""
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY")
    if not api_key:
        return {
            "provider": "brave-search",
            "configured": False,
            "message": "Brave Search credentials are not configured.",
            "errors": [],
            "candidates": []
        }

    def fetch_lane(plan):
        params = urlencode({
            "q": plan["query"],
            "count": min(max(int(per_lane), 1), 20),
            "safesearch": "moderate"
        })
        request = Request(
            "https://api.search.brave.com/res/v1/web/search?" + params,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
                "User-Agent": "CrowdfundingDeepSearch/1.2"
            }
        )
        try:
            with urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
            lane_candidates = [
                normalize_candidate(item, plan["lane"], plan["query"], source="brave-search")
                for item in payload.get("web", {}).get("results", [])
            ]
            return lane_candidates, None
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            return [], {"lane": plan["lane"], "error": str(error)}

    candidates = []
    errors = []
    workers = max(1, min(len(search_plan), 4))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch_lane, plan) for plan in search_plan]
        for future in as_completed(futures):
            lane_candidates, error = future.result()
            candidates.extend(lane_candidates)
            if error:
                errors.append(error)

    return {
        "provider": "brave-search",
        "configured": True,
        "message": "Live retrieval completed." if not errors else "Live retrieval completed with some provider errors.",
        "errors": errors,
        "candidates": deduplicate_candidates(candidates)
    }



def retrieve_tavily_candidates(search_plan, per_lane=5):
    """Retrieve broad-web candidates from Tavily Search API."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return {
            "provider": "tavily-search",
            "configured": False,
            "message": "Tavily Search credentials are not configured.",
            "errors": [],
            "candidates": []
        }

    def fetch_lane(plan):
        body = json.dumps({
            "query": plan["query"],
            "search_depth": "basic",
            "max_results": min(max(int(per_lane), 1), 20),
            "topic": "general",
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "safe_search": True
        }).encode("utf-8")
        request = Request(
            "https://api.tavily.com/search",
            data=body,
            method="POST",
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "CrowdfundingDeepSearch/1.9"
            }
        )
        try:
            with urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
            lane_candidates = [
                normalize_candidate(item, plan["lane"], plan["query"], source="tavily-search")
                for item in payload.get("results", [])
            ]
            return lane_candidates, None
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            return [], {"lane": plan["lane"], "error": str(error)}

    candidates = []
    errors = []
    workers = max(1, min(len(search_plan), 4))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch_lane, plan) for plan in search_plan]
        for future in as_completed(futures):
            lane_candidates, error = future.result()
            candidates.extend(lane_candidates)
            if error:
                errors.append(error)

    return {
        "provider": "tavily-search",
        "configured": True,
        "message": "Live retrieval completed." if not errors else "Live retrieval completed with some provider errors.",
        "errors": errors,
        "candidates": deduplicate_candidates(candidates)
    }

def retrieve_candidates(search_plan, per_lane=5):
    """Provider router: keeps discovery independent from any single search service."""
    requested = os.environ.get("SEARCH_PROVIDER", "auto").strip().lower()
    providers = []

    if requested in {"auto", "tavily", "tavily-search"}:
        providers.append(retrieve_tavily_candidates)
    if requested in {"auto", "brave", "brave-search"}:
        providers.append(retrieve_brave_candidates)
    if requested in {"auto", "google", "google-custom-search"}:
        providers.append(retrieve_google_candidates)

    if not providers:
        return {
            "provider": requested,
            "configured": False,
            "message": "Requested search provider is not supported by this build.",
            "errors": [],
            "candidates": []
        }

    attempts = []
    for provider in providers:
        result = provider(search_plan, per_lane=per_lane)
        attempts.append({
            "provider": result["provider"],
            "configured": result["configured"],
            "message": result["message"]
        })
        if result["configured"]:
            result["attempts"] = attempts
            return result

    return {
        "provider": "none",
        "configured": False,
        "message": "No configured live search provider is available.",
        "errors": [],
        "candidates": [],
        "attempts": attempts
    }

def is_public_hostname(hostname):
    """Reject loopback, private, link-local, multicast, reserved, and unspecified targets."""
    if not hostname or hostname.lower() in {"localhost", "localhost.localdomain"}:
        return False
    try:
        addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    if not addresses:
        return False
    for entry in addresses:
        try:
            address = ipaddress.ip_address(entry[4][0])
        except ValueError:
            return False
        if not address.is_global:
            return False
    return True


class NoRedirectHandler(HTTPRedirectHandler):
    """Expose redirects so each destination can be validated before it is followed."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


NO_REDIRECT_OPENER = build_opener(NoRedirectHandler())


def fetch_source_page(url, max_bytes=300000, max_redirects=4):
    """Fetch a public candidate page with bounded, redirect-aware SSRF protection."""
    current_url = url
    for _ in range(max_redirects + 1):
        parsed = urlparse(current_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return {"reachable": False, "error": "unsupported_url"}
        if parsed.username or parsed.password:
            return {"reachable": False, "error": "userinfo_not_allowed"}
        if not is_public_hostname(parsed.hostname):
            return {"reachable": False, "error": "non_public_target"}

        request = Request(
            current_url,
            headers={
                "User-Agent": "CrowdfundingDeepSearch/1.0 (+source verification)",
                "Accept": "text/html,application/xhtml+xml"
            }
        )
        try:
            with NO_REDIRECT_OPENER.open(request, timeout=6) as response:
                content_type = response.headers.get("Content-Type", "")
                if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                    return {
                        "reachable": True,
                        "final_url": current_url,
                        "content_type": content_type,
                        "html": "",
                        "last_checked": datetime.now(timezone.utc).isoformat()
                    }
                raw = response.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raw = raw[:max_bytes]
                html = raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
                return {
                    "reachable": True,
                    "final_url": current_url,
                    "content_type": content_type,
                    "html": html,
                    "last_checked": datetime.now(timezone.utc).isoformat()
                }
        except HTTPError as error:
            if error.code in {301, 302, 303, 307, 308}:
                location = error.headers.get("Location")
                if not location:
                    return {"reachable": False, "error": "redirect_without_location"}
                next_url = urljoin(current_url, location)
                next_parsed = urlparse(next_url)
                if next_parsed.scheme not in {"http", "https"} or not next_parsed.hostname:
                    return {"reachable": False, "error": "unsafe_redirect_target"}
                if next_parsed.username or next_parsed.password or not is_public_hostname(next_parsed.hostname):
                    return {"reachable": False, "error": "unsafe_redirect_target"}
                current_url = next_url
                continue
            return {
                "reachable": False,
                "error": str(error),
                "last_checked": datetime.now(timezone.utc).isoformat()
            }
        except (URLError, TimeoutError, ValueError) as error:
            return {
                "reachable": False,
                "error": str(error),
                "last_checked": datetime.now(timezone.utc).isoformat()
            }

    return {
        "reachable": False,
        "error": "too_many_redirects",
        "last_checked": datetime.now(timezone.utc).isoformat()
    }

class SourceSignalParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text_parts = []
        self.links = []
        self.title_parts = []
        self.in_title = False
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1
        if tag == "title":
            self.in_title = True
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.skip_depth:
            return
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        self.text_parts.append(cleaned)
        if self.in_title:
            self.title_parts.append(cleaned)


def extract_page_signals(html, base_url):
    """Extract conservative program/application evidence without submitting anything."""
    if not html:
        return {
            "page_title": "",
            "program_evidence_found": False,
            "application_route_found": False,
            "contact_route_found": False,
            "eligibility_language_found": False,
            "application_links": [],
            "evidence_terms": []
        }

    parser = SourceSignalParser()
    try:
        parser.feed(html)
    except Exception:
        pass

    text = " ".join(parser.text_parts).lower()
    page_title = " ".join(parser.title_parts).strip()[:300]
    program_terms = (
        "assistance program", "financial assistance", "emergency assistance",
        "transportation assistance", "vehicle assistance", "rental assistance",
        "patient assistance", "grant program", "get help"
    )
    application_terms = ("apply now", "apply online", "application", "request assistance")
    eligibility_terms = ("eligibility", "eligible", "requirements", "qualify", "qualification")
    contact_terms = ("contact us", "call us", "email us")

    evidence_terms = [term for term in program_terms if term in text]
    application_language = any(term in text for term in application_terms)
    eligibility_language = any(term in text for term in eligibility_terms)
    contact_language = any(term in text for term in contact_terms)

    application_links = []
    contact_links = []
    for href in parser.links:
        lowered = href.lower()
        absolute = urljoin(base_url, href)
        if any(term in lowered for term in ("apply", "application", "assistance", "get-help")):
            application_links.append(absolute)
        if "contact" in lowered:
            contact_links.append(absolute)

    application_links = list(dict.fromkeys(application_links))[:5]
    contact_links = list(dict.fromkeys(contact_links))[:5]

    return {
        "page_title": page_title,
        "program_evidence_found": bool(evidence_terms),
        "application_route_found": application_language or bool(application_links),
        "contact_route_found": contact_language or bool(contact_links),
        "eligibility_language_found": eligibility_language,
        "application_links": application_links,
        "contact_links": contact_links,
        "evidence_terms": evidence_terms[:8],
        "page_base_url": base_url
    }

def enrich_with_source_checks(candidates, max_candidates=None):
    """Visit a limited number of top candidates and record source-level signals."""
    if max_candidates is None:
        max_candidates = MAX_SOURCE_CHECKS
    enriched = [dict(candidate) for candidate in candidates]
    to_check = enriched[:max_candidates]

    for item in enriched[max_candidates:]:
        item["source_check"] = {"checked": False, "reason": "verification_limit"}
        item["verification_stage"] = "source_check_skipped"

    def check_item(item):
        page = fetch_source_page(item.get("url", ""))
        source_check = {
            "checked": True,
            "reachable": page.get("reachable", False),
            "last_checked": page.get("last_checked")
        }
        if page.get("reachable"):
            source_check.update(extract_page_signals(page.get("html", ""), page.get("final_url", item.get("url", ""))))
            if source_check.get("program_evidence_found"):
                item["verification_score"] = min(100, item.get("verification_score", 0) + 10)
            if source_check.get("application_route_found"):
                item["verification_score"] = min(100, item.get("verification_score", 0) + 10)
            if source_check.get("eligibility_language_found"):
                item["verification_score"] = min(100, item.get("verification_score", 0) + 5)
            if source_check.get("contact_route_found"):
                item["verification_score"] = min(100, item.get("verification_score", 0) + 5)
        else:
            source_check["error"] = page.get("error", "unreachable")
            item.setdefault("verification_concerns", []).append("source_page_unreachable")

        if source_check.get("application_route_found") or source_check.get("contact_route_found"):
            item["verification_stage"] = "application_or_contact_found"
        elif source_check.get("program_evidence_found"):
            item["verification_stage"] = "program_evidence_found"
        elif source_check.get("reachable"):
            item["verification_stage"] = "source_reachable"
        else:
            item["verification_stage"] = "discovered_unverified"

        # Recompute the human-readable verification label after source-level boosts.
        final_score = item.get("verification_score", 0)
        if final_score >= 65:
            item["verification"] = "promising_unverified"
        elif final_score >= 35:
            item["verification"] = "candidate_unverified"
        else:
            item["verification"] = "weak_unverified"

        item["source_check"] = source_check
        return item

    if to_check:
        workers = max(1, min(len(to_check), 4))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(check_item, item) for item in to_check]
            checked_items = [future.result() for future in as_completed(futures)]
        checked_by_url = {(item.get("url") or item.get("name")): item for item in checked_items}
        for index, item in enumerate(enriched[:max_candidates]):
            key = item.get("url") or item.get("name")
            if key in checked_by_url:
                enriched[index] = checked_by_url[key]

    enriched.sort(key=lambda item: item.get("verification_score", 0), reverse=True)
    return enriched


def verification_signals(candidate, need, location):
    """Apply conservative source and relevance checks without claiming eligibility."""
    url = candidate.get("url", "")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    text = " ".join([
        candidate.get("name", ""),
        candidate.get("snippet", ""),
        candidate.get("type", ""),
    ]).lower()

    signals = []
    concerns = []
    score = 0

    if parsed.scheme == "https" and host:
        score += 10
        signals.append("https_source")
    else:
        concerns.append("non_https_or_missing_host")

    if host.endswith(".gov"):
        score += 30
        signals.append("government_domain")
    elif host.endswith(".org"):
        score += 18
        signals.append("organization_domain")
    elif host.endswith(".edu"):
        score += 16
        signals.append("education_domain")

    assistance_terms = (
        "assistance", "apply", "application", "eligibility", "program",
        "help", "support", "relief", "grant", "transportation", "vehicle",
        "housing", "medical", "financial"
    )
    term_hits = sorted({term for term in assistance_terms if term in text or term in path})
    if term_hits:
        score += min(24, len(term_hits) * 4)
        signals.append("assistance_language:" + ",".join(term_hits[:6]))
    else:
        concerns.append("no_clear_assistance_language")

    need_terms = {
        "Transportation": ("transportation", "vehicle", "car", "repair", "mobility"),
        "Medical Assistance": ("medical", "patient", "health", "hospital", "treatment"),
        "Housing Assistance": ("housing", "rent", "rental", "utility", "shelter"),
        "Education Assistance": ("education", "school", "tuition", "scholarship", "student"),
        "Community / Nonprofit Funding": ("nonprofit", "community", "foundation", "grant"),
        "General Financial Assistance": ("financial", "emergency", "assistance", "relief"),
    }
    matched_need_terms = [term for term in need_terms.get(need, ()) if term in text]
    if matched_need_terms:
        score += min(24, len(matched_need_terms) * 8)
        signals.append("need_match:" + ",".join(matched_need_terms))
    else:
        concerns.append("weak_need_match")

    location_tokens = [
        token.strip().lower()
        for token in (location or "").replace(",", " ").split()
        if len(token.strip()) > 2 and token.lower() not in {"united", "states"}
    ]
    if location_tokens and any(token in text for token in location_tokens):
        score += 12
        signals.append("location_language_match")

    score = min(score, 100)
    if score >= 65:
        status = "promising_unverified"
    elif score >= 35:
        status = "candidate_unverified"
    else:
        status = "weak_unverified"

    checked = dict(candidate)
    checked["verification"] = status
    checked["verification_score"] = score
    checked["verification_signals"] = signals
    checked["verification_concerns"] = concerns
    checked["eligibility_verified"] = False
    checked["official_domain_signal"] = host.endswith((".gov", ".edu"))
    checked["official_source_verified"] = False
    return checked


def verify_candidates(candidates, need, location):
    checked = [verification_signals(item, need, location) for item in candidates]
    checked.sort(key=lambda item: item.get("verification_score", 0), reverse=True)
    return checked


def build_discovery_response(data):
    need = str(data.get("need") or "General Financial Assistance").strip()
    location = str(data.get("location") or "Location not specified").strip()
    goal = data.get("goal")

    if len(need) > MAX_QUERY_LENGTH or len(location) > MAX_QUERY_LENGTH:
        raise ValueError("Search fields are too long.")
    if goal not in (None, ""):
        try:
            goal = float(goal)
        except (TypeError, ValueError):
            raise ValueError("Goal must be numeric.")
        if goal < 0 or goal > 1000000000:
            raise ValueError("Goal is outside the supported range.")

    search_plan = build_discovery_queries(need, location)
    search_plan = search_plan[:MAX_SEARCH_LANES]
    retrieval = retrieve_candidates(search_plan, per_lane=MAX_RESULTS_PER_LANE)
    verified_candidates = verify_candidates(retrieval["candidates"], need, location)
    source_checked_candidates = enrich_with_source_checks(verified_candidates)[:MAX_DISCOVERY_RESULTS]

    for item in source_checked_candidates:
        score = item.get("verification_score", 0)
        source_check = item.get("source_check", {})
        if source_check.get("checked") and source_check.get("reachable") and score >= 65:
            item["review_status"] = "needs_review"
        elif score >= 35:
            item["review_status"] = "unverified"
        else:
            item["review_status"] = "low_relevance"

    return {
        "status": "success",
        "query": {"need": need, "location": location, "goal": goal},
        "discovery": {
            "stage": "source-verification-v1",
            "live_search": retrieval["configured"],
            "verification_enabled": True,
            "provider": retrieval["provider"],
            "result_limit": MAX_DISCOVERY_RESULTS,
            "search_lane_limit": MAX_SEARCH_LANES,
            "results_per_lane": MAX_RESULTS_PER_LANE
        },
        "search_plan": search_plan,
        "provider_status": {
            "configured": retrieval["configured"],
            "message": retrieval["message"],
            "errors": retrieval.get("errors", []),
            "attempts": retrieval.get("attempts", [])
        },
        "verification_policy": {
            "eligibility_claims": False,
            "automatic_official_source_claims": False,
            "note": "Scores are screening signals only; eligibility and program availability still require source-level verification."
        },
        "results": source_checked_candidates
    }


def create_app():
    from flask import Flask, jsonify, request
    app = Flask(__name__)

    @app.after_request
    def security_headers(response):
        origin = request.headers.get("Origin")
        if "*" in ALLOWED_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = "*"
        elif origin and origin in ALLOWED_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def root():
        return jsonify({
            "service": "Crowdfunding DeepSearch Backend",
            "status": "ok",
            "version": "1.11",
            "health": "/api/health",
            "discovery": "/api/discover"
        })

    @app.get("/api/health")
    def health():
        return jsonify({
            "status": "ok",
            "service": "Crowdfunding DeepSearch Backend",
            "version": "1.11"
        })

    @app.get("/api/test-discovery")
    def test_discovery():
        """Run one bounded browser-accessible live discovery smoke test."""
        result = build_discovery_response({
            "need": "Transportation",
            "location": "Austin, Texas",
            "goal": None
        })
        result["test_mode"] = True
        result["test_note"] = "Bounded live discovery smoke test; results remain unverified until source checks support them."
        return jsonify(result)

    @app.route("/api/discover", methods=["POST", "OPTIONS"])
    def discover():
        if request.method == "OPTIONS":
            return ("", 204)
        if request.content_length is not None and request.content_length > 65536:
            return jsonify({"status": "error", "message": "Request body is too large."}), 413
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "A JSON request body is required."}), 400
        try:
            return jsonify(build_discovery_response(data))
        except ValueError as error:
            return jsonify({"status": "error", "message": str(error)}), 400
        except Exception:
            app.logger.exception("Discovery request failed")
            return jsonify({"status": "error", "message": "Discovery request failed."}), 500

    return app


app = create_app()

def run_server():
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    run_server()
