import json
import os
import re
import hashlib
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlencode, urlparse, urljoin, parse_qsl
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
MAX_AUDIENCE_RULE_CHECKS = env_int("MAX_AUDIENCE_RULE_CHECKS", 4, 0, 12)
MAX_DISCOVERY_RESULTS = env_int("MAX_DISCOVERY_RESULTS", 25, 1, 100)
MAX_QUERY_LENGTH = env_int("MAX_QUERY_LENGTH", 500, 100, 2000)
MAX_SEARCH_LANES = env_int("MAX_SEARCH_LANES", 4, 1, 4)
MAX_AUDIENCE_SEARCH_QUERIES = env_int("MAX_AUDIENCE_SEARCH_QUERIES", 4, 1, 8)
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


def build_disabled_sendgrid_execution_plan(data):
    """Build the final SendGrid execution envelope without performing network I/O."""
    if not isinstance(data, dict):
        raise ValueError("A JSON request body is required.")
    execution = validate_automation_execution_request(data)
    preflight = sendgrid_connector_preflight(data.get("sendgrid_settings"))
    blockers = list(execution["blockers"])
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
    if "connector_live_send_disabled" not in blockers:
        blockers.append("connector_live_send_disabled")
    return {
        "status": "blocked",
        "allowed": False,
        "sent": False,
        "mechanism": "sendgrid_mail_v3",
        "blockers": blockers,
        "idempotency_key": execution.get("idempotency_key"),
        "payload": payload,
        "endpoint": AUTOMATION_CONNECTOR_REGISTRY["sendgrid_mail_v3"]["endpoint"],
    }


def automation_rate_limit_contract(mechanism):
    """Return the configured bounded rate contract without consuming quota."""
    status = automation_connector_status(mechanism)
    limit = status.get("rate_limit_per_hour")
    return {
        "mechanism": status.get("mechanism"),
        "registered": status.get("registered") is True,
        "configured": status.get("configured") is True,
        "limit_per_hour": limit if isinstance(limit, int) else None,
        "enforcement_required": status.get("registered") is True,
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


def automation_execution_receipt(candidate, prerequisite_result):
    """Create a non-secret, non-sending audit receipt for an execution check."""
    return {
        "tracking_id": (candidate or {}).get("tracking_id"),
        "lead_name": (candidate or {}).get("name"),
        "mechanism": prerequisite_result.get("decision", {}).get("mechanism"),
        "allowed": prerequisite_result.get("allowed") is True,
        "sent": False,
        "blockers": list(prerequisite_result.get("blockers") or []),
        "checked_at": datetime.now(timezone.utc).isoformat(),
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
    if data.get("user_authorized") is not True:
        blockers.append("user_authorization_required")
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

    @app.route("/api/automation/connectors/sendgrid/execution-plan", methods=["POST", "OPTIONS"])
    def sendgrid_execution_plan():
        if request.method == "OPTIONS":
            return ("", 204)
        data = request.get_json(silent=True)
        try:
            return jsonify(build_disabled_sendgrid_execution_plan(data))
        except ValueError as error:
            return jsonify({"status": "error", "message": str(error)}), 400

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
