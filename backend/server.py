import json
import os
import re
import hashlib
import hmac
import base64
import ipaddress
import socket
import sqlite3
try:
    import libsql
except ImportError:  # Local/test SQLite remains available without Turso.
    libsql = None
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlencode, urlparse, urljoin, parse_qsl
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from urllib.error import HTTPError, URLError
from cryptography.fernet import Fernet, InvalidToken


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
MAX_AUDIENCE_RULE_CHECKS = env_int("MAX_AUDIENCE_RULE_CHECKS", 4, 0, 12)
MAX_DISCOVERY_RESULTS = env_int("MAX_DISCOVERY_RESULTS", 25, 1, 100)
MAX_QUERY_LENGTH = env_int("MAX_QUERY_LENGTH", 500, 100, 2000)
MAX_SEARCH_LANES = env_int("MAX_SEARCH_LANES", 4, 1, 4)
MAX_AUDIENCE_SEARCH_QUERIES = env_int("MAX_AUDIENCE_SEARCH_QUERIES", 4, 1, 8)
MAX_RESULTS_PER_LANE = env_int("MAX_RESULTS_PER_LANE", 4, 1, 10)
AUTOMATION_STORAGE_BACKEND = os.environ.get("AUTOMATION_STORAGE_BACKEND", "sqlite").strip().lower()
AUTOMATION_LEDGER_PATH = os.environ.get("AUTOMATION_LEDGER_PATH", "/tmp/crowdfunding-deepsearch-automation.sqlite3")
AUTOMATION_LEDGER_RETENTION_DAYS = env_int("AUTOMATION_LEDGER_RETENTION_DAYS", 90, 7, 365)
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

    topic = str(need or "financial assistance").strip()
    topic_lower = topic.lower()
    if "transport" in topic_lower or "vehicle" in topic_lower:
        lanes = [
            ("Community Forums", "public community forum transportation vehicle assistance resources"),
            ("Local Media", "local news transportation hardship human interest story tips"),
            ("Creators & Podcasts", "podcast creator transportation hardship community assistance interview"),
            ("Directories & Newsletters", "transportation assistance community newsletter resource directory submissions"),
        ]
    elif "medical" in topic_lower or "health" in topic_lower:
        lanes = [
            ("Community Forums", "public community forum medical financial assistance patient support resources"),
            ("Local Media", "local news medical hardship human interest story tips"),
            ("Creators & Podcasts", "podcast creator patient medical hardship community stories interview"),
            ("Directories & Newsletters", "medical assistance patient support newsletter resource directory submissions"),
        ]
    elif "housing" in topic_lower or "rent" in topic_lower or "utility" in topic_lower:
        lanes = [
            ("Community Forums", "public community forum housing rental utility assistance resources"),
            ("Local Media", "local news housing hardship human interest story tips"),
            ("Creators & Podcasts", "podcast creator housing stability community assistance interview"),
            ("Directories & Newsletters", "housing assistance community newsletter resource directory submissions"),
        ]
    else:
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
    if scope == "automatic":
        selected_stages = stages
    else:
        selected_stages = [(scope, dict(stages).get(scope, geo_terms["local"]))]

    queries = []
    for geo_stage, geography in selected_stages:
        for lane, terms in lanes:
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
        if result["configured"] and result.get("candidates"):
            result["attempts"] = attempts
            return result
        if result["configured"]:
            attempts[-1]["fallback_reason"] = "configured_provider_returned_no_candidates"
            continue

    configured_attempts = [item for item in attempts if item.get("configured")]
    return {
        "provider": configured_attempts[-1]["provider"] if configured_attempts else "none",
        "configured": bool(configured_attempts),
        "message": "Configured providers returned no candidates." if configured_attempts else "No configured live search provider is available.",
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
        max_candidates = MAX_AUDIENCE_RULE_CHECKS
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
    campaign_url = str(data.get("campaign_url") or "").strip()
    campaign_summary = " ".join(str(data.get("campaign_summary") or "").split()).strip()[:1200]
    if campaign_url and not re.match(r"^https?://", campaign_url, re.I):
        raise ValueError("Campaign URL must use http or https.")
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
        "query": {"need": need, "location": location, "goal": goal, "scope": scope, "campaign_url": campaign_url, "campaign_summary": campaign_summary},
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
    intent_terms = ("submit", "submission", "story tip", "pitch", "contact", "community", "resource", "assistance", "support")
    intent_hits = [term for term in intent_terms if term in text]
    if intent_hits:
        score += min(15, len(intent_hits) * 5)
        signals.append("outreach_intent:" + ",".join(intent_hits[:3]))
    low_signal_terms = ("login", "sign in", "privacy policy", "cookie policy", "terms only")
    if any(term in text for term in low_signal_terms) and not hits and not intent_hits:
        score = max(0, score - 15)
        signals.append("low_signal_page")
    noise_terms = ("jobs", "careers", "employment opportunities", "advertise with us", "sponsored content", "press release distribution", "seo service", "marketing agency")
    noise_hits = [term for term in noise_terms if term in text]
    if noise_hits and not intent_hits:
        score = max(0, score - min(30, len(noise_hits) * 10))
        signals.append("commercial_or_irrelevant_noise:" + ",".join(noise_hits[:3]))
    crowdfunding_terms = ("crowdfunding", "fundraiser", "fundraising", "donation", "donate", "gofundme")
    crowdfunding_hits = [term for term in crowdfunding_terms if term in text]
    if crowdfunding_hits:
        score += min(15, len(crowdfunding_hits) * 5)
        signals.append("fundraising_context:" + ",".join(crowdfunding_hits[:3]))
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
    # Audience plans can contain the same lanes at several geographic stages.
    # Spread the bounded live-search budget across stages instead of always
    # spending it on the first (local) stage.
    full_plan = list(search_plan)
    budget = min(MAX_AUDIENCE_SEARCH_QUERIES, len(full_plan))
    if budget and len(full_plan) > budget:
        indexes = [round(i * (len(full_plan) - 1) / (budget - 1)) for i in range(budget)] if budget > 1 else [0]
        bounded_plan = [full_plan[index] for index in dict.fromkeys(indexes)]
    else:
        bounded_plan = full_plan
    retrieval = retrieve_candidates(bounded_plan, per_lane=min(max(int(per_lane), 1), 2))
    normalized = []
    plan_by_query = {
        (item.get("lane"), item.get("query")): item
        for item in bounded_plan
    }
    plan_by_lane = {}
    for plan_item in bounded_plan:
        plan_by_lane.setdefault(plan_item.get("lane"), plan_item)
    for item in retrieval.get("candidates", [])[:MAX_DISCOVERY_RESULTS]:
        lane = item.get("type") or "Community Forums"
        source_query = item.get("source_query", "")
        plan = plan_by_query.get((lane, source_query)) or plan_by_lane.get(lane, {})
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
    retrieval["candidate_count_before_dedup"] = len(normalized)
    return retrieval


def deduplicate_audience_candidates(candidates):
    """Merge repeated audience leads by normalized destination URL while preserving strongest evidence."""
    merged = {}
    order = []
    for candidate in candidates:
        item = dict(candidate)
        raw_url = (item.get("url") or "").strip()
        parsed = urlparse(raw_url)
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = (parsed.path or "/").rstrip("/") or "/"
        ignored_query_keys = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid", "ref", "source"}
        clean_query = tuple(sorted(
            (key_name.lower(), value)
            for key_name, value in parse_qsl(parsed.query, keep_blank_values=False)
            if key_name.lower() not in ignored_query_keys
        ))
        key = (host, path.lower(), clean_query) if host else ("", (item.get("name") or "").strip().lower())
        if key not in merged:
            item["discovered_in_lanes"] = [item.get("type")] if item.get("type") else []
            item["discovered_in_stages"] = [item.get("geographic_stage")] if item.get("geographic_stage") else []
            item["_discovery_count"] = 1
            merged[key] = item
            order.append(key)
            continue
        current = merged[key]
        current["_discovery_count"] = current.get("_discovery_count", 1) + 1
        lane = item.get("type")
        stage = item.get("geographic_stage")
        if lane and lane not in current["discovered_in_lanes"]:
            current["discovered_in_lanes"].append(lane)
        if stage and stage not in current["discovered_in_stages"]:
            current["discovered_in_stages"].append(stage)
        if len(item.get("snippet") or "") > len(current.get("snippet") or ""):
            current["snippet"] = item.get("snippet")
        if item.get("audience_relevance_score", 0) > current.get("audience_relevance_score", 0):
            preserved_lanes = current["discovered_in_lanes"]
            preserved_stages = current["discovered_in_stages"]
            current.update(item)
            current["discovered_in_lanes"] = preserved_lanes
            current["discovered_in_stages"] = preserved_stages
    results = [merged[key] for key in order]
    for item in results:
        discovery_count = item.pop("_discovery_count", 1)
        item["duplicate_discoveries_merged"] = max(0, discovery_count - 1)
    return results


def enrich_audience_with_rule_checks(candidates, max_candidates=None):
    """Inspect a bounded set of audience sources for rules/restrictions without posting."""
    if max_candidates is None:
        max_candidates = MAX_AUDIENCE_RULE_CHECKS
    enriched = [dict(item) for item in candidates]
    for item in enriched:
        item.setdefault("channel_rules", {
            "status": "not_checked",
            "evidence": {},
            "permission_verified": False,
            "automatic_distribution": False,
            "requires_review": True,
            "automation_eligibility": {
                "status": "manual_review_only",
                "eligible": False,
                "supported_mechanism": None,
                "evidence": [],
                "send_enabled": False,
            },
        })
    to_check = enriched[:max_candidates]

    def check(item):
        page = fetch_source_page(item.get("url", ""))
        if not page.get("reachable"):
            item["channel_rules"] = {
                "status": "source_unreachable",
                "evidence": {},
                "permission_verified": False,
                "automatic_distribution": False,
                "requires_review": True,
                "automation_eligibility": {
                    "status": "manual_review_only",
                    "eligible": False,
                    "supported_mechanism": None,
                    "evidence": [],
                    "send_enabled": False,
                },
            }
            return item
        final_url = page.get("final_url", item.get("url", ""))
        signals = extract_page_signals(page.get("html", ""), final_url)
        rules = detect_audience_channel_rules(signals)
        rules["review_routes"] = {
            "source_url": final_url,
            "application_links": list(signals.get("application_links") or [])[:5],
            "contact_links": list(signals.get("contact_links") or [])[:5],
        }
        # Discovery of a form/link is not permission to automate it. Stage 6
        # requires affirmative integration evidence before any send capability
        # can ever be enabled.
        rules["automation_eligibility"] = {
            "status": "manual_review_only",
            "eligible": False,
            "supported_mechanism": None,
            "evidence": [],
            "reason": "No verified official API or explicitly automation-permitted submission mechanism has been established.",
            "send_enabled": False,
        }
        rules["source_reachable"] = True
        rules["last_checked"] = page.get("last_checked")
        item["channel_rules"] = rules
        if rules["status"] == "restriction_detected":
            item["review_status"] = "restricted_review"
        elif rules["status"] == "rules_or_submission_route_found":
            item["review_status"] = "rules_found_review"
        else:
            item["review_status"] = "needs_review"
        return item

    if to_check:
        workers = max(1, min(len(to_check), 4))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(check, item) for item in to_check]
            checked = [future.result() for future in as_completed(futures)]
        checked_by_id = {item.get("tracking_id"): item for item in checked}
        for index, item in enumerate(enriched[:max_candidates]):
            if item.get("tracking_id") in checked_by_id:
                enriched[index] = checked_by_id[item.get("tracking_id")]
    return enriched



ALLOWED_AUTOMATION_ACTION_TYPES = frozenset({"official_api", "official_submission_api"})


def validate_automation_connector_definition(name, connector):
    """Validate a connector registration contract before it can become supported."""
    errors = []
    connector_name = str(name or "").strip()
    if not connector_name:
        errors.append("connector_name_required")
    if not isinstance(connector, dict):
        return {"valid": False, "errors": errors + ["connector_definition_required"]}
    if connector.get("action_type") not in ALLOWED_AUTOMATION_ACTION_TYPES:
        errors.append("unsupported_action_type")
    if not str(connector.get("credential_env") or "").strip():
        errors.append("credential_env_required")
    rate_limit = connector.get("rate_limit_per_hour")
    if isinstance(rate_limit, bool) or not isinstance(rate_limit, int) or rate_limit < 1 or rate_limit > 100:
        errors.append("bounded_rate_limit_required")
    if connector.get("send_enabled") not in (True, False):
        errors.append("send_enabled_boolean_required")
    if connector.get("requires_user_authorization") is not True:
        errors.append("user_authorization_requirement_required")
    documentation_url = str(connector.get("documentation_url") or "").strip()
    if not documentation_url.startswith("https://"):
        errors.append("official_documentation_url_required")
    return {"valid": not errors, "errors": errors}


AUTOMATION_CONNECTOR_REGISTRY = {
    "sendgrid_mail_v3": {
        "action_type": "official_api",
        "credential_env": "SENDGRID_API_KEY",
        "rate_limit_per_hour": 20,
        "send_enabled": False,
        "requires_user_authorization": True,
        "documentation_url": "https://www.twilio.com/docs/sendgrid/api-reference/mail-send",
        "endpoint": "https://api.sendgrid.com/v3/mail/send",
        "requires_verified_sender": True,
        "requires_unsubscribe_compliance": True,
    },
    "brevo_email_v3": {
        "action_type": "official_api",
        "credential_env": "BREVO_API_KEY",
        "rate_limit_per_hour": 12,
        "rate_limit_per_day": 300,
        "send_enabled": False,
        "requires_user_authorization": True,
        "documentation_url": "https://developers.brevo.com/reference/sendtransacemail",
        "endpoint": "https://api.brevo.com/v3/smtp/email",
        "requires_verified_sender": True,
        "requires_unsubscribe_compliance": True,
        "provider_suppression_supported": True,
        "provider_suppression_read_endpoint": "https://api.brevo.com/v3/smtp/blockedContacts",
        "provider_suppression_write_mode": None,
    },
}

