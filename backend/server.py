from http.server import BaseHTTPRequestHandler, HTTPServer
import json


HOST = "localhost"
PORT = 8080


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
                "version": "0.1"
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

            response = {
                "status": "success",
                "query": {
                    "need": need,
                    "location": location,
                    "goal": goal
                },
                "discovery": {
                    "stage": "backend-foundation",
                    "live_search": False,
                    "verification_enabled": False
                },
                "results": []
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
