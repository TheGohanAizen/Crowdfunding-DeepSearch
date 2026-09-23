from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from urllib.parse import quote_plus, urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


HOST = "localhost"
PORT = 8080


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


def normalize_candidate(item, lane, query):
    """Normalize provider output into the internal candidate schema."""
    url = item.get("link") or item.get("url") or ""
    title = item.get("title") or "Untitled result"
    snippet = item.get("snippet") or item.get("description") or ""
    return {
        "name": title,
        "type": lane,
        "url": url,
        "snippet": snippet,
        "source": "google-custom-search",
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


def retrieve_candidates(search_plan, per_lane=5):
    """Retrieve candidates when Google Programmable Search credentials are configured."""
    api_key = os.environ.get("GOOGLE_CSE_API_KEY")
    engine_id = os.environ.get("GOOGLE_CSE_ID")

    if not api_key or not engine_id:
        return {
            "provider": "google-custom-search",
            "configured": False,
            "message": "Set GOOGLE_CSE_API_KEY and GOOGLE_CSE_ID on the server to enable live retrieval.",
            "candidates": []
        }

    candidates = []
    errors = []

    for plan in search_plan:
        params = urlencode({
            "key": api_key,
            "cx": engine_id,
            "q": plan["query"],
            "num": min(max(int(per_lane), 1), 10)
        })
        request = Request(
            "https://www.googleapis.com/customsearch/v1?" + params,
            headers={"User-Agent": "CrowdfundingDeepSearch/0.2"}
        )
        try:
            with urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
            for item in payload.get("items", []):
                candidates.append(normalize_candidate(item, plan["lane"], plan["query"]))
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            errors.append({"lane": plan["lane"], "error": str(error)})

    return {
        "provider": "google-custom-search",
        "configured": True,
        "message": "Live retrieval completed." if not errors else "Live retrieval completed with some provider errors.",
        "errors": errors,
        "candidates": deduplicate_candidates(candidates)
    }


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
    checked["official_source_verified"] = host.endswith((".gov", ".edu"))
    return checked


def verify_candidates(candidates, need, location):
    checked = [verification_signals(item, need, location) for item in candidates]
    checked.sort(key=lambda item: item.get("verification_score", 0), reverse=True)
    return checked


class DeepSearchHandler(BaseHTTPRequestHandler):

    def send_json(self, data, status=200):
        body = json.dumps(data, indent=2).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):

        if self.path == "/api/health":
            self.send_json({
                "status": "ok",
                "service": "Crowdfunding DeepSearch Backend",
                "version": "0.3"
            })
            return

        self.send_json({
            "error": "Not found"
        }, 404)

    def do_POST(self):

        if self.path != "/api/discover":
            self.send_json({
                "error": "Not found"
            }, 404)
            return

        try:
            content_length = int(
                self.headers.get("Content-Length", 0)
            )

            raw_body = self.rfile.read(content_length)

            data = json.loads(
                raw_body.decode("utf-8") or "{}"
            )

            need = data.get(
                "need",
                "General Financial Assistance"
            )

            location = data.get(
                "location",
                "Location not specified"
            )

            goal = data.get("goal")

            search_plan = build_discovery_queries(need, location)
            retrieval = retrieve_candidates(search_plan)
            verified_candidates = verify_candidates(retrieval["candidates"], need, location)

            response = {
                "status": "success",
                "query": {
                    "need": need,
                    "location": location,
                    "goal": goal
                },
                "discovery": {
                    "stage": "retrieval-provider-v1",
                    "live_search": retrieval["configured"],
                    "verification_enabled": True,
                    "provider": retrieval["provider"]
                },
                "search_plan": search_plan,
                "provider_status": {
                    "configured": retrieval["configured"],
                    "message": retrieval["message"],
                    "errors": retrieval.get("errors", [])
                },
                "verification_policy": {
                    "eligibility_claims": False,
                    "automatic_official_source_claims": False,
                    "note": "Scores are screening signals only; eligibility and program availability still require source-level verification."
                },
                "results": verified_candidates
            }

            self.send_json(response)

        except Exception as error:

            self.send_json({
                "status": "error",
                "message": str(error)
            }, 400)


def run_server():

    server = HTTPServer(
        (HOST, PORT),
        DeepSearchHandler
    )

    print(
        f"Crowdfunding DeepSearch backend running "
        f"at http://{HOST}:{PORT}"
    )

    print(
        f"Health check: "
        f"http://{HOST}:{PORT}/api/health"
    )

    server.serve_forever()


if __name__ == "__main__":
    run_server()