INVALID_AUTOMATION_CONNECTORS = {
    name: validate_automation_connector_definition(name, connector)
    for name, connector in AUTOMATION_CONNECTOR_REGISTRY.items()
    if not validate_automation_connector_definition(name, connector)["valid"]
}
SUPPORTED_AUTOMATION_MECHANISMS = frozenset(
    name for name in AUTOMATION_CONNECTOR_REGISTRY
    if name not in INVALID_AUTOMATION_CONNECTORS
)


def env_flag(name, default=False):
    value = str(os.environ.get(name, "")).strip().lower()
    if not value:
        return bool(default)
    return value in {"1", "true", "yes", "on"}


def automation_live_send_policy(mechanism):
    """Require independent deployment opt-in before any connector may send live."""
    name = str(mechanism or "").strip()
    connector = AUTOMATION_CONNECTOR_REGISTRY.get(name) or {}
    registry_enabled = connector.get("send_enabled") is True
    deployment_enabled = env_flag("AUTOMATION_LIVE_SEND_ENABLED", False)
    connector_opt_in = env_flag("AUTOMATION_" + re.sub(r"[^A-Z0-9]+", "_", name.upper()) + "_ENABLED", False)
    return {
        "mechanism": name or None,
        "registry_enabled": registry_enabled,
        "deployment_enabled": deployment_enabled,
        "connector_opt_in": connector_opt_in,
        "live_send_enabled": registry_enabled and deployment_enabled and connector_opt_in,
    }


def automation_connector_status(mechanism):
    """Return non-secret connector capability metadata for UI/API diagnostics."""
    name = str(mechanism or "").strip()
    connector = AUTOMATION_CONNECTOR_REGISTRY.get(name)
    if not connector:
        return {
            "registered": False,
            "mechanism": name or None,
            "configured": False,
            "send_enabled": False,
            "action_type": None,
        }
    env_key = str(connector.get("credential_env") or "").strip()
    configured = bool(env_key and os.environ.get(env_key))
    live_policy = automation_live_send_policy(name)
    return {
        "registered": True,
        "mechanism": name,
        "configured": configured,
        "send_enabled": bool(live_policy["live_send_enabled"] and configured),
        "live_send_policy": live_policy,
        "action_type": connector.get("action_type"),
        "rate_limit_per_hour": connector.get("rate_limit_per_hour"),
        "requires_user_authorization": connector.get("requires_user_authorization") is True,
        "documentation_url": connector.get("documentation_url"),
        "requires_verified_sender": connector.get("requires_verified_sender") is True,
        "requires_unsubscribe_compliance": connector.get("requires_unsubscribe_compliance") is True,
        "registration_valid": name not in INVALID_AUTOMATION_CONNECTORS,
    }


def automation_distribution_decision(candidate):
    """Fail closed unless a candidate names an explicitly supported send mechanism."""
    rules = (candidate or {}).get("channel_rules") or {}
    eligibility = rules.get("automation_eligibility") or {}
    mechanism = str(eligibility.get("supported_mechanism") or "").strip()
    eligible = eligibility.get("eligible") is True
    send_enabled = eligibility.get("send_enabled") is True
    connector = automation_connector_status(mechanism)
    supported = bool(mechanism and mechanism in SUPPORTED_AUTOMATION_MECHANISMS)
    blockers = []
    if not eligible:
        blockers.append("lead_not_automation_eligible")
    if not send_enabled:
        blockers.append("lead_send_not_enabled")
    if not supported:
        blockers.append("mechanism_not_registered")
    elif not connector["configured"]:
        blockers.append("connector_not_configured")
    elif not connector["send_enabled"]:
        blockers.append("connector_send_disabled")
    allowed = not blockers
    return {
        "allowed": allowed,
        "mechanism": mechanism or None,
        "eligible": eligible,
        "send_enabled": send_enabled,
        "supported": supported,
        "connector": connector,
        "blockers": blockers,
        "reason": "supported_automation_mechanism" if allowed else blockers[0],
    }


def sendgrid_connector_preflight(settings):
    """Validate non-secret SendGrid production prerequisites without sending."""
    settings = settings if isinstance(settings, dict) else {}
    blockers = []
    from_email = str(settings.get("from_email") or "").strip()
    if "@" not in from_email:
        blockers.append("verified_sender_email_required")
    if settings.get("sender_verified") is not True:
        blockers.append("verified_sender_identity_required")
    if settings.get("compliance_confirmed") is not True:
        blockers.append("email_compliance_confirmation_required")
    if settings.get("unsubscribe_ready") is not True:
        blockers.append("unsubscribe_mechanism_required")
    return {
        "ready": not blockers,
        "blockers": blockers,
        "from_email": from_email or None,
        "send_enabled": False,
    }


def sendgrid_server_preflight():
    """Validate trusted deployment-side SendGrid prerequisites without exposing secrets."""
    blockers = []
    from_email = str(os.environ.get("SENDGRID_FROM_EMAIL", "")).strip()
    if not os.environ.get("SENDGRID_API_KEY"):
        blockers.append("sendgrid_api_key_not_configured")
    if "@" not in from_email:
        blockers.append("server_verified_sender_email_required")
    if not env_flag("SENDGRID_SENDER_VERIFIED"):
        blockers.append("server_sender_verification_required")
    if not env_flag("SENDGRID_COMPLIANCE_CONFIRMED"):
        blockers.append("server_email_compliance_confirmation_required")
    if not env_flag("SENDGRID_UNSUBSCRIBE_READY"):
        blockers.append("server_unsubscribe_mechanism_required")
    policy = automation_live_send_policy("sendgrid_mail_v3")
    if not policy["live_send_enabled"]:
        blockers.append("live_send_policy_disabled")
    return {
        "ready": not blockers,
        "blockers": blockers,
        "from_email": from_email or None,
        "credentials_configured": bool(os.environ.get("SENDGRID_API_KEY")),
        "live_send_policy": policy,
    }


def brevo_provider_suppression_capability():
    """Describe Brevo provider-side suppression capability without network I/O."""
    connector = AUTOMATION_CONNECTOR_REGISTRY.get("brevo_email_v3") or {}
    configured = bool(os.environ.get("BREVO_API_KEY"))
    return {
        "mechanism": "brevo_email_v3",
        "supported": connector.get("provider_suppression_supported") is True,
        "credentials_configured": configured,
        "read_endpoint_configured": bool(connector.get("provider_suppression_read_endpoint")),
        "write_mode": connector.get("provider_suppression_write_mode"),
        "write_supported": bool(connector.get("provider_suppression_write_mode")),
        "read_only": not bool(connector.get("provider_suppression_write_mode")),
        "network_io": False,
        "authorization_granted": False,
        "sending_enabled": False,
    }


def brevo_provider_suppression_request(email=None, limit=100, offset=0):
    """Build a Brevo transactional suppression read request without performing network I/O."""
    endpoint = (AUTOMATION_CONNECTOR_REGISTRY.get("brevo_email_v3") or {}).get("provider_suppression_read_endpoint")
    if not endpoint:
        raise RuntimeError("Brevo provider suppression read endpoint is not configured.")
    safe_limit = max(1, min(int(limit or 100), 100))
    safe_offset = max(0, int(offset or 0))
    normalized = normalize_automation_email(email) if email else None
    if normalized is not None and "@" not in normalized:
        raise ValueError("A valid email address is required.")
    return {
        "method": "GET",
        "url": endpoint,
        "query": {"limit": safe_limit, "offset": safe_offset, "sort": "desc"},
        "match_email": normalized,
        "authentication": "api-key",
        "network_io": False,
        "sending_enabled": False,
        "authorization_granted": False,
    }


def brevo_provider_suppression_response_contains_email(payload, email, offset=0):
    """Inspect one Brevo blocked-contact page locally without exposing other contacts."""
    normalized = normalize_automation_email(email)
    if "@" not in normalized:
        raise ValueError("A valid email address is required.")
    contacts = payload.get("contacts") if isinstance(payload, dict) else None
    if not isinstance(contacts, list):
        return {"suppressed": False, "valid_response": False}
    suppressed = any(
        normalize_automation_email(item.get("email")) == normalized
        for item in contacts if isinstance(item, dict)
    )
    count = payload.get("count") if isinstance(payload, dict) else None
    safe_offset = max(0, int(offset or 0))
    exhaustive = isinstance(count, int) and count >= 0 and count <= safe_offset + len(contacts)
    return {
        "suppressed": suppressed,
        "valid_response": True,
        "exhaustive": exhaustive,
        "clear": (not suppressed) and exhaustive,
        "page_size": len(contacts),
        "count": count if isinstance(count, int) and count >= 0 else None,
    }


def verify_brevo_provider_suppression(email, timeout=8, max_pages=100):
    """Read Brevo transactional blocked contacts only; never sends or authorizes email."""
    api_key = str(os.environ.get("BREVO_API_KEY", "")).strip()
    if not api_key:
        return {"verified": False, "suppressed": False, "reason": "brevo_api_key_not_configured", "network_io": False, "sent": False, "authorization_granted": False, "checked_email": normalize_automation_email(email)}

    try:
        page_ceiling = max(1, min(int(max_pages or 100), 100))
    except (TypeError, ValueError):
        page_ceiling = 100
    offset = 0
    pages_checked = 0
    total_count = None

    try:
        while pages_checked < page_ceiling:
            spec = brevo_provider_suppression_request(email, limit=100, offset=offset)
            request = Request(
                spec["url"] + "?" + urlencode(spec["query"]),
                method="GET",
                headers={
                    "api-key": api_key,
                    "Accept": "application/json",
                    "User-Agent": "CrowdfundingDeepSearch/2.0",
                },
            )
            with urlopen(request, timeout=max(1, min(int(timeout or 8), 15))) as response:
                payload = json.loads(response.read().decode("utf-8"))

            inspected = brevo_provider_suppression_response_contains_email(payload, email, offset=offset)
            pages_checked += 1
            if inspected["valid_response"] is not True:
                return {
                    "verified": False,
                    "suppressed": False,
                    "clear": False,
                    "exhaustive": False,
                    "reason": "provider_suppression_response_invalid",
                    "pages_checked": pages_checked,
                    "network_io": True,
                    "sent": False,
                    "authorization_granted": False,
                    "checked_email": normalize_automation_email(email),
                }

            if inspected["suppressed"] is True:
                return {
                    "verified": True,
                    "suppressed": True,
                    "clear": False,
                    "exhaustive": inspected.get("exhaustive") is True,
                    "reason": "provider_suppressed",
                    "pages_checked": pages_checked,
                    "network_io": True,
                    "sent": False,
                    "authorization_granted": False,
                    "checked_email": normalize_automation_email(email),
                }

            total_count = inspected.get("count")
            if inspected.get("clear") is True:
                return {
                    "verified": True,
                    "suppressed": False,
                    "clear": True,
                    "exhaustive": True,
                    "reason": "provider_clear_verified",
                    "pages_checked": pages_checked,
                    "network_io": True,
                    "sent": False,
                    "authorization_granted": False,
                    "checked_email": normalize_automation_email(email),
                }

            page_size = int(inspected.get("page_size") or 0)
            if page_size <= 0 or total_count is None:
                break
            next_offset = offset + page_size
            if next_offset <= offset:
                break
            offset = next_offset

        return {
            "verified": True,
            "suppressed": False,
            "clear": False,
            "exhaustive": False,
            "reason": "provider_suppression_lookup_incomplete",
            "pages_checked": pages_checked,
            "provider_count": total_count,
            "page_ceiling": page_ceiling,
            "network_io": True,
            "sent": False,
            "authorization_granted": False,
            "checked_email": normalize_automation_email(email),
        }
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
        return {
            "verified": False,
            "suppressed": False,
            "clear": False,
            "exhaustive": False,
            "reason": "provider_suppression_lookup_failed",
            "error_type": type(error).__name__,
            "pages_checked": pages_checked,
            "network_io": True,
            "sent": False,
            "authorization_granted": False,
            "checked_email": normalize_automation_email(email),
        }

