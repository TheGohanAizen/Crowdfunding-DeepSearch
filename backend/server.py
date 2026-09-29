import json
import os
import re
import hashlib
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


def stable_id(prefix, *parts):
    """Create a deterministic, non-secret identifier for tracking records."""
    payload = "\x1f".join(str(part or "").strip().lower() for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return prefix + "_" + digest


def campaign_tracking_id(need, location, goal):
    return stable_id("campaign", need, location, goal)


def opportunity_tracking_id(candidate):
    return stable_id("opportunity", candidate.get("url"), candidate.get("name"), candidate.get("type"))



def parse_location_parts(location):
    """Parse a simple comma-delimited campaign location without external geocoding."""
    raw = str(location or "").strip()
    if not raw or raw == "Location not specified":
        return {"raw": raw, "city": "", "region": "", "country": ""}
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if len(parts) >= 3:
        return {"raw": raw, "city": parts[0], "region": parts[1], "country": ", ".join(parts[2:])}
    if len(parts) == 2:
        return {"raw": raw, "city": parts[0], "region": "", "country": parts[1]}
    return {"raw": raw, "city": parts[0], "region": "", "country": ""}


def geographic_terms(location):
    """Return bounded geographic terms for progressive discovery."""
    parts = parse_location_parts(location)
    local = ", ".join(x for x in (parts["city"], parts["region"], parts["country"]) if x)
    state = ", ".join(x for x in (parts["region"], parts["country"]) if x) or local
    national = parts["country"] or state or local
    return {"local": local, "state": state, "national": national, "worldwide": "international worldwide"}

def build_discovery_queries(need, location, scope="local"):
    """Build bounded geographic search lanes without claiming eligibility."""
    location_term = location if location and location != "Location not specified" else ""
    geo_terms = geographic_terms(location_term)
    scope = str(scope or "local").strip().lower()
    allowed_scopes = {"local", "state", "national", "worldwide", "automatic"}
    if scope not in allowed_scopes:
        scope = "local"

    lanes = {
        "Transportation": [
            ("Vehicle Assistance", "vehicle assistance donated car reliable transportation nonprofit"),
            ("Transportation Assistance", "transportation assistance emergency financial assistance nonprofit"),
            ("Community Action", "community action transportation assistance"),
            ("Employment Mobility", "employment transportation mobility vehicle repair assistance"),
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

    for index, (lane, terms) in enumerate(selected):
        if scope == "local":
            geography = geo_terms["local"]
            geo_stage = "local"
        elif scope == "state":
            geography = (geo_terms["state"] + " statewide").strip()
            geo_stage = "state"
        elif scope == "national":
            geography = (geo_terms["national"] + " national nationwide serves applicants").strip()
            geo_stage = "national"
        elif scope == "worldwide":
            geography = "international worldwide"
            geo_stage = "worldwide"
        else:
            # Automatic mode uses the bounded lane budget to sample progressively
            # broader service areas. Later stages can deepen any promising tier.
            stages = [
                ("local", geo_terms["local"]),
                ("state", (geo_terms["state"] + " statewide").strip()),
                ("national", (geo_terms["national"] + " national nationwide serves applicants").strip()),
                ("worldwide", "international worldwide"),
            ]
            geo_stage, geography = stages[min(index, len(stages) - 1)]

        query = " ".join(part for part in [geography, terms] if part).strip()
        queries.append({
            "lane": lane,
            "query": query,
            "geographic_stage": geo_stage,
            "search_url": "https://www.google.com/search?q=" + quote_plus(query),
            "status": "ready_for_provider"
        })

    return queries



def build_audience_queries(need, location, scope="automatic"):
    """Build public-web audience discovery lanes without automating posting."""
    geo_terms = geographic_terms(location)
    scope = str(scope or "automatic").strip().lower()
    allowed_scopes = {"local", "state", "national", "worldwide", "automatic"}
    if scope not in allowed_scopes:
        scope = "automatic"

    lanes = [
        ("Community Forums", "public community forum discussion support resources"),
        ("Local Media", "local news human interest community assistance story tips"),
        ("Creators & Podcasts", "podcast creator community stories assistance interview"),
        ("Directories & Newsletters", "community newsletter resource directory public submissions"),
    ]
    stages = [
        ("local", geo_terms["local"]),
        ("state", (geo_terms["state"] + " statewide").strip()),
        ("national", (geo_terms["national"] + " national").strip()),
        ("worldwide", "international worldwide"),
    ]
    queries = []
    for index, (lane, terms) in enumerate(lanes):
        if scope == "automatic":
            geo_stage, geography = stages[index]
        else:
            geo_stage = scope
            geography = dict(stages).get(scope, geo_terms["local"])
        topic = str(need or "financial assistance").strip()
        query = " ".join(part for part in [geography, topic, terms] if part).strip()
        queries.append({
            "lane": lane,
            "query": query,
            "geographic_stage": geo_stage,
            "search_url": "https://www.google.com/search?q=" + quote_plus(query),
            "status": "ready_for_provider",
            "discovery_kind": "audience",
            "action_mode": "review_required"
        })
    return queries


def audience_channel_tracking_id(candidate):
    return stable_id("audience", candidate.get("url"), candidate.get("name"), candidate.get("type"))


def normalize_candidate(item, lane, query, source="web-search", geographic_stage="unspecified"):
    """Normalize provider output into the internal candidate schema."""
    url = item.get("link") or item.get("url") or ""
    title = item.get("title") or "Untitled result"
    snippet = item.get("snippet") or item.get("description") or item.get("content") or ""
    return {
        "tracking_id": stable_id("opportunity", url, title, lane),
        "name": title,
        "type": lane,
        "url": url,
        "snippet": snippet,
        "source": source,
        "source_query": query,
        "geographic_stage": geographic_stage,
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
                normalize_candidate(item, plan["lane"], plan["query"], source="google-custom-search", geographic_stage=plan.get("geographic_stage", "unspecified"))
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
                normalize_candidate(item, plan["lane"], plan["query"], source="brave-search", geographic_stage=plan.get("geographic_stage", "unspecified"))
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
                normalize_candidate(item, plan["lane"], plan["query"], source="tavily-search", geographic_stage=plan.get("geographic_stage", "unspecified"))
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
            "service_area_language_found": False,
            "service_area_evidence": [],
            "page_text_excerpt": "",
            "application_links": [],
            "contact_links": [],
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
    service_area_terms = (
        "service area", "areas we serve", "serving residents", "serves residents",
        "available statewide", "statewide program", "nationwide",
        "available nationwide", "throughout the united states",
        "international applicants", "available worldwide"
    )

    evidence_terms = [term for term in program_terms if term in text]
    application_language = any(term in text for term in application_terms)
    eligibility_language = any(term in text for term in eligibility_terms)
    contact_language = any(term in text for term in contact_terms)
    service_area_evidence = [term for term in service_area_terms if term in text]

    def safe_source_link(href):
        """Keep only HTTP(S) links on the same source host before showing them to users."""
        try:
            absolute = urljoin(base_url, href)
            parsed_link = urlparse(absolute)
            parsed_base = urlparse(base_url)
        except Exception:
            return ""
        if parsed_link.scheme not in {"http", "https"} or not parsed_link.hostname:
            return ""
        if not parsed_base.hostname or parsed_link.hostname.lower() != parsed_base.hostname.lower():
            return ""
        return absolute

    application_links = []
    contact_links = []
    for href in parser.links:
        lowered = href.lower()
        absolute = safe_source_link(href)
        if not absolute:
            continue
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
        "service_area_language_found": bool(service_area_evidence),
        "service_area_evidence": service_area_evidence[:6],
        "page_text_excerpt": text[:5000],
        "application_links": application_links,
        "contact_links": contact_links,
        "evidence_terms": evidence_terms[:8],
        "page_base_url": base_url
    }

def classify_service_area(signals, location):
    """Compare source service-area language with structured campaign geography."""
    evidence = list(signals.get("service_area_evidence") or [])
    if not evidence:
        return "not_confirmed", []

    parts = parse_location_parts(location)
    page_text = " ".join([
        signals.get("page_title", ""),
        signals.get("page_text_excerpt", ""),
        " ".join(evidence),
    ]).lower()
    city = parts.get("city", "").lower()
    region = parts.get("region", "").lower()
    country = parts.get("country", "").lower()
    matched = []
    for label, value in (("city", city), ("region", region), ("country", country)):
        if value and value in page_text:
            matched.append(label + ":" + value)

    local_language = any(term in evidence for term in ("service area", "areas we serve", "serving residents", "serves residents"))
    statewide_language = any(term in evidence for term in ("available statewide", "statewide program"))
    national_language = any(term in evidence for term in ("nationwide", "available nationwide", "throughout the united states"))
    worldwide_language = any(term in evidence for term in ("international applicants", "available worldwide"))

    if city and "city:" + city in matched and local_language:
        return "confirmed", (evidence + matched)[:8]
    if region and "region:" + region in matched and (local_language or statewide_language):
        return "confirmed", (evidence + matched)[:8]
    if country and "country:" + country in matched and national_language:
        return "confirmed", (evidence + matched)[:8]
    if worldwide_language:
        return "possible", evidence[:6]
    if statewide_language or national_language or local_language:
        return "possible", (evidence + matched)[:8]
    return "not_confirmed", evidence[:6]

def enrich_with_source_checks(candidates, location="", max_candidates=None):
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
            service_status, service_evidence = classify_service_area(source_check, location)
            item["service_area_status"] = service_status
            item["service_area_evidence"] = service_evidence
            source_check["service_area_status"] = service_status
            # Geographic coverage improves ranking only when the source supports it.
            # This is not an eligibility determination.
            if service_status == "confirmed":
                item["verification_score"] = min(100, item.get("verification_score", 0) + 12)
                item.setdefault("verification_signals", []).append("service_area_confirmed")
            elif service_status == "possible":
                item["verification_score"] = min(100, item.get("verification_score", 0) + 4)
                item.setdefault("verification_signals", []).append("service_area_possible")
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

        application_links = source_check.get("application_links") or []
        contact_links = source_check.get("contact_links") or []
        outreach_readiness = {
            "source_reachable": bool(source_check.get("reachable")),
            "service_area_confirmed": item.get("service_area_status") == "confirmed",
            "application_route_found": bool(application_links),
            "contact_route_found": bool(contact_links),
            "eligibility_requires_confirmation": True,
            "safe_for_automatic_submission": False
        }
        if application_links and item.get("service_area_status") == "confirmed":
            outreach_readiness["status"] = "review_application"
        elif application_links or contact_links:
            outreach_readiness["status"] = "review_route"
        else:
            outreach_readiness["status"] = "verify_source"
        item["outreach_readiness"] = outreach_readiness
        if application_links:
            item["recommended_next_action"] = {
                "type": "review_application",
                "label": "Review application route",
                "url": application_links[0],
                "automation_ready": False,
                "requires_user_review": True
            }
        elif contact_links:
            item["recommended_next_action"] = {
                "type": "review_contact",
                "label": "Review contact route",
                "url": contact_links[0],
                "automation_ready": False,
                "requires_user_review": True
            }
        else:
            item["recommended_next_action"] = {
                "type": "verify_source",
                "label": "Verify source before outreach",
                "url": item.get("url", ""),
                "automation_ready": False,
                "requires_user_review": True
            }

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
    scope = str(data.get("scope") or "local").strip().lower()
    allowed_scopes = {"local", "state", "national", "worldwide", "automatic"}
    if scope not in allowed_scopes:
        raise ValueError("Unsupported search scope.")

    if len(need) > MAX_QUERY_LENGTH or len(location) > MAX_QUERY_LENGTH:
        raise ValueError("Search fields are too long.")
    if goal not in (None, ""):
        try:
            goal = float(goal)
        except (TypeError, ValueError):
            raise ValueError("Goal must be numeric.")
        if goal < 0 or goal > 1000000000:
            raise ValueError("Goal is outside the supported range.")

    search_plan = build_discovery_queries(need, location, scope)
    search_plan = search_plan[:MAX_SEARCH_LANES]
    retrieval = retrieve_candidates(search_plan, per_lane=MAX_RESULTS_PER_LANE)
    verified_candidates = verify_candidates(retrieval["candidates"], need, location)
    source_checked_candidates = enrich_with_source_checks(verified_candidates, location=location)[:MAX_DISCOVERY_RESULTS]

    for item in source_checked_candidates:
        score = item.get("verification_score", 0)
        source_check = item.get("source_check", {})
        if source_check.get("checked") and source_check.get("reachable") and score >= 65:
            item["review_status"] = "needs_review"
        elif score >= 35:
            item["review_status"] = "unverified"
        else:
            item["review_status"] = "low_relevance"

    campaign_id = campaign_tracking_id(need, location, goal)
    for item in source_checked_candidates:
        item.setdefault("tracking_id", opportunity_tracking_id(item))

    return {
        "status": "success",
        "campaign_tracking_id": campaign_id,
        "query": {"need": need, "location": location, "goal": goal, "scope": scope},
        "discovery": {
            "stage": "source-verification-v1",
            "live_search": retrieval["configured"],
            "verification_enabled": True,
            "provider": retrieval["provider"],
            "result_limit": MAX_DISCOVERY_RESULTS,
            "search_lane_limit": MAX_SEARCH_LANES,
            "results_per_lane": MAX_RESULTS_PER_LANE,
            "geographic_scope": scope
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




AUDIENCE_CHANNEL_RULE_TERMS = {
    "submission_allowed": ("submit", "submission", "send us", "story tip", "pitch us", "contact us"),
    "rules_present": ("community rules", "posting rules", "submission guidelines", "editorial guidelines", "terms of use"),
    "fundraising_restricted": ("no fundraising", "no crowdfunding", "no solicitation", "no self promotion", "no self-promotion"),
}

def normalize_audience_candidate(item, lane, query, source="web-search", geographic_stage="unspecified"):
    """Normalize an audience lead separately from institutional opportunities."""
    candidate = normalize_candidate(item, lane, query, source=source, geographic_stage=geographic_stage)
    candidate["tracking_id"] = audience_channel_tracking_id(candidate)
    candidate["discovery_kind"] = "audience"
    candidate["channel_type"] = lane
    candidate["review_status"] = "needs_review"
    candidate["automatic_distribution"] = False
    return candidate

def audience_relevance_signals(candidate, need, location):
    """Rank audience leads as discovery signals, never as permission to post."""
    text = " ".join([
        candidate.get("name", ""), candidate.get("snippet", ""),
        candidate.get("channel_type", candidate.get("type", ""))
    ]).lower()
    score = 0
    signals = []
    topic_terms = {
        "Transportation": ("transportation", "vehicle", "car", "mobility", "commute"),
        "Medical Assistance": ("medical", "health", "patient", "care"),
        "Housing Assistance": ("housing", "rent", "shelter", "community"),
        "Education Assistance": ("education", "school", "student", "scholarship"),
        "Community / Nonprofit Funding": ("nonprofit", "community", "fundraising", "charity"),
        "General Financial Assistance": ("assistance", "community", "financial", "support"),
    }
    hits = [term for term in topic_terms.get(need, ()) if term in text]
    if hits:
        score += min(40, len(hits) * 10)
        signals.append("topic_match:" + ",".join(hits[:4]))
    location_tokens = [x.lower() for x in re.findall(r"[A-Za-z]{3,}", location or "") if x.lower() not in {"united", "states"}]
    if location_tokens and any(token in text for token in location_tokens):
        score += 25
        signals.append("location_match")
    channel = candidate.get("channel_type", candidate.get("type", ""))
    if channel in {"Local Media", "Community Forums", "Creators & Podcasts", "Directories & Newsletters"}:
        score += 20
        signals.append("supported_channel")
    parsed = urlparse(candidate.get("url", ""))
    if parsed.scheme == "https" and parsed.hostname:
        score += 10
        signals.append("https_source")
    checked = dict(candidate)
    checked["audience_relevance_score"] = min(score, 100)
    checked["audience_relevance_signals"] = signals
    checked["permission_verified"] = False
    checked["channel_rules_status"] = "not_checked"
    checked["automatic_distribution"] = False
    return checked

def detect_audience_channel_rules(signals):
    """Classify source text conservatively; restrictions always require human review."""
    text = " ".join([
        signals.get("page_title", ""),
        signals.get("page_text_excerpt", ""),
    ]).lower()
    found = {key: [term for term in terms if term in text] for key, terms in AUDIENCE_CHANNEL_RULE_TERMS.items()}
    if found["fundraising_restricted"]:
        status = "restriction_detected"
    elif found["rules_present"] or found["submission_allowed"]:
        status = "rules_or_submission_route_found"
    else:
        status = "not_found"
    return {
        "status": status,
        "evidence": {key: values[:5] for key, values in found.items() if values},
        "permission_verified": False,
        "automatic_distribution": False,
        "requires_review": True
    }




def retrieve_audience_candidates(search_plan, per_lane=2):
    """Reuse the configured provider under a smaller audience-specific budget."""
    bounded_plan = list(search_plan)[:MAX_SEARCH_LANES]
    retrieval = retrieve_candidates(bounded_plan, per_lane=min(max(int(per_lane), 1), 2))
    normalized = []
    plan_by_lane = {item["lane"]: item for item in bounded_plan}
    for item in retrieval.get("candidates", [])[:MAX_DISCOVERY_RESULTS]:
        lane = item.get("type") or "Community Forums"
        plan = plan_by_lane.get(lane, {})
        raw = {
            "url": item.get("url", ""),
            "title": item.get("name", ""),
            "snippet": item.get("snippet", ""),
        }
        normalized.append(normalize_audience_candidate(
            raw, lane, plan.get("query", item.get("source_query", "")),
            source=item.get("source", retrieval.get("provider", "web-search")),
            geographic_stage=item.get("geographic_stage", plan.get("geographic_stage", "unspecified"))
        ))
    retrieval["candidates"] = normalized
    return retrieval

def build_audience_discovery_response(data):
    """Run bounded Audience DeepSearch retrieval; all resulting actions require review."""
    base = build_audience_plan_response(data)
    retrieval = retrieve_audience_candidates(base["search_plan"], per_lane=2)
    results = [
        audience_relevance_signals(item, base["query"]["need"], base["query"]["location"])
        for item in retrieval.get("candidates", [])
    ]
    results.sort(key=lambda item: item.get("audience_relevance_score", 0), reverse=True)
    base["audience"].update({
        "stage": "audience-discovery-v1",
        "live_search": retrieval.get("configured", False),
        "provider": retrieval.get("provider", "none"),
        "result_limit": MAX_DISCOVERY_RESULTS,
        "automatic_distribution": False,
    })
    base["provider_status"] = {
        "provider": retrieval.get("provider", "none"),
        "configured": retrieval.get("configured", False),
        "message": retrieval.get("message", ""),
        "errors": retrieval.get("errors", []),
        "attempts": retrieval.get("attempts", []),
    }
    base["results"] = results
    return base


def build_audience_preview_response(data):
    """Normalize supplied search-result-shaped leads without making a paid provider call."""
    base = build_audience_plan_response(data)
    raw = data.get("candidates") or []
    if not isinstance(raw, list):
        raise ValueError("Audience candidates must be a list.")
    raw = raw[:MAX_DISCOVERY_RESULTS]
    plan_by_lane = {item["lane"]: item for item in base["search_plan"]}
    results = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        lane = str(item.get("lane") or item.get("channel_type") or "Community Forums")
        plan = plan_by_lane.get(lane, {"query": "", "geographic_stage": "unspecified"})
        normalized = normalize_audience_candidate(
            item, lane, plan.get("query", ""), source=str(item.get("source") or "preview"),
            geographic_stage=str(item.get("geographic_stage") or plan.get("geographic_stage", "unspecified"))
        )
        results.append(audience_relevance_signals(normalized, base["query"]["need"], base["query"]["location"]))
    results.sort(key=lambda item: item.get("audience_relevance_score", 0), reverse=True)
    base["audience"]["stage"] = "audience-normalization-v1"
    base["results"] = results
    return base


def build_audience_plan_response(data):
    """Return a no-credit Audience DeepSearch plan for review before live retrieval."""
    if not isinstance(data, dict):
        raise ValueError("A JSON request body is required.")
    need = str(data.get("need") or "General Financial Assistance").strip()
    location = str(data.get("location") or "").strip()
    scope = str(data.get("scope") or "automatic").strip().lower()
    if len(need) > MAX_QUERY_LENGTH or len(location) > MAX_QUERY_LENGTH:
        raise ValueError("Audience search fields are too long.")
    if scope not in {"local", "state", "national", "worldwide", "automatic"}:
        raise ValueError("Unsupported geographic scope.")
    plan = build_audience_queries(need, location, scope)
    return {
        "status": "success",
        "query": {"need": need, "location": location, "scope": scope},
        "audience": {
            "stage": "audience-planning-v1",
            "live_search": False,
            "credits_used": 0,
            "geographic_scope": scope,
            "automatic_distribution": False
        },
        "safety_policy": {
            "review_required": True,
            "automatic_posting": False,
            "fake_accounts": False,
            "captcha_bypass": False,
            "note": "Discovery identifies possible public channels. Permission, channel rules, and relevance must be reviewed before outreach."
        },
        "search_plan": plan
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

    def deployment_info():
        """Expose non-secret deployment identity for automated production verification."""
        return {
            "commit": os.environ.get("RENDER_GIT_COMMIT", "unknown"),
            "service_id": os.environ.get("RENDER_SERVICE_ID", "unknown"),
        }

    @app.get("/")
    def root():
        return jsonify({
            "service": "Crowdfunding DeepSearch Backend",
            "status": "ok",
            "version": "1.13",
            "health": "/api/health",
            "deployment": "/api/deployment",
            "discovery": "/api/discover",
            "audience_plan": "/api/audience/plan"
        })

    @app.get("/api/deployment")
    def deployment():
        info = deployment_info()
        return jsonify({
            "status": "ok",
            "service": "Crowdfunding DeepSearch Backend",
            "commit": info["commit"],
            "service_id": info["service_id"]
        })

    @app.get("/api/health")
    def health():
        return jsonify({
            "status": "ok",
            "service": "Crowdfunding DeepSearch Backend",
            "version": "1.13",
            "deployment_commit": deployment_info()["commit"]
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

    @app.route("/api/audience/discover", methods=["POST", "OPTIONS"])
    def audience_discover():
        if request.method == "OPTIONS":
            return ("", 204)
        if request.content_length is not None and request.content_length > 65536:
            return jsonify({"status": "error", "message": "Request body is too large."}), 413
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "A JSON request body is required."}), 400
        try:
            return jsonify(build_audience_discovery_response(data))
        except ValueError as error:
            return jsonify({"status": "error", "message": str(error)}), 400
        except Exception:
            app.logger.exception("Audience discovery request failed")
            return jsonify({"status": "error", "message": "Audience discovery request failed."}), 500

    @app.route("/api/audience/preview", methods=["POST", "OPTIONS"])
    def audience_preview():
        if request.method == "OPTIONS":
            return ("", 204)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "A JSON request body is required."}), 400
        try:
            return jsonify(build_audience_preview_response(data))
        except ValueError as error:
            return jsonify({"status": "error", "message": str(error)}), 400
        except Exception:
            app.logger.exception("Audience preview request failed")
            return jsonify({"status": "error", "message": "Audience preview request failed."}), 500

    @app.route("/api/audience/plan", methods=["POST", "OPTIONS"])
    def audience_plan():
        if request.method == "OPTIONS":
            return ("", 204)
        if request.content_length is not None and request.content_length > 65536:
            return jsonify({"status": "error", "message": "Request body is too large."}), 413
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "A JSON request body is required."}), 400
        try:
            return jsonify(build_audience_plan_response(data))
        except ValueError as error:
            return jsonify({"status": "error", "message": str(error)}), 400
        except Exception:
            app.logger.exception("Audience planning request failed")
            return jsonify({"status": "error", "message": "Audience planning request failed."}), 500

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
