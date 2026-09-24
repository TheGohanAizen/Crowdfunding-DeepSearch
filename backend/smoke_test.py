import json
import os
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = os.environ.copy()
env["HOST"] = "127.0.0.1"
env["PORT"] = "8099"

process = subprocess.Popen(
    [sys.executable, os.path.join(ROOT, "backend", "server.py")],
    env=env,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

try:
    last_error = None
    for _ in range(20):
        try:
            with urlopen("http://127.0.0.1:8099/api/health", timeout=1) as response:
                payload = json.loads(response.read().decode("utf-8"))
            assert payload["status"] == "ok"
            assert payload["service"] == "Crowdfunding DeepSearch Backend"
            print("Smoke test passed:", payload)
            break
        except Exception as error:
            last_error = error
            time.sleep(0.25)
    else:
        raise RuntimeError("Backend did not become healthy: " + str(last_error))
finally:
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