def brevo_server_preflight():
    """Validate trusted deployment-side Brevo prerequisites without exposing secrets."""
    blockers = []
    from_email = str(os.environ.get("BREVO_FROM_EMAIL", "")).strip()
    if not os.environ.get("BREVO_API_KEY"):
        blockers.append("brevo_api_key_not_configured")
    if "@" not in from_email:
        blockers.append("server_verified_sender_email_required")
    if not env_flag("BREVO_SENDER_VERIFIED"):
        blockers.append("server_sender_verification_required")
    if not env_flag("BREVO_COMPLIANCE_CONFIRMED"):
        blockers.append("server_email_compliance_confirmation_required")
    unsubscribe_ready = env_flag("BREVO_UNSUBSCRIBE_READY")
    public_base_url = str(os.environ.get("PUBLIC_BASE_URL", "")).strip().rstrip("/")
    unsubscribe_secret = automation_unsubscribe_secret()
    if not unsubscribe_ready:
        blockers.append("server_unsubscribe_mechanism_required")
    else:
        if not public_base_url.lower().startswith("https://"):
            blockers.append("server_public_base_url_required")
        if len(unsubscribe_secret) < 32:
            blockers.append("server_unsubscribe_signing_secret_required")
    storage = automation_storage_status()
    provider_suppression = brevo_provider_suppression_capability()
    provider_suppression_verified = env_flag("BREVO_PROVIDER_SUPPRESSION_VERIFIED")
    provider_suppression_ready = (
        provider_suppression.get("supported") is True
        and provider_suppression.get("credentials_configured") is True
        and provider_suppression.get("read_endpoint_configured") is True
        and provider_suppression_verified
    )
    unsubscribe_token_storage_ready = True
    # Provider suppression verification is intentionally read-only. It can
    # protect sends against provider-known blocks, but it cannot make a new
    # unsubscribe durable because this application does not yet have a
    # verified provider-side suppression write path.
    durable_unsubscribe_ready = storage.get("live_ready") is True
    if unsubscribe_ready and not durable_unsubscribe_ready:
        blockers.append("durable_unsubscribe_storage_required")
    policy = automation_live_send_policy("brevo_email_v3")
    if not policy["live_send_enabled"]:
        blockers.append("live_send_policy_disabled")
    return {
        "ready": not blockers,
        "blockers": blockers,
        "from_email": from_email or None,
        "credentials_configured": bool(os.environ.get("BREVO_API_KEY")),
        "public_base_url_configured": public_base_url.lower().startswith("https://"),
        "unsubscribe_signing_secret_configured": len(unsubscribe_secret) >= 32,
        "unsubscribe_storage_ready": storage.get("live_ready") is True,
        "provider_suppression_supported": provider_suppression.get("supported") is True,
        "provider_suppression_read_only": provider_suppression.get("read_only") is True,
        "provider_suppression_write_supported": provider_suppression.get("write_supported") is True,
        "provider_suppression_verified": provider_suppression_verified,
        "provider_suppression_ready": provider_suppression_ready,
        "unsubscribe_token_storage_ready": unsubscribe_token_storage_ready,
        "durable_unsubscribe_ready": durable_unsubscribe_ready,
        "live_send_policy": policy,
    }


def brevo_unsubscribe_footer(unsubscribe_url):
    """Build a plain-text opt-out footer only from an explicit HTTPS unsubscribe URL."""
    url = str(unsubscribe_url or "").strip()
    if not url:
        raise ValueError("An unsubscribe URL is required.")
    if not url.lower().startswith("https://"):
        raise ValueError("The unsubscribe URL must use HTTPS.")
    return "\n\n---\nTo stop receiving these outreach emails, unsubscribe here: " + url


def build_brevo_email_v3_payload(to_email, from_email, subject, body, reply_to=None):
    """Build but do not send a conservative Brevo single-recipient payload."""
    fields = {"to_email": str(to_email or "").strip(), "from_email": str(from_email or "").strip(), "subject": str(subject or "").strip(), "body": str(body or "").strip()}
    missing = [name for name, value in fields.items() if not value]
    if missing:
        raise ValueError("Missing required email fields: " + ", ".join(missing))
    if "@" not in fields["to_email"] or "@" not in fields["from_email"]:
        raise ValueError("Valid recipient and sender email addresses are required.")
    if env_flag("BREVO_UNSUBSCRIBE_READY"):
        public_base_url = str(os.environ.get("PUBLIC_BASE_URL", "")).strip().rstrip("/")
        if not public_base_url.lower().startswith("https://"):
            raise RuntimeError("A secure public base URL is required for unsubscribe links.")
        token = build_automation_unsubscribe_token(fields["to_email"])
        unsubscribe_url = public_base_url + "/api/automation/unsubscribe/" + token
        fields["body"] += brevo_unsubscribe_footer(unsubscribe_url)
    payload = {"sender": {"email": fields["from_email"]}, "to": [{"email": fields["to_email"]}], "subject": fields["subject"], "textContent": fields["body"]}
    reply = str(reply_to or "").strip()
    if reply:
        if "@" not in reply:
            raise ValueError("A valid reply-to email address is required.")
        payload["replyTo"] = {"email": reply}
    return payload


def build_sendgrid_mail_v3_payload(to_email, from_email, subject, body, reply_to=None):
    """Build but do not send a conservative SendGrid v3 single-recipient payload."""
    fields = {
        "to_email": str(to_email or "").strip(),
        "from_email": str(from_email or "").strip(),
        "subject": str(subject or "").strip(),
        "body": str(body or "").strip(),
    }
    missing = [name for name, value in fields.items() if not value]
    if missing:
        raise ValueError("Missing required email fields: " + ", ".join(missing))
    if "@" not in fields["to_email"] or "@" not in fields["from_email"]:
        raise ValueError("Valid recipient and sender email addresses are required.")
    payload = {
        "personalizations": [{"to": [{"email": fields["to_email"]}]}],
        "from": {"email": fields["from_email"]},
        "subject": fields["subject"],
        "content": [{"type": "text/plain", "value": fields["body"]}],
    }
    reply = str(reply_to or "").strip()
    if reply:
        if "@" not in reply:
            raise ValueError("A valid reply-to email address is required.")
        payload["reply_to"] = {"email": reply}
    return payload


def prepare_automation_storage_path():
    """Validate and prepare the configured SQLite parent directory."""
    path = str(AUTOMATION_LEDGER_PATH or "").strip()
    if not path:
        raise RuntimeError("Automation ledger path is not configured.")
    if path == ":memory:":
        return {"path": path, "parent": None, "prepared": True}
    absolute = os.path.abspath(path)
    parent = os.path.dirname(absolute)
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as error:
        raise RuntimeError("Automation ledger directory could not be prepared.") from error
    if not os.path.isdir(parent):
        raise RuntimeError("Automation ledger parent path is not a directory.")
    if not os.access(parent, os.W_OK):
        raise RuntimeError("Automation ledger directory is not writable.")
    return {"path": absolute, "parent": parent, "prepared": True}


def automation_row_value(row, name, index=None, default=None):
    """Read a column from sqlite3.Row, mapping-like rows, or libSQL tuples."""
    if row is None:
        return default
    try:
        return row[name]
    except (TypeError, KeyError, IndexError):
        if index is not None:
            try:
                return row[index]
            except (TypeError, IndexError):
                pass
    return default


def automation_row_dict(cursor, row):
    """Normalize SQLite/libSQL result rows without depending on row_factory."""
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return dict(row)
    if isinstance(row, dict):
        return dict(row)
    names = [str(item[0]) for item in (getattr(cursor, "description", None) or [])]
    if names:
        return {name: row[index] for index, name in enumerate(names) if index < len(row)}
    raise TypeError("Automation storage row metadata is unavailable.")


def automation_ledger_connection():
    backend = str(AUTOMATION_STORAGE_BACKEND or "").strip().lower()
    if backend == "sqlite":
        storage = prepare_automation_storage_path()
        connection = sqlite3.connect(storage["path"], timeout=5)
        connection.row_factory = sqlite3.Row
    elif backend == "turso":
        database_url = str(os.environ.get("TURSO_DATABASE_URL", "")).strip()
        auth_token = str(os.environ.get("TURSO_AUTH_TOKEN", "")).strip()
        if not database_url.startswith("libsql://") or not auth_token:
            raise RuntimeError("Turso database credentials are not configured.")
        if libsql is None:
            raise RuntimeError("Turso libSQL driver is not installed.")
        connection = libsql.connect(database=database_url, auth_token=auth_token)
        # libsql rows support positional access; normalize mapping access below
        # through its sqlite-compatible row factory when available.
        try:
            connection.row_factory = sqlite3.Row
        except (AttributeError, TypeError):
            pass
    else:
        raise RuntimeError("Configured automation storage backend is not supported.")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS automation_execution_ledger (
            ledger_key TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL,
            execution_mode TEXT NOT NULL,
            mechanism TEXT NOT NULL,
            endpoint TEXT,
            outcome TEXT NOT NULL,
            sent INTEGER NOT NULL DEFAULT 0,
            provider_message_id TEXT,
            resolution_reason TEXT,
            blockers_json TEXT NOT NULL DEFAULT '[]',
            recorded_at TEXT NOT NULL,
            updated_at TEXT,
            resolved_at TEXT,
            parent_idempotency_key TEXT,
            attempt_number INTEGER NOT NULL DEFAULT 1
        )"""
    )
    table_info = connection.execute("PRAGMA table_info(automation_execution_ledger)").fetchall()
    columns = {automation_row_value(row, "name", 1) for row in table_info}
    idempotency_is_primary = any(
        automation_row_value(row, "name", 1) == "idempotency_key" and int(automation_row_value(row, "pk", 5, 0) or 0) > 0 for row in table_info
    )
    if idempotency_is_primary:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """CREATE TABLE automation_execution_ledger_v2 (
                ledger_key TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL,
                execution_mode TEXT NOT NULL,
                mechanism TEXT NOT NULL,
                endpoint TEXT,
                outcome TEXT NOT NULL,
                sent INTEGER NOT NULL DEFAULT 0,
                provider_message_id TEXT,
                resolution_reason TEXT,
                blockers_json TEXT NOT NULL DEFAULT '[]',
                recorded_at TEXT NOT NULL,
                updated_at TEXT,
                resolved_at TEXT,
                parent_idempotency_key TEXT,
                attempt_number INTEGER NOT NULL DEFAULT 1
            )"""
        )
        resolution_expr = "resolution_reason" if "resolution_reason" in columns else "NULL"
        updated_expr = "updated_at" if "updated_at" in columns else "recorded_at"
        resolved_expr = "resolved_at" if "resolved_at" in columns else "NULL"
        parent_expr = "parent_idempotency_key" if "parent_idempotency_key" in columns else "NULL"
        attempt_expr = "attempt_number" if "attempt_number" in columns else "1"
        mode_expr = "COALESCE(NULLIF(execution_mode, ''), 'live')" if "execution_mode" in columns else "'live'"
        connection.execute(
            f"""INSERT INTO automation_execution_ledger_v2
                (ledger_key, idempotency_key, execution_mode, mechanism, endpoint, outcome,
                 sent, provider_message_id, resolution_reason, blockers_json, recorded_at,
                 updated_at, resolved_at, parent_idempotency_key, attempt_number)
                SELECT idempotency_key || ':' || {mode_expr}, idempotency_key, {mode_expr},
                       mechanism, endpoint, outcome, sent, provider_message_id,
                       {resolution_expr}, blockers_json, recorded_at, {updated_expr}, {resolved_expr},
                       {parent_expr}, {attempt_expr}
                FROM automation_execution_ledger"""
        )
        connection.execute("DROP TABLE automation_execution_ledger")
        connection.execute("ALTER TABLE automation_execution_ledger_v2 RENAME TO automation_execution_ledger")
        connection.commit()
        columns = {automation_row_value(row, "name", 1) for row in connection.execute("PRAGMA table_info(automation_execution_ledger)").fetchall()}
    if "ledger_key" not in columns:
        connection.execute("ALTER TABLE automation_execution_ledger ADD COLUMN ledger_key TEXT")
    if "execution_mode" not in columns:
        connection.execute("ALTER TABLE automation_execution_ledger ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'live'")
    if "resolution_reason" not in columns:
        connection.execute("ALTER TABLE automation_execution_ledger ADD COLUMN resolution_reason TEXT")
    if "updated_at" not in columns:
        connection.execute("ALTER TABLE automation_execution_ledger ADD COLUMN updated_at TEXT")
        connection.execute("UPDATE automation_execution_ledger SET updated_at = recorded_at WHERE updated_at IS NULL")
    if "resolved_at" not in columns:
        connection.execute("ALTER TABLE automation_execution_ledger ADD COLUMN resolved_at TEXT")
    if "parent_idempotency_key" not in columns:
        connection.execute("ALTER TABLE automation_execution_ledger ADD COLUMN parent_idempotency_key TEXT")
    if "attempt_number" not in columns:
        connection.execute("ALTER TABLE automation_execution_ledger ADD COLUMN attempt_number INTEGER NOT NULL DEFAULT 1")
    connection.execute(
        "UPDATE automation_execution_ledger SET ledger_key = idempotency_key || ':' || execution_mode WHERE ledger_key IS NULL OR ledger_key = ''"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_automation_ledger_key ON automation_execution_ledger(ledger_key)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_automation_idempotency_mode ON automation_execution_ledger(idempotency_key, execution_mode)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS automation_rate_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            mechanism TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            consumed_at TEXT NOT NULL,
            UNIQUE(mechanism, idempotency_key)
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_automation_rate_events_window ON automation_rate_events(mechanism, consumed_at)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS automation_email_suppressions (
            email TEXT PRIMARY KEY,
            reason TEXT NOT NULL DEFAULT 'unsubscribe',
            suppressed_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS automation_unsubscribe_tokens (
            token_hash TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    # Schema setup/migrations may open an implicit SQLite transaction. Commit it
    # before callers begin their own explicit write transaction (BEGIN IMMEDIATE).
    connection.commit()
    return connection

def automation_storage_integrity_check():
    """Run a non-destructive SQLite integrity check for the configured automation store."""
    try:
        with automation_ledger_connection() as connection:
            row = connection.execute("PRAGMA quick_check").fetchone()
            result = str(row[0] if row else "").strip().lower()
            required_tables = {
                "automation_execution_ledger",
                "automation_rate_events",
                "automation_email_suppressions",
                "automation_unsubscribe_tokens",
            }
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            tables = {str(automation_row_value(item, "name", 0)) for item in rows}
            missing_tables = sorted(required_tables - tables)
            return {
                "healthy": result == "ok" and not missing_tables,
                "quick_check": result or None,
                "required_tables_present": not missing_tables,
                "missing_tables": missing_tables,
                "network_io": str(AUTOMATION_STORAGE_BACKEND or "").strip().lower() == "turso",
                "sent": False,
            }
    except Exception as error:
        return {
            "healthy": False,
            "quick_check": None,
            "required_tables_present": False,
            "missing_tables": [],
            "reason": "automation_storage_integrity_check_failed",
            "error_type": type(error).__name__,
            "network_io": str(AUTOMATION_STORAGE_BACKEND or "").strip().lower() == "turso",
            "sent": False,
        }


def write_automation_storage_probe(probe_id):
    """Persist a non-secret durability probe; never sends or performs network I/O."""
    safe_id = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(probe_id or "").strip())[:120]
    if not safe_id:
        raise ValueError("A storage probe id is required.")
    now = datetime.now(timezone.utc).isoformat()
    with automation_ledger_connection() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS automation_storage_probes (
                probe_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT OR IGNORE INTO automation_storage_probes (probe_id, created_at) VALUES (?, ?)",
            (safe_id, now),
        )
        row = connection.execute(
            "SELECT probe_id, created_at FROM automation_storage_probes WHERE probe_id = ?",
            (safe_id,),
        ).fetchone()
    return {
        "probe_id": safe_id,
        "present": row is not None,
        "created_at": automation_row_value(row, "created_at", 1) if row else None,
        "network_io": False,
        "sent": False,
    }


def read_automation_storage_probe(probe_id):
    """Read a non-secret durability probe without modifying it."""
    safe_id = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(probe_id or "").strip())[:120]
    if not safe_id:
        raise ValueError("A storage probe id is required.")
    with automation_ledger_connection() as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='automation_storage_probes'"
        ).fetchone()
        row = None
        if table:
            row = connection.execute(
                "SELECT probe_id, created_at FROM automation_storage_probes WHERE probe_id = ?",
                (safe_id,),
            ).fetchone()
    return {
        "probe_id": safe_id,
        "present": row is not None,
        "created_at": automation_row_value(row, "created_at", 1) if row else None,
        "network_io": False,
        "sent": False,
    }


def automation_unsubscribe_secret():
    return str(os.environ.get("AUTOMATION_UNSUBSCRIBE_SECRET", "")).strip()


def automation_unsubscribe_cipher():
    """Derive a stable authenticated-encryption key from the deployment secret."""
    secret = automation_unsubscribe_secret()
    if len(secret) < 32:
        raise RuntimeError("A strong unsubscribe signing secret is required.")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest()))


def build_automation_unsubscribe_token(email):
    """Create an opaque authenticated token that survives stateless deployments."""
    normalized = normalize_automation_email(email)
    if "@" not in normalized:
        raise ValueError("A valid email address is required.")
    return automation_unsubscribe_cipher().encrypt(normalized.encode("utf-8")).decode("ascii")


def email_from_automation_unsubscribe_token(token):
    """Decrypt and authenticate an opaque unsubscribe token."""
    raw = str(token or "").strip()
    if len(raw) < 32 or len(raw) > 512:
        raise ValueError("Invalid unsubscribe token.")
    try:
        # Require one canonical URL-safe Base64 representation. Some decoders
        # otherwise ignore trailing bytes after padding, which could let a
        # visibly modified token resolve to the same authenticated payload.
        decoded = base64.b64decode(raw.encode("ascii"), altchars=b"-_", validate=True)
        canonical = base64.urlsafe_b64encode(decoded).decode("ascii")
        if not hmac.compare_digest(canonical, raw):
            raise ValueError("Invalid unsubscribe token.")
        email = automation_unsubscribe_cipher().decrypt(raw.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as error:
        raise ValueError("Invalid unsubscribe token.") from error
    normalized = normalize_automation_email(email)
    if "@" not in normalized:
        raise ValueError("Invalid unsubscribe token.")
    return normalized


def normalize_automation_email(value):
    return str(value or "").strip().lower()


def automation_email_suppression_status(email):
    """Check local opt-out state without exposing the suppression list."""
    normalized = normalize_automation_email(email)
    if "@" not in normalized:
        return {"suppressed": False, "valid": False}
    with automation_ledger_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM automation_email_suppressions WHERE email = ?",
            (normalized,),
        ).fetchone()
    return {"suppressed": row is not None, "valid": True}


def suppress_automation_email(email, reason="unsubscribe"):
    """Persist an email opt-out locally; no provider/network action is performed."""
    normalized = normalize_automation_email(email)
    if "@" not in normalized:
        raise ValueError("A valid email address is required.")
    safe_reason = str(reason or "unsubscribe").strip()[:64] or "unsubscribe"
    with automation_ledger_connection() as connection:
        connection.execute(
            """INSERT INTO automation_email_suppressions (email, reason, suppressed_at)
               VALUES (?, ?, ?)
               ON CONFLICT(email) DO UPDATE SET reason=excluded.reason, suppressed_at=excluded.suppressed_at""",
            (normalized, safe_reason, datetime.now(timezone.utc).isoformat()),
        )
    return {"suppressed": True}


def persist_automation_execution_record(record):
    """Persist a non-secret execution record once per idempotency key."""
    if not isinstance(record, dict) or not record.get("idempotency_key"):
        raise ValueError("An idempotency key is required for durable execution records.")
    with automation_ledger_connection() as connection:
        cursor = connection.execute(
            """INSERT OR IGNORE INTO automation_execution_ledger
               (ledger_key, idempotency_key, execution_mode, mechanism, endpoint, outcome, sent, provider_message_id, resolution_reason, blockers_json, recorded_at, updated_at, resolved_at, parent_idempotency_key, attempt_number)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.get("ledger_key") or record["idempotency_key"],
                record["idempotency_key"],
                record.get("execution_mode") or "live",
                record.get("mechanism") or "unknown",
                record.get("endpoint"),
                record.get("outcome") or "blocked",
                1 if record.get("sent") is True else 0,
                record.get("provider_message_id"),
                record.get("resolution_reason"),
                json.dumps(record.get("blockers") or []),
                record.get("recorded_at") or datetime.now(timezone.utc).isoformat(),
                record.get("updated_at") or record.get("recorded_at") or datetime.now(timezone.utc).isoformat(),
                record.get("resolved_at"),
                record.get("parent_idempotency_key"),
                int(record.get("attempt_number") or 1),
            ),
        )
        stored_cursor = connection.execute(
            "SELECT * FROM automation_execution_ledger WHERE ledger_key = ?",
            (record.get("ledger_key") or record["idempotency_key"],),
        )
        stored = stored_cursor.fetchone()
        return {"created": cursor.rowcount == 1, "record": automation_row_dict(stored_cursor, stored) if stored else record}


def prune_automation_execution_ledger(retention_days=None):
    """Delete old non-secret execution metadata according to bounded retention."""
    days = retention_days if isinstance(retention_days, int) else AUTOMATION_LEDGER_RETENTION_DAYS
    days = max(7, min(days, 365))
    cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
    with automation_ledger_connection() as connection:
        rows = connection.execute(
            "SELECT idempotency_key, recorded_at FROM automation_execution_ledger"
        ).fetchall()
        expired = []
        for row in rows:
            try:
                recorded = datetime.fromisoformat(str(automation_row_value(row, "recorded_at", 1)).replace("Z", "+00:00"))
                if recorded.tzinfo is None:
                    recorded = recorded.replace(tzinfo=timezone.utc)
                if recorded.timestamp() < cutoff:
                    expired.append(automation_row_value(row, "idempotency_key", 0))
            except (TypeError, ValueError):
                continue
        if expired:
            connection.executemany(
                "DELETE FROM automation_execution_ledger WHERE idempotency_key = ?",
                [(key,) for key in expired],
            )
        return {"retention_days": days, "deleted": len(expired)}


def find_automation_execution_record(idempotency_key, execution_mode="live"):
    key = str(idempotency_key or "").strip()
    mode = str(execution_mode or "live").strip().lower()
    if not key or mode not in {"live", "simulation"}:
        return None
    with automation_ledger_connection() as connection:
        cursor = connection.execute(
            "SELECT * FROM automation_execution_ledger WHERE idempotency_key = ? AND execution_mode = ?",
            (key, mode),
        )
        row = cursor.fetchone()
        return automation_row_dict(cursor, row) if row else None


AUTOMATION_EXECUTION_TRANSITIONS = {
    "reserved": {"sent", "failed", "cancelled", "unknown"},
    "unknown": {"sent", "failed", "cancelled"},
    "failed": set(),
    "cancelled": set(),
    "sent": set(),
}


def transition_automation_execution(idempotency_key, new_outcome, provider_message_id=None, resolution_reason=None):
    """Move a live execution record through an explicit reconciliation state machine."""
    key = str(idempotency_key or "").strip()
    target = str(new_outcome or "").strip().lower()
    if not key or target not in {"sent", "failed", "cancelled", "unknown"}:
        raise ValueError("A valid live execution transition is required.")
    provider_id = str(provider_message_id or "").strip() or None
    reason = str(resolution_reason or "").strip() or None
    if target == "sent" and not provider_id:
        raise ValueError("A provider message ID is required to confirm a sent execution.")
    if target != "sent" and provider_id:
        raise ValueError("Provider message IDs may only be stored for confirmed sent executions.")
    if target in {"failed", "cancelled", "unknown"} and not reason:
        raise ValueError("A resolution reason is required for non-sent execution transitions.")
    with automation_ledger_connection() as connection:
        row = connection.execute(
            "SELECT * FROM automation_execution_ledger WHERE idempotency_key = ? AND execution_mode = 'live'",
            (key,),
        ).fetchone()
        if not row:
            raise ValueError("Live execution record was not found.")
        current = str(automation_row_value(row, "outcome", 5) or "").lower()
        if target not in AUTOMATION_EXECUTION_TRANSITIONS.get(current, set()):
            raise ValueError("Invalid execution transition from %s to %s." % (current, target))
        connection.execute(
            """UPDATE automation_execution_ledger
               SET outcome = ?, sent = ?, provider_message_id = ?, resolution_reason = ?,
                   updated_at = ?, resolved_at = ?
               WHERE idempotency_key = ? AND execution_mode = 'live'""",
            (target, 1 if target == "sent" else 0, provider_id, reason,
             datetime.now(timezone.utc).isoformat(),
             datetime.now(timezone.utc).isoformat() if target in {"sent", "failed", "cancelled"} else None,
             key),
        )
        updated_cursor = connection.execute(
            "SELECT * FROM automation_execution_ledger WHERE idempotency_key = ? AND execution_mode = 'live'",
            (key,),
        )
        updated = updated_cursor.fetchone()
        return automation_row_dict(updated_cursor, updated)


def automation_execution_duplicate_status(idempotency_key):
    """Return whether an execution key already has durable history."""
    record = find_automation_execution_record(idempotency_key)
    return {
        "duplicate": record is not None,
        "previous_outcome": record.get("outcome") if record else None,
        "previous_sent": bool(record.get("sent")) if record else False,
        "reconciliation_required": bool(record and record.get("outcome") in {"reserved", "unknown"}),
        "provider_message_id": record.get("provider_message_id") if record else None,
        "resolution_reason": record.get("resolution_reason") if record else None,
    }


def reserve_live_automation_execution(plan):
    """Atomically reserve a live idempotency key before any future provider call."""
    record = automation_execution_record(plan, "reserved", execution_mode="live")
    result = persist_automation_execution_record(record)
    stored = result.get("record") or {}
    return {
        "reserved": result.get("created") is True,
        "duplicate": result.get("created") is not True,
        "record": stored,
        "reconciliation_required": stored.get("outcome") in {"reserved", "unknown"},
    }


def automation_execution_record(plan, outcome="blocked", provider_message_id=None, execution_mode="live"):

    """Build a non-secret execution record suitable for durable persistence later."""
    if not isinstance(plan, dict):
        raise ValueError("Execution plan must be an object.")
    return {
        "idempotency_key": plan.get("idempotency_key"),
        "ledger_key": ((str(plan.get("idempotency_key") or "") + ":" + execution_mode) if plan.get("idempotency_key") else None),
        "execution_mode": execution_mode,
        "mechanism": plan.get("mechanism"),
        "endpoint": plan.get("endpoint"),
        "outcome": str(outcome or "blocked"),
        "sent": outcome == "sent",
        "provider_message_id": str(provider_message_id or "").strip() or None,
        "blockers": list(plan.get("blockers") or []),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "resolved_at": datetime.now(timezone.utc).isoformat() if outcome in {"sent", "failed", "cancelled"} else None,
        "parent_idempotency_key": plan.get("parent_idempotency_key"),
        "attempt_number": int(plan.get("attempt_number") or 1),
    }


def execute_sendgrid_transport(plan, simulate=True):
    """Exercise the SendGrid transport boundary; network sending is not implemented."""
    if not isinstance(plan, dict):
        raise ValueError("Execution plan must be an object.")
    policy = automation_live_send_policy("sendgrid_mail_v3")
    blockers = list(plan.get("blockers") or [])
    if simulate:
        return {
            "status": "simulated",
            "sent": False,
            "network_io": False,
            "policy": policy,
            "record": automation_execution_record(plan, "simulated", execution_mode="simulation"),
        }
    if not policy["live_send_enabled"]:
        if "live_send_policy_disabled" not in blockers:
            blockers.append("live_send_policy_disabled")
        blocked_plan = dict(plan)
        blocked_plan["blockers"] = blockers
        return {
            "status": "blocked",
            "sent": False,
            "network_io": False,
            "policy": policy,
            "record": automation_execution_record(blocked_plan, "blocked"),
        }
    return {
        "status": "blocked",
        "sent": False,
        "network_io": False,
        "policy": policy,
        "record": automation_execution_record(plan, "transport_not_implemented"),
        "reason": "live_transport_not_implemented",
    }


def redact_automation_plan(plan):
    """Return a diagnostics-safe execution plan without recipient data, body, or credentials."""
    if not isinstance(plan, dict):
        return {}
    payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
    personalizations = payload.get("personalizations") if isinstance(payload, dict) else []
    recipient_count = 0
    if isinstance(personalizations, list):
        for item in personalizations:
            if isinstance(item, dict) and isinstance(item.get("to"), list):
                recipient_count += len(item["to"])
    brevo_recipients = payload.get("to") if isinstance(payload, dict) else None
    if isinstance(brevo_recipients, list):
        recipient_count += len(brevo_recipients)
    body_present = bool(
        payload.get("content")
        or payload.get("textContent")
        or payload.get("htmlContent")
    ) if isinstance(payload, dict) else False
    return {
        "status": plan.get("status"),
        "ready": plan.get("ready") is True,
        "allowed": plan.get("allowed") is True,
        "sent": plan.get("sent") is True,
        "network_io": plan.get("network_io") is True,
        "authorization_granted": plan.get("authorization_granted") is True,
        "mechanism": plan.get("mechanism"),
        "blockers": list(plan.get("blockers") or []),
        "idempotency_key": plan.get("idempotency_key"),
        "endpoint": plan.get("endpoint"),
        "payload_present": bool(payload),
        "recipient_count": recipient_count,
        "subject_present": bool(payload.get("subject")) if isinstance(payload, dict) else False,
        "body_present": body_present,
    }


def build_live_sendgrid_execution_candidate(data):
    """Build a production candidate using trusted server state; never performs network I/O."""
    if not isinstance(data, dict):
        raise ValueError("A JSON request body is required.")
    execution = validate_automation_execution_request(data)
    send_authorization = validate_live_send_authorization(data)
    server_preflight = sendgrid_server_preflight()
    blockers = list(execution["blockers"])
    blockers.extend(code for code in send_authorization["blockers"] if code not in blockers)
    blockers.extend(code for code in server_preflight["blockers"] if code not in blockers)
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
    to_email = str(data.get("to_email") or "").strip()
    if not to_email:
        blockers.append("recipient_email_required")
    elif automation_email_suppression_status(to_email)["suppressed"]:
        blockers.append("recipient_unsubscribed")
    if not str(draft.get("subject") or "").strip():
        blockers.append("outreach_subject_required")
    if not str(draft.get("body") or "").strip():
        blockers.append("outreach_body_required")
    duplicate_status = automation_execution_duplicate_status(execution.get("idempotency_key"))
    if duplicate_status["duplicate"]:
        blockers.append("idempotency_key_already_recorded")
    storage = automation_storage_status()
    if not storage["live_ready"]:
        blockers.append("durable_automation_storage_required")
    rate_limit = automation_rate_limit_status("sendgrid_mail_v3")
    if not rate_limit.get("allowed"):
        blockers.append("automation_rate_limit_unavailable_or_exhausted")
    payload = None
    if not blockers:
        payload = build_sendgrid_mail_v3_payload(
            to_email,
            server_preflight["from_email"],
            draft["subject"],
            draft["body"],
            data.get("reply_to"),
        )
    return {
        "ready": not blockers,
        "sent": False,
        "network_io": False,
        "mechanism": "sendgrid_mail_v3",
        "blockers": blockers,
        "idempotency_key": execution.get("idempotency_key"),
        "duplicate_status": duplicate_status,
        "rate_limit": rate_limit,
        "storage": storage,
        "payload": payload,
        "endpoint": AUTOMATION_CONNECTOR_REGISTRY["sendgrid_mail_v3"]["endpoint"],
    }


def build_brevo_execution_candidate(data, provider_suppression=None):
    """Build a Brevo execution envelope locally; never performs provider network I/O."""
    if not isinstance(data, dict):
        raise ValueError("A JSON request body is required.")
    execution = validate_automation_execution_request(data)
    send_authorization = validate_live_send_authorization(data)
    server_preflight = brevo_server_preflight()
    blockers = list(execution["blockers"])
    blockers.extend(code for code in send_authorization["blockers"] if code not in blockers)
    blockers.extend(code for code in server_preflight["blockers"] if code not in blockers)

    draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
    to_email = str(data.get("to_email") or "").strip()
    if not to_email or "@" not in to_email:
        blockers.append("recipient_email_required")
    else:
        local_suppression = automation_email_suppression_status(to_email)
        if local_suppression.get("suppressed") is True:
            blockers.append("recipient_unsubscribed")

    if not str(draft.get("subject") or "").strip():
        blockers.append("outreach_subject_required")
    if not str(draft.get("body") or "").strip():
        blockers.append("outreach_body_required")

    provider_state = provider_suppression if isinstance(provider_suppression, dict) else {}
    provider_checked_email = normalize_automation_email(provider_state.get("checked_email")) if provider_state.get("checked_email") else ""
    normalized_recipient = normalize_automation_email(to_email) if to_email else ""
    if not provider_state:
        blockers.append("provider_suppression_check_required")
    elif not provider_checked_email or provider_checked_email != normalized_recipient:
        blockers.append("provider_suppression_recipient_mismatch")
    elif provider_state.get("suppressed") is True:
        blockers.append("recipient_provider_suppressed")
    elif not (
        provider_state.get("verified") is True
        and provider_state.get("clear") is True
        and provider_state.get("exhaustive") is True
    ):
        blockers.append("provider_suppression_clearance_required")

    duplicate_status = automation_execution_duplicate_status(execution.get("idempotency_key"))
    if duplicate_status["duplicate"]:
        blockers.append("idempotency_key_already_recorded")

    storage = automation_storage_status()
    if not storage["live_ready"]:
        blockers.append("durable_automation_storage_required")

    hourly_rate = automation_rate_limit_status("brevo_email_v3")
    if not hourly_rate.get("allowed"):
        blockers.append("automation_rate_limit_unavailable_or_exhausted")
    daily_rate = automation_daily_rate_limit_status("brevo_email_v3")
    if not daily_rate.get("allowed"):
        blockers.append("automation_daily_rate_limit_unavailable_or_exhausted")

    blockers = list(dict.fromkeys(blockers))
    payload = None
    if not blockers:
        payload = build_brevo_email_v3_payload(
            to_email,
            server_preflight["from_email"],
            draft["subject"],
            draft["body"],
            data.get("reply_to"),
        )

    return {
        "ready": not blockers,
        "status": "ready" if not blockers else "blocked",
        "sent": False,
        "network_io": False,
        "authorization_granted": False,
        "mechanism": "brevo_email_v3",
        "blockers": blockers,
        "idempotency_key": execution.get("idempotency_key"),
        "duplicate_status": duplicate_status,
        "provider_suppression": {
            "verified": provider_state.get("verified") is True,
            "suppressed": provider_state.get("suppressed") is True,
            "clear": provider_state.get("clear") is True,
            "exhaustive": provider_state.get("exhaustive") is True,
        },
        "hourly_rate_limit": hourly_rate,
        "daily_rate_limit": daily_rate,
        "storage": storage,
        "payload": payload,
        "endpoint": AUTOMATION_CONNECTOR_REGISTRY["brevo_email_v3"]["endpoint"],
    }


def build_disabled_sendgrid_execution_plan(data):
    """Build the final SendGrid execution envelope without performing network I/O."""
    if not isinstance(data, dict):
        raise ValueError("A JSON request body is required.")
    execution = validate_automation_execution_request(data)
    send_authorization = validate_live_send_authorization(data)
    preflight = sendgrid_connector_preflight(data.get("sendgrid_settings"))
    blockers = list(execution["blockers"])
    blockers.extend(code for code in send_authorization["blockers"] if code not in blockers)
    blockers.extend(code for code in preflight["blockers"] if code not in blockers)
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
    to_email = str(data.get("to_email") or "").strip()
    payload = None
    if not to_email:
        blockers.append("recipient_email_required")
    if not str(draft.get("subject") or "").strip():
        blockers.append("outreach_subject_required")
    if not str(draft.get("body") or "").strip():
        blockers.append("outreach_body_required")
    if not blockers:
        payload = build_sendgrid_mail_v3_payload(
            to_email,
            preflight["from_email"],
            draft["subject"],
            draft["body"],
            data.get("reply_to"),
        )
    duplicate_status = automation_execution_duplicate_status(execution.get("idempotency_key"))
    if duplicate_status["duplicate"]:
        blockers.append("idempotency_key_already_recorded")
    if "connector_live_send_disabled" not in blockers:
        blockers.append("connector_live_send_disabled")
    return {
        "status": "blocked",
        "allowed": False,
        "sent": False,
        "mechanism": "sendgrid_mail_v3",
        "blockers": blockers,
        "idempotency_key": execution.get("idempotency_key"),
        "duplicate_status": duplicate_status,
        "send_authorization": send_authorization,
        "payload": payload,
        "endpoint": AUTOMATION_CONNECTOR_REGISTRY["sendgrid_mail_v3"]["endpoint"],
    }


def automation_operational_tools_enabled():
    """Gate stateful or execution-oriented automation tooling independently from diagnostics."""
    return env_flag("AUTOMATION_OPERATIONAL_TOOLS_ENABLED")


def automation_admin_endpoints_enabled():
    """Keep operational execution-history and reconciliation APIs disabled by default."""
    return env_flag("AUTOMATION_ADMIN_ENDPOINTS_ENABLED")


def automation_storage_status():
    """Describe whether the configured execution store is suitable for live automation."""
    backend = str(AUTOMATION_STORAGE_BACKEND or "").strip().lower()
    if backend == "turso":
        database_url = str(os.environ.get("TURSO_DATABASE_URL", "")).strip()
        auth_token = str(os.environ.get("TURSO_AUTH_TOKEN", "")).strip()
        credentials_configured = database_url.startswith("libsql://") and bool(auth_token)
        driver_ready = libsql is not None
        return {
            "backend": backend,
            "supported_backend": True,
            "remote_durable_store": True,
            "credentials_configured": credentials_configured,
            "driver_ready": driver_ready,
            "persistence_evidence": credentials_configured and driver_ready,
            "live_ready": credentials_configured and driver_ready,
            "reason": "remote_persistent_storage_ready" if credentials_configured and driver_ready else "turso_storage_not_ready",
            "storage_error": None,
        }

    path = str(AUTOMATION_LEDGER_PATH or "").strip()
    persistent_declared = env_flag("AUTOMATION_STORAGE_PERSISTENT")
    persistent_root = str(os.environ.get("AUTOMATION_STORAGE_PERSISTENT_ROOT", "")).strip()
    memory_path = path == ":memory:"
    absolute_path = os.path.abspath(path) if path and not memory_path else path
    absolute_persistent_root = os.path.abspath(persistent_root) if persistent_root else ""
    ephemeral_roots = ("/tmp", "/var/tmp", "/dev/shm")
    ephemeral_path = memory_path or any(
        absolute_path == root or absolute_path.startswith(root + os.sep)
        for root in ephemeral_roots
    )
    persistent_root_is_ephemeral = bool(absolute_persistent_root) and any(
        absolute_persistent_root == root or absolute_persistent_root.startswith(root + os.sep)
        for root in ephemeral_roots
    )
    path_within_persistent_root = bool(
        absolute_path and absolute_persistent_root and not memory_path
        and not persistent_root_is_ephemeral
        and os.path.commonpath([absolute_path, absolute_persistent_root]) == absolute_persistent_root
    )
    explicit_path_configured = "AUTOMATION_LEDGER_PATH" in os.environ and bool(path)
    persistent_root_configured = bool(persistent_root)
    path_prepared = False
    storage_error = None
    if path:
        try:
            prepare_automation_storage_path()
            path_prepared = True
        except RuntimeError as error:
            storage_error = str(error)
    supported_backend = backend == "sqlite"
    persistence_evidence = bool(
        supported_backend and explicit_path_configured and persistent_declared
        and persistent_root_configured and path_within_persistent_root
        and path_prepared and not ephemeral_path
    )
    live_ready = persistence_evidence
    if live_ready:
        reason = "persistent_storage_ready"
    elif not supported_backend:
        reason = "unsupported_automation_storage_backend"
    elif not explicit_path_configured:
        reason = "storage_path_not_explicitly_configured"
    elif not persistent_declared:
        reason = "storage_not_declared_persistent"
    elif not persistent_root_configured:
        reason = "persistent_storage_root_required"
    elif persistent_root_is_ephemeral:
        reason = "persistent_storage_root_is_ephemeral"
    elif not path_within_persistent_root:
        reason = "storage_path_outside_persistent_root"
    elif not path_prepared:
        reason = "storage_path_not_ready"
    else:
        reason = "durable_automation_storage_required"
    return {
        "backend": backend, "supported_backend": supported_backend,
        "path_configured": bool(path), "explicit_path_configured": explicit_path_configured,
        "path_prepared": path_prepared, "persistent_declared": persistent_declared,
        "persistent_root_configured": persistent_root_configured,
        "path_within_persistent_root": path_within_persistent_root,
        "ephemeral_path": ephemeral_path, "persistent_root_is_ephemeral": persistent_root_is_ephemeral,
        "persistence_evidence": persistence_evidence, "live_ready": live_ready,
        "reason": reason, "storage_error": storage_error,
    }

def automation_rate_limit_contract(mechanism):
    """Return the configured bounded rate contract without consuming quota."""
    status = automation_connector_status(mechanism)
    limit = status.get("rate_limit_per_hour")
    connector = AUTOMATION_CONNECTOR_REGISTRY.get(str(mechanism or "").strip()) or {}
    daily_limit = connector.get("rate_limit_per_day")
    return {
        "mechanism": status.get("mechanism"),
        "registered": status.get("registered") is True,
        "configured": status.get("configured") is True,
        "limit_per_hour": limit if isinstance(limit, int) else None,
        "limit_per_day": daily_limit if isinstance(daily_limit, int) and not isinstance(daily_limit, bool) else None,
        "enforcement_required": status.get("registered") is True,
    }


def automation_rate_limit_status(mechanism, now=None):
    """Read durable hourly quota status without consuming it."""
    contract = automation_rate_limit_contract(mechanism)
    limit = contract.get("limit_per_hour")
    if not contract.get("enforcement_required") or not isinstance(limit, int):
        return {"allowed": False, "reason": "rate_limit_contract_unavailable", **contract}
    current = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    cutoff = current.timestamp() - 3600
    with automation_ledger_connection() as connection:
        rows = connection.execute(
            "SELECT consumed_at FROM automation_rate_events WHERE mechanism = ?",
            (str(mechanism or "").strip(),),
        ).fetchall()
    used = 0
    for row in rows:
        try:
            consumed = datetime.fromisoformat(str(automation_row_value(row, "consumed_at", 0)).replace("Z", "+00:00"))
            if consumed.tzinfo is None:
                consumed = consumed.replace(tzinfo=timezone.utc)
            if consumed.timestamp() > cutoff:
                used += 1
        except (TypeError, ValueError):
            continue
    remaining = max(0, limit - used)
    return {**contract, "allowed": remaining > 0, "used_last_hour": used, "remaining": remaining}


def automation_daily_rate_limit_status(mechanism, now=None):
    """Read the connector's UTC-day quota status without consuming quota."""
    status = automation_connector_status(mechanism)
    connector = AUTOMATION_CONNECTOR_REGISTRY.get(str(mechanism or "").strip()) or {}
    limit = connector.get("rate_limit_per_day")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return {"mechanism": status.get("mechanism"), "allowed": False, "reason": "daily_rate_limit_contract_unavailable", "limit_per_day": None}
    current = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    with automation_ledger_connection() as connection:
        rows = connection.execute(
            "SELECT consumed_at FROM automation_rate_events WHERE mechanism = ?",
            (str(mechanism or "").strip(),),
        ).fetchall()
    used = 0
    for row in rows:
        try:
            consumed = datetime.fromisoformat(str(automation_row_value(row, "consumed_at", 0)).replace("Z", "+00:00"))
            if consumed.tzinfo is None:
                consumed = consumed.replace(tzinfo=timezone.utc)
            if consumed.astimezone(timezone.utc) >= day_start:
                used += 1
        except (TypeError, ValueError):
            continue
    remaining = max(0, limit - used)
    return {
        "mechanism": status.get("mechanism"),
        "allowed": remaining > 0,
        "limit_per_day": limit,
        "used_today": used,
        "remaining_today": remaining,
        "day_basis": "UTC",
    }


def prune_automation_rate_events(retention_hours=48):
    """Keep only a small operational window of quota metadata."""
    hours = max(24, min(int(retention_hours or 48), 168))
    cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)
    with automation_ledger_connection() as connection:
        rows = connection.execute(
            "SELECT event_id, consumed_at FROM automation_rate_events"
        ).fetchall()
        expired = []
        for row in rows:
            try:
                consumed = datetime.fromisoformat(str(automation_row_value(row, "consumed_at", 0)).replace("Z", "+00:00"))
                if consumed.tzinfo is None:
                    consumed = consumed.replace(tzinfo=timezone.utc)
                if consumed.timestamp() < cutoff:
                    expired.append(automation_row_value(row, "event_id", 0))
            except (TypeError, ValueError):
                continue
        if expired:
            connection.executemany(
                "DELETE FROM automation_rate_events WHERE event_id = ?",
                [(event_id,) for event_id in expired],
            )
        return {"retention_hours": hours, "deleted": len(expired)}


def consume_automation_rate_limit(mechanism, idempotency_key):
    """Atomically check and consume hourly quota under a SQLite write lock."""
    contract = automation_rate_limit_contract(mechanism)
    limit = contract.get("limit_per_hour")
    if not contract.get("enforcement_required") or not isinstance(limit, int):
        return {"allowed": False, "reason": "rate_limit_contract_unavailable", **contract, "consumed": False}
    mechanism_key = str(mechanism or "").strip()
    key = str(idempotency_key or "").strip()
    if not key:
        raise ValueError("Idempotency key is required to consume automation quota.")
    now = datetime.now(timezone.utc)
    cutoff_iso = datetime.fromtimestamp(now.timestamp() - 3600, tz=timezone.utc).isoformat()
    with automation_ledger_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        retention_cutoff_iso = datetime.fromtimestamp(now.timestamp() - (48 * 3600), tz=timezone.utc).isoformat()
        cleanup_cursor = connection.execute(
            "DELETE FROM automation_rate_events WHERE consumed_at < ?",
            (retention_cutoff_iso,),
        )
        existing = connection.execute(
            """SELECT 1 FROM automation_rate_events
               WHERE mechanism = ? AND idempotency_key = ? LIMIT 1""",
            (mechanism_key, key),
        ).fetchone()
        used = connection.execute(
            """SELECT COUNT(*) AS count FROM automation_rate_events
               WHERE mechanism = ? AND consumed_at > ?""",
            (mechanism_key, cutoff_iso),
        ).fetchone()["count"]
        daily_limit = contract.get("limit_per_day")
        day_start_iso = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        used_today = connection.execute(
            """SELECT COUNT(*) AS count FROM automation_rate_events
               WHERE mechanism = ? AND consumed_at >= ?""",
            (mechanism_key, day_start_iso),
        ).fetchone()["count"] if isinstance(daily_limit, int) else 0
        if existing:
            connection.commit()
            return {
                **contract,
                "allowed": used < limit,
                "used_last_hour": used,
                "remaining": max(0, limit - used),
                "consumed": False,
                "reason": "quota_already_consumed_for_execution",
                "pruned_events": cleanup_cursor.rowcount,
            }
        if used >= limit or (isinstance(daily_limit, int) and used_today >= daily_limit):
            connection.rollback()
            return {
                **contract,
                "allowed": False,
                "used_last_hour": used,
                "remaining": 0,
                "consumed": False,
                "reason": "daily_rate_limit_exhausted" if isinstance(daily_limit, int) and used_today >= daily_limit else "hourly_rate_limit_exhausted",
                "used_today": used_today,
                "remaining_today": max(0, daily_limit - used_today) if isinstance(daily_limit, int) else None,
                "pruned_events": cleanup_cursor.rowcount,
            }
        connection.execute(
            """INSERT INTO automation_rate_events
               (mechanism, idempotency_key, consumed_at) VALUES (?, ?, ?)""",
            (mechanism_key, key, now.isoformat()),
        )
        connection.commit()
        used += 1
    return {
        **contract,
        "allowed": used < limit,
        "used_last_hour": used,
        "remaining": max(0, limit - used),
        "consumed": True,
        "reason": "quota_consumed",
        "pruned_events": cleanup_cursor.rowcount,
    }

def automation_idempotency_key(workspace_id, tracking_id, mechanism, route):
    """Return a stable non-secret key for duplicate execution protection."""
    parts = [
        str(workspace_id or "").strip(),
        str(tracking_id or "").strip(),
        str(mechanism or "").strip().lower(),
        str(route or "").strip().rstrip("/").lower(),
    ]
    if not all(parts):
        raise ValueError("Workspace, tracking ID, mechanism, and route are required for idempotency.")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def automation_attempt_idempotency_key(base_key, attempt=1):
    """Derive a stable execution key for an explicit retry generation."""
    base = str(base_key or "").strip()
    try:
        attempt_number = int(attempt)
    except (TypeError, ValueError):
        raise ValueError("Attempt number must be an integer.")
    if not base:
        raise ValueError("Base idempotency key is required.")
    if attempt_number < 1 or attempt_number > 100:
        raise ValueError("Attempt number must be between 1 and 100.")
    if attempt_number == 1:
        return base
    return hashlib.sha256((base + "|attempt:" + str(attempt_number)).encode("utf-8")).hexdigest()


def automation_retry_decision(base_idempotency_key):
    """Describe whether a terminal live outcome may produce a new explicit retry attempt."""
    record = find_automation_execution_record(base_idempotency_key)
    if not record:
        return {
            "retry_allowed": False,
            "reason": "original_execution_not_found",
            "previous_outcome": None,
            "next_attempt": None,
            "next_idempotency_key": None,
        }
    outcome = str(record.get("outcome") or "").lower()
    if outcome in {"sent", "reserved", "unknown"}:
        return {
            "retry_allowed": False,
            "reason": "retry_blocked_for_" + (outcome or "unknown"),
            "previous_outcome": outcome,
            "next_attempt": None,
            "next_idempotency_key": None,
        }
    if outcome not in {"failed", "cancelled"}:
        return {
            "retry_allowed": False,
            "reason": "retry_requires_terminal_no_send_outcome",
            "previous_outcome": outcome,
            "next_attempt": None,
            "next_idempotency_key": None,
        }
    with automation_ledger_connection() as connection:
        rows = connection.execute(
            """SELECT idempotency_key FROM automation_execution_ledger
               WHERE execution_mode = 'live'"""
        ).fetchall()
    attempt = 2
    while attempt <= 100:
        candidate = automation_attempt_idempotency_key(base_idempotency_key, attempt)
        if not any(str(automation_row_value(row, "idempotency_key", 0)) == candidate for row in rows):
            return {
                "retry_allowed": True,
                "reason": "explicit_retry_available",
                "previous_outcome": outcome,
                "next_attempt": attempt,
                "next_idempotency_key": candidate,
            }
        attempt += 1
    return {
        "retry_allowed": False,
        "reason": "retry_attempt_limit_reached",
        "previous_outcome": outcome,
        "next_attempt": None,
        "next_idempotency_key": None,
    }


def validate_automation_retry_request(data):
    """Require a fresh, explicit authorization before creating a retry attempt."""
    if not isinstance(data, dict):
        raise ValueError("A JSON request body is required.")
    base_key = str(data.get("base_idempotency_key") or "").strip()
    if not base_key:
        raise ValueError("Base idempotency key is required.")
    decision = automation_retry_decision(base_key)
    blockers = []
    if not decision["retry_allowed"]:
        blockers.append(decision["reason"])
    if data.get("user_authorized_retry") is not True:
        blockers.append("explicit_retry_authorization_required")
    if data.get("permission_review_current") is not True:
        blockers.append("current_permission_review_required")
    return {
        "allowed": not blockers,
        "blockers": blockers,
        "reason": "retry_authorized" if not blockers else blockers[0],
        "base_idempotency_key": base_key,
        "next_attempt": decision.get("next_attempt"),
        "next_idempotency_key": decision.get("next_idempotency_key"),
        "previous_outcome": decision.get("previous_outcome"),
    }


def automation_execution_receipt(candidate, prerequisite_result):
    """Create a privacy-minimized, non-sending audit receipt for an execution check."""
    blockers = list(prerequisite_result.get("blockers") or [])
    return {
        "mechanism": prerequisite_result.get("decision", {}).get("mechanism"),
        "allowed": prerequisite_result.get("allowed") is True,
        "sent": False,
        "network_io": False,
        "authorization_granted": False,
        "blockers": blockers,
        "blocker_count": len(blockers),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "privacy": {
            "tracking_id_included": False,
            "lead_name_included": False,
            "recipient_address_included": False,
            "message_body_included": False,
        },
    }


def validate_automation_execution_request(data):
    """Validate user-controlled execution prerequisites without performing a send."""
    if not isinstance(data, dict):
        raise ValueError("A JSON request body is required.")
    lead = data.get("lead")
    if not isinstance(lead, dict):
        raise ValueError("Audience lead must be an object.")
    decision = automation_distribution_decision(lead)
    blockers = list(decision["blockers"])
    if data.get("user_authorized_check") is not True:
        blockers.append("execution_check_authorization_required")
    if data.get("permission_review_current") is not True:
        blockers.append("current_permission_review_required")
    if data.get("deduplication_clear") is not True:
        blockers.append("deduplication_clearance_required")
    route = str(data.get("route") or "").strip()
    workspace_id = str(data.get("workspace_id") or "").strip()
    tracking_id = str(lead.get("tracking_id") or "").strip()
    idempotency_key = None
    if not route:
        blockers.append("verified_route_required")
    if not workspace_id:
        blockers.append("workspace_id_required")
    if route and workspace_id and tracking_id and decision.get("mechanism"):
        idempotency_key = automation_idempotency_key(
            workspace_id, tracking_id, decision["mechanism"], route
        )
    return {
        "allowed": not blockers,
        "dry_run": True,
        "sent": False,
        "decision": decision,
        "blockers": blockers,
        "idempotency_key": idempotency_key,
        "rate_limit": automation_rate_limit_contract(decision.get("mechanism")),
        "reason": "execution_prerequisites_satisfied" if not blockers else blockers[0],
    }


def validate_live_send_authorization(data):
    """Validate explicit authorization for a future live send, separate from dry-run consent."""
    if not isinstance(data, dict):
        raise ValueError("A JSON request body is required.")
    blockers = []
    if data.get("user_authorized_send") is not True:
        blockers.append("live_send_authorization_required")
    if data.get("permission_review_current") is not True:
        blockers.append("current_permission_review_required")
    if data.get("deduplication_clear") is not True:
        blockers.append("deduplication_clearance_required")
    if data.get("user_authorized_check") is True and data.get("user_authorized_send") is not True:
        blockers.append("dry_run_authorization_not_valid_for_send")
    return {
        "allowed": not blockers,
        "blockers": blockers,
        "reason": "live_send_authorized" if not blockers else blockers[0],
    }


def build_automation_dry_run(candidate):
    """Build a non-sending Stage 6 execution plan for a discovered audience lead."""
    if not isinstance(candidate, dict):
        raise ValueError("Audience lead must be an object.")
    decision = automation_distribution_decision(candidate)
    rules = candidate.get("channel_rules") or {}
    eligibility = rules.get("automation_eligibility") or {}
    return {
        "status": "ready" if decision["allowed"] else "blocked",
        "dry_run": True,
        "sent": False,
        "tracking_id": candidate.get("tracking_id"),
        "lead_name": candidate.get("name"),
        "mechanism": decision.get("mechanism"),
        "decision": decision,
        "eligibility_status": eligibility.get("status", "manual_review_only"),
        "next_step": (
            "Connector is eligible for a future user-authorized execution flow."
            if decision["allowed"]
            else "Blocked by: " + ", ".join(decision["blockers"]) + ". Keep this lead in assisted/manual review."
        ),
    }


def audience_next_action(candidate):
    """Recommend a review-first next action from discovered channel-rule evidence."""
    rules = candidate.get("channel_rules") or {}
    status = rules.get("status", "not_checked")
    url = candidate.get("url", "")
    if status == "restriction_detected":
        return {
            "type": "do_not_contact_until_reviewed",
            "label": "Review restriction",
            "url": url,
            "automation_ready": False,
            "requires_user_review": True,
            "blocked_by_rules": True,
        }
    if status == "rules_or_submission_route_found":
        return {
            "type": "review_submission_rules",
            "label": "Review submission rules",
            "url": url,
            "automation_ready": False,
            "requires_user_review": True,
            "blocked_by_rules": False,
        }
    return {
        "type": "verify_channel_rules",
        "label": "Verify channel rules",
        "url": url,
        "automation_ready": False,
        "requires_user_review": True,
        "blocked_by_rules": False,
    }


def build_assisted_outreach_draft(data):
    """Prepare a truthful, channel-aware outreach draft without sending or posting it."""
    if not isinstance(data, dict):
        raise ValueError("A JSON request body is required.")
    need = str(data.get("need") or "General Financial Assistance").strip()
    location = str(data.get("location") or "").strip()
    campaign_url = str(data.get("campaign_url") or "").strip()
    campaign_summary = " ".join(str(data.get("campaign_summary") or "").split()).strip()[:1200]
    goal = data.get("goal")
    lead = data.get("lead") or {}
    if not isinstance(lead, dict):
        raise ValueError("Audience lead must be an object.")
    name = str(lead.get("name") or "this community").strip()[:300]
    channel = str(lead.get("channel_type") or lead.get("type") or "Audience").strip()[:100]
    rules = lead.get("channel_rules") or {}
    rule_status = str(rules.get("status") or "not_checked")
    if rule_status == "restriction_detected":
        return {
            "status": "blocked", "reason": "restriction_detected",
            "message": "A restriction was detected for this channel. Review the source rules before preparing outreach.",
            "automatic_distribution": False, "requires_user_review": True,
        }
    if len(need) > MAX_QUERY_LENGTH or len(location) > MAX_QUERY_LENGTH:
        raise ValueError("Outreach fields are too long.")
    if campaign_url and not campaign_url.lower().startswith(("http://", "https://")):
        raise ValueError("Campaign URL must use HTTP or HTTPS.")
    if goal not in (None, ""):
        try:
            goal = float(goal)
            if goal < 0:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("Fundraising goal must be a non-negative number.")
    else:
        goal = None

    location_phrase = (" in " + location) if location else ""
    need_phrase = need.lower()
    goal_line = ("The campaign goal is $" + format(goal, ",.0f") + ".") if goal is not None else ""
    summary_line = campaign_summary if campaign_summary else "The campaign is seeking support related to " + need_phrase + location_phrase + "."

    if channel == "Local Media":
        subject = "Community story tip: " + need
        purpose = "I am sharing this as a possible community or human-interest story for your editorial consideration."
        request = "If this fits your coverage, please let me know the appropriate story-tip or submission route."
    elif channel == "Creators & Podcasts":
        subject = "Possible community story or interview: " + need
        purpose = "I am reaching out to see whether this campaign may fit your community stories, interviews, or resource-focused coverage."
        request = "If it is relevant to your audience, please let me know whether there is an appropriate way to submit the story for consideration."
    elif channel == "Directories & Newsletters":
        subject = "Resource submission for consideration: " + need
        purpose = "I would like to ask whether this campaign is eligible to be considered for your resource list, directory, or newsletter."
        request = "If submissions are accepted, please point me to the preferred submission process and any eligibility requirements."
    else:
        subject = "Question about sharing a " + need + " crowdfunding campaign"
        purpose = "I found this community while looking for relevant public resources and would like to ask whether sharing this campaign here is permitted."
        request = "If campaign sharing is allowed, please let me know the appropriate section, format, or posting rules."

    intro = "Hello, I found " + name + " while researching resources related to " + need_phrase + location_phrase + "."
    transparency = "I am contacting you about a crowdfunding campaign; I am not representing your organization or claiming that you endorse it."
    respect = "I do not want to post or send anything that conflicts with your rules."
    link_line = ("Campaign link: " + campaign_url) if campaign_url else "Campaign link: [add campaign URL after review]"
    paragraphs = [intro, summary_line]
    if goal_line:
        paragraphs.append(goal_line)
    paragraphs.extend([purpose, transparency, respect + " " + request, link_line, "Thank you for your time."])
    body = "\n\n".join(paragraphs)

    return {
        "status": "success",
        "draft": {"subject": subject, "body": body, "channel_type": channel, "lead_name": name},
        "permission_status": rule_status,
        "automatic_distribution": False,
        "requires_user_review": True,
        "send_enabled": False,
        "note": "This is a prepared draft only. Review the destination's current rules, verify every campaign detail, and edit the message before sending or posting.",
    }

def finalize_audience_actions(candidates):
    finalized = []
    for candidate in candidates:
        item = dict(candidate)
        item["recommended_next_action"] = audience_next_action(item)
        item["outreach_readiness"] = {
            "status": item["recommended_next_action"]["type"],
            "rules_reviewed": False,
            "permission_verified": False,
            "safe_for_automatic_distribution": False,
            "requires_user_review": True,
        }
        finalized.append(item)
    return finalized


def build_audience_discovery_response(data):
    """Run bounded Audience DeepSearch retrieval; all resulting actions require review."""
    base = build_audience_plan_response(data)
    retrieval = retrieve_audience_candidates(base["search_plan"], per_lane=2)
    results = [
        audience_relevance_signals(item, base["query"]["need"], base["query"]["location"])
        for item in retrieval.get("candidates", [])
    ]
    before_dedup_count = len(results)
    results = deduplicate_audience_candidates(results)
    after_dedup_count = len(results)
    for item in results:
        lane_count = len(item.get("discovered_in_lanes") or [])
        stage_count = len(item.get("discovered_in_stages") or [])
        if lane_count > 1 or stage_count > 1:
            corroboration_boost = min(10, max(0, lane_count - 1) * 5 + max(0, stage_count - 1) * 3)
            item["audience_relevance_score"] = min(100, item.get("audience_relevance_score", 0) + corroboration_boost)
            item.setdefault("audience_relevance_signals", []).append("cross_search_corroboration")
            item["cross_search_corroboration"] = {
                "lane_count": lane_count,
                "stage_count": stage_count,
                "boost": corroboration_boost,
            }
    results.sort(key=lambda item: item.get("audience_relevance_score", 0), reverse=True)
    results = enrich_audience_with_rule_checks(results)
    results = finalize_audience_actions(results)
    results.sort(key=lambda item: (
        item.get("review_status") != "restricted_review",
        item.get("audience_relevance_score", 0)
    ), reverse=True)
    base["audience"].update({
        "stage": "audience-discovery-v1",
        "live_search": retrieval.get("configured", False),
        "provider": retrieval.get("provider", "none"),
        "result_limit": MAX_DISCOVERY_RESULTS,
        "automatic_distribution": False,
        "candidate_count_before_dedup": before_dedup_count,
        "candidate_count_after_dedup": after_dedup_count,
        "duplicates_merged": max(0, before_dedup_count - after_dedup_count),
        "credits_used": 0 if not retrieval.get("configured", False) else None,
        "credit_usage_known": not retrieval.get("configured", False),
        "credit_usage_note": "No provider call was configured." if not retrieval.get("configured", False) else "Provider usage is provider-dependent; this response does not claim zero credits.",
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
    goal = data.get("goal")
    campaign_url = str(data.get("campaign_url") or "").strip()
    campaign_summary = " ".join(str(data.get("campaign_summary") or "").split()).strip()[:1200]
    if campaign_url and not re.match(r"^https?://", campaign_url, re.I):
        raise ValueError("Campaign URL must use http or https.")
    if goal not in (None, ""):
        try:
            goal = float(goal)
        except (TypeError, ValueError):
            raise ValueError("Goal must be numeric.")
        if goal < 0 or goal > 1000000000:
            raise ValueError("Goal is outside the supported range.")
    if len(need) > MAX_QUERY_LENGTH or len(location) > MAX_QUERY_LENGTH:
        raise ValueError("Audience search fields are too long.")
    if scope not in {"local", "state", "national", "worldwide", "automatic"}:
        raise ValueError("Unsupported geographic scope.")
    plan = build_audience_queries(need, location, scope)
    return {
        "status": "success",
        "query": {"need": need, "location": location, "scope": scope, "goal": goal, "campaign_url": campaign_url, "campaign_summary": campaign_summary},
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
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        if str(os.environ.get("PUBLIC_BASE_URL", "")).strip().lower().startswith("https://"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
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
            "audience_plan": "/api/audience/plan",
            "automation_connectors": "/api/automation/connectors"
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

    @app.get("/api/automation/connectors")
    def automation_connectors():
        """Expose only non-secret Stage 6 connector readiness metadata."""
        mechanisms = sorted(AUTOMATION_CONNECTOR_REGISTRY)
        return jsonify({
            "status": "ok",
            "automatic_distribution_enabled": any(
                automation_connector_status(name)["send_enabled"] for name in mechanisms
            ),
            "connectors": [automation_connector_status(name) for name in mechanisms],
            "unregistered_routes_remain_manual": True,
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

    @app.route("/api/automation/unsubscribe/<token>", methods=["GET", "POST"])
    def automation_unsubscribe(token):
        try:
            email = email_from_automation_unsubscribe_token(token)
        except (ValueError, RuntimeError):
            return ("Invalid or unavailable unsubscribe link.", 400, {"Content-Type": "text/plain; charset=utf-8"})
        if request.method == "GET":
            return (
                "<!doctype html><html><head><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                "<title>Confirm unsubscribe</title></head><body><main>"
                "<h1>Confirm unsubscribe</h1><p>Use the button below to stop future Crowdfunding DeepSearch outreach emails.</p>"
                "<form method=\"post\"><button type=\"submit\">Unsubscribe</button></form>"
                "</main></body></html>",
                200,
                {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"},
            )
        suppress_automation_email(email, "unsubscribe")
        return ("You have been unsubscribed from future Crowdfunding DeepSearch outreach emails.", 200, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"})


    @app.route("/api/automation/executions/reconcile", methods=["POST", "OPTIONS"])
    def automation_execution_reconcile():
        if request.method == "OPTIONS":
            return ("", 204)
        if not automation_admin_endpoints_enabled():
            return jsonify({"status": "disabled", "message": "Automation admin endpoints are disabled."}), 404
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "A JSON request body is required."}), 400
        if data.get("confirm_reconciliation") is not True:
            return jsonify({"status": "error", "message": "Explicit reconciliation confirmation is required."}), 400
        try:
            record = transition_automation_execution(
                data.get("idempotency_key"),
                data.get("outcome"),
                data.get("provider_message_id"),
                data.get("resolution_reason"),
            )
        except ValueError as error:
            return jsonify({"status": "error", "message": str(error)}), 400
        record["sent"] = bool(record.get("sent"))
        record["blockers"] = json.loads(record.pop("blockers_json", "[]"))
        return jsonify({"status": "ok", "record": record, "network_io": False})

    @app.route("/api/automation/executions/reconciliation", methods=["GET", "OPTIONS"])
    def automation_reconciliation_queue():
        if request.method == "OPTIONS":
            return ("", 204)
        if not automation_admin_endpoints_enabled():
            return jsonify({"status": "disabled", "message": "Automation admin endpoints are disabled."}), 404
        with automation_ledger_connection() as connection:
            rows = connection.execute(
                """SELECT idempotency_key, mechanism, endpoint, outcome, sent,
                          provider_message_id, resolution_reason, recorded_at
                   FROM automation_execution_ledger
                   WHERE execution_mode = 'live' AND outcome IN ('reserved', 'unknown')
                   ORDER BY recorded_at ASC
                   LIMIT 100"""
            ).fetchall()
        return jsonify({
            "status": "ok",
            "count": len(rows),
            "records": [dict(row) for row in rows],
        })

    @app.route("/api/automation/executions/<idempotency_key>", methods=["GET", "OPTIONS"])
    def automation_execution_lookup(idempotency_key):
        if request.method == "OPTIONS":
            return ("", 204)
        if not automation_admin_endpoints_enabled():
            return jsonify({"status": "disabled", "message": "Automation admin endpoints are disabled."}), 404
        mode = request.args.get("mode", "live")
        record = find_automation_execution_record(idempotency_key, mode)
        if not record:
            return jsonify({"status": "not_found"}), 404
        record["blockers"] = json.loads(record.pop("blockers_json", "[]"))
        record["sent"] = bool(record.get("sent"))
        return jsonify({"status": "ok", "record": record})

    @app.route("/api/automation/connectors/sendgrid/simulate", methods=["POST", "OPTIONS"])
    def sendgrid_simulate():
        if request.method == "OPTIONS":
            return ("", 204)
        if not automation_operational_tools_enabled():
            return jsonify({"status": "disabled", "message": "Automation operational tools are disabled."}), 404
        data = request.get_json(silent=True)
        try:
            plan = build_disabled_sendgrid_execution_plan(data)
            result = execute_sendgrid_transport(plan, simulate=True)
            record = result.get("record") or {}
            result["ledger"] = (
                persist_automation_execution_record(record)
                if record.get("idempotency_key")
                else {"created": False, "record": None}
            )
            return jsonify(result)
        except ValueError as error:
            return jsonify({"status": "error", "message": str(error)}), 400

    @app.route("/api/automation/connectors/sendgrid/execution-diagnostics", methods=["POST", "OPTIONS"])
    def sendgrid_execution_diagnostics():
        if request.method == "OPTIONS":
            return ("", 204)
        data = request.get_json(silent=True)
        try:
            return jsonify(redact_automation_plan(build_disabled_sendgrid_execution_plan(data)))
        except ValueError as error:
            return jsonify({"status": "error", "message": str(error)}), 400

    @app.route("/api/automation/connectors/brevo/execution-candidate", methods=["POST", "OPTIONS"])
    def brevo_execution_candidate():
        if request.method == "OPTIONS":
            return ("", 204)
        if not automation_operational_tools_enabled():
            return jsonify({"status": "disabled", "message": "Automation operational tools are disabled."}), 404
        if request.content_length is not None and request.content_length > 65536:
            return jsonify({"status": "error", "message": "Request body is too large."}), 413
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "A JSON request body is required."}), 400
        if data.get("user_authorized_check") is not True:
            return jsonify({
                "status": "blocked",
                "ready": False,
                "sent": False,
                "network_io": False,
                "authorization_granted": False,
                "blockers": ["execution_check_authorization_required"],
            }), 403
        to_email = str(data.get("to_email") or "").strip()
        if "@" not in to_email:
            return jsonify({"status": "error", "message": "A valid recipient email is required."}), 400
        provider_suppression = verify_brevo_provider_suppression(to_email)
        plan = build_brevo_execution_candidate(data, provider_suppression=provider_suppression)
        # This endpoint performs a read-only provider suppression check, then
        # returns redacted diagnostics. It never executes the candidate.
        result = redact_automation_plan(plan)
        result["provider_suppression_checked"] = provider_suppression.get("verified") is True
        result["provider_suppression_clear"] = provider_suppression.get("clear") is True
        result["provider_suppression_suppressed"] = provider_suppression.get("suppressed") is True
        result["sent"] = False
        result["authorization_granted"] = False
        return jsonify(result)

    @app.route("/api/automation/connectors/brevo/execution-diagnostics", methods=["POST", "OPTIONS"])
    def brevo_execution_diagnostics():
        if request.method == "OPTIONS":
            return ("", 204)
        if not automation_operational_tools_enabled():
            return jsonify({"status": "disabled", "message": "Automation operational tools are disabled."}), 404
        if request.content_length is not None and request.content_length > 65536:
            return jsonify({"status": "error", "message": "Request body is too large."}), 413
        data = request.get_json(silent=True)
        try:
            plan = build_brevo_execution_candidate(data)
            return jsonify(redact_automation_plan(plan))
        except ValueError as error:
            return jsonify({"status": "error", "message": str(error)}), 400

    @app.route("/api/automation/connectors/sendgrid/execution-plan", methods=["POST", "OPTIONS"])
    def sendgrid_execution_plan():
        if request.method == "OPTIONS":
            return ("", 204)
        if not automation_operational_tools_enabled():
            return jsonify({"status": "disabled", "message": "Automation operational tools are disabled."}), 404
        data = request.get_json(silent=True)
        try:
            return jsonify(build_disabled_sendgrid_execution_plan(data))
        except ValueError as error:
            return jsonify({"status": "error", "message": str(error)}), 400

    @app.route("/api/automation/connectors/sendgrid/server-readiness", methods=["GET", "OPTIONS"])
    def sendgrid_server_readiness():
        if request.method == "OPTIONS":
            return ("", 204)
        result = sendgrid_server_preflight()
        return jsonify({
            "ready": result["ready"],
            "blockers": result["blockers"],
            "from_email_configured": bool(result["from_email"]),
            "credentials_configured": result["credentials_configured"],
            "live_send_policy": result["live_send_policy"],
        })

    @app.route("/api/automation/connectors/brevo/suppression-check", methods=["POST", "OPTIONS"])
    def brevo_suppression_check():
        if request.method == "OPTIONS":
            return ("", 204)
        if not automation_operational_tools_enabled():
            return jsonify({"status": "disabled", "message": "Automation operational tools are disabled."}), 404
        if request.content_length is not None and request.content_length > 4096:
            return jsonify({"status": "error", "message": "Request body is too large."}), 413
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "A JSON request body is required."}), 400
        if data.get("user_authorized_check") is not True:
            return jsonify({
                "status": "blocked",
                "reason": "execution_check_authorization_required",
                "verified": False,
                "suppressed": False,
                "clear": False,
                "sent": False,
                "network_io": False,
                "authorization_granted": False,
            }), 403
        email = str(data.get("email") or "").strip()
        if "@" not in email:
            return jsonify({"status": "error", "message": "A valid email address is required."}), 400
        result = verify_brevo_provider_suppression(email)
        result["status"] = (
            "suppressed" if result.get("suppressed") is True
            else ("clear" if result.get("clear") is True else "incomplete")
        )
        # Authorization here is permission for this read-only check only. It is
        # deliberately not live-send authorization.
        result["authorization_granted"] = False
        result["sent"] = False
        return jsonify(result)

    @app.route("/api/automation/connectors/brevo/server-readiness", methods=["GET", "OPTIONS"])
    def brevo_server_readiness():
        if request.method == "OPTIONS":
            return ("", 204)
        result = brevo_server_preflight()
        return jsonify({
            "ready": result["ready"],
            "blockers": result["blockers"],
            "from_email_configured": bool(result["from_email"]),
            "credentials_configured": result["credentials_configured"],
            "public_base_url_configured": result["public_base_url_configured"],
            "unsubscribe_signing_secret_configured": result["unsubscribe_signing_secret_configured"],
            "unsubscribe_storage_ready": result["unsubscribe_storage_ready"],
            "provider_suppression_supported": result["provider_suppression_supported"],
            "provider_suppression_read_only": result["provider_suppression_read_only"],
            "provider_suppression_write_supported": result["provider_suppression_write_supported"],
            "provider_suppression_verified": result["provider_suppression_verified"],
            "provider_suppression_ready": result["provider_suppression_ready"],
            "unsubscribe_token_storage_ready": result["unsubscribe_token_storage_ready"],
            "durable_unsubscribe_ready": result["durable_unsubscribe_ready"],
            "live_send_policy": result["live_send_policy"],
            "sent": False,
            "network_io": False,
            "authorization_granted": False,
        })

    @app.route("/api/automation/storage-probe/<probe_id>", methods=["GET", "POST", "OPTIONS"])
    def automation_storage_probe(probe_id):
        if request.method == "OPTIONS":
            return ("", 204)
        if not automation_admin_endpoints_enabled():
            return jsonify({"status": "disabled", "message": "Automation admin endpoints are disabled."}), 404
        storage = automation_storage_status()
        if storage.get("live_ready") is not True:
            return jsonify({
                "status": "blocked",
                "reason": "durable_automation_storage_required",
                "present": False,
                "sent": False,
                "network_io": False,
            }), 409
        try:
            result = (
                write_automation_storage_probe(probe_id)
                if request.method == "POST"
                else read_automation_storage_probe(probe_id)
            )
            result["status"] = "present" if result.get("present") else "missing"
            return jsonify(result)
        except ValueError as error:
            return jsonify({"status": "error", "message": str(error)}), 400

    @app.route("/api/automation/operational-readiness", methods=["GET", "OPTIONS"])
    def automation_operational_readiness():
        if request.method == "OPTIONS":
            return ("", 204)
        storage = automation_storage_status()
        integrity = automation_storage_integrity_check()
        rate_limit = automation_rate_limit_status("brevo_email_v3")
        daily_rate_limit = automation_daily_rate_limit_status("brevo_email_v3")
        return jsonify({
            "status": "ok",
            "storage": {
                "backend": storage.get("backend"),
                "supported_backend": storage.get("supported_backend") is True,
                "explicit_path_configured": storage.get("explicit_path_configured") is True,
                "persistent_declared": storage.get("persistent_declared") is True,
                "persistent_root_configured": storage.get("persistent_root_configured") is True,
                "path_within_persistent_root": storage.get("path_within_persistent_root") is True,
                "ephemeral_path": storage.get("ephemeral_path") is True,
                "persistent_root_is_ephemeral": storage.get("persistent_root_is_ephemeral") is True,
                "persistence_evidence": storage.get("persistence_evidence") is True,
                "live_ready": storage.get("live_ready") is True,
                "reason": storage.get("reason"),
            },
            "storage_integrity": {
                "healthy": integrity.get("healthy") is True,
                "quick_check": integrity.get("quick_check"),
                "required_tables_present": integrity.get("required_tables_present") is True,
                "missing_tables": list(integrity.get("missing_tables") or []),
            },
            "rate_limit": {
                "mechanism": rate_limit.get("mechanism"),
                "enforcement_required": rate_limit.get("enforcement_required") is True,
                "limit_per_hour": rate_limit.get("limit_per_hour"),
                "used_last_hour": rate_limit.get("used_last_hour"),
                "remaining": rate_limit.get("remaining"),
                "allowed": rate_limit.get("allowed") is True,
                "reason": rate_limit.get("reason"),
            },
            "daily_rate_limit": {
                "mechanism": daily_rate_limit.get("mechanism"),
                "limit_per_day": daily_rate_limit.get("limit_per_day"),
                "used_today": daily_rate_limit.get("used_today"),
                "remaining": daily_rate_limit.get("remaining_today"),
                "allowed": daily_rate_limit.get("allowed") is True,
                "reason": daily_rate_limit.get("reason"),
            },
            "sent": False,
            "network_io": False,
            "authorization_granted": False,
        })

    @app.route("/api/automation/connectors/sendgrid/preflight", methods=["POST", "OPTIONS"])
    def sendgrid_preflight():
        if request.method == "OPTIONS":
            return ("", 204)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "A JSON request body is required."}), 400
        result = sendgrid_connector_preflight(data)
        result["status"] = "ready" if result["ready"] else "blocked"
        return jsonify(result)

    @app.route("/api/automation/execution-check", methods=["POST", "OPTIONS"])
    def automation_execution_check():
        if request.method == "OPTIONS":
            return ("", 204)
        if not automation_operational_tools_enabled():
            return jsonify({"status": "disabled", "message": "Automation operational tools are disabled."}), 404
        if request.content_length is not None and request.content_length > 65536:
            return jsonify({"status": "error", "message": "Request body is too large."}), 413
        data = request.get_json(silent=True)
        try:
            result = validate_automation_execution_request(data)
            result["status"] = "ready" if result["allowed"] else "blocked"
            result["receipt"] = automation_execution_receipt(data.get("lead"), result)
            return jsonify(result)
        except ValueError as error:
            return jsonify({"status": "error", "message": str(error)}), 400

    @app.route("/api/automation/dry-run", methods=["POST", "OPTIONS"])
    def automation_dry_run():
        if request.method == "OPTIONS":
            return ("", 204)
        if request.content_length is not None and request.content_length > 65536:
            return jsonify({"status": "error", "message": "Request body is too large."}), 413
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "A JSON request body is required."}), 400
        try:
            lead = data.get("lead")
            return jsonify(build_automation_dry_run(lead))
        except ValueError as error:
            return jsonify({"status": "error", "message": str(error)}), 400
        except Exception:
            app.logger.exception("Automation dry-run request failed")
            return jsonify({"status": "error", "message": "Automation dry-run request failed."}), 500

    @app.route("/api/audience/outreach-draft", methods=["POST", "OPTIONS"])
    def audience_outreach_draft():
        if request.method == "OPTIONS":
            return ("", 204)
        if request.content_length is not None and request.content_length > 65536:
            return jsonify({"status": "error", "message": "Request body is too large."}), 413
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "A JSON request body is required."}), 400
        try:
            result = build_assisted_outreach_draft(data)
            return jsonify(result), (409 if result.get("status") == "blocked" else 200)
        except ValueError as error:
            return jsonify({"status": "error", "message": str(error)}), 400
        except Exception:
            app.logger.exception("Audience outreach draft request failed")
            return jsonify({"status": "error", "message": "Audience outreach draft request failed."}), 500

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
