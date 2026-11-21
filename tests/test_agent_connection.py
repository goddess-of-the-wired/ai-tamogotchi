import json
import socket
import sqlite3
import subprocess
import sys
import time
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


class AgentConnectionTests(unittest.TestCase):
    def test_two_executable_clients_and_durable_turn_receipts(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary) / "world"
            subprocess.run([sys.executable, ROOT / "glitch-server", "init",
                            "--directory", directory, "--seed", "test"], check=True, capture_output=True)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            server = subprocess.Popen([sys.executable, ROOT / "glitch-server", "serve",
                                       "--directory", directory, "--port", str(port),
                                       "--tick-seconds", "3"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                def call(path, name=None, payload=None):
                    headers = {"Content-Type": "application/json"}
                    if name:
                        token = (directory / f"{name}.token").read_text().strip()
                        headers["Authorization"] = "Bearer " + token
                    data = json.dumps(payload).encode() if payload is not None else None
                    with urlopen(Request(base + path, data=data, headers=headers), timeout=2) as response:
                        return json.load(response)

                deadline = time.monotonic() + 8
                while True:
                    try:
                        call("/health")
                        break
                    except URLError:
                        if time.monotonic() >= deadline:
                            self.fail("Agent server did not start.")
                        time.sleep(.05)
                with self.assertRaises(HTTPError) as unauthenticated:
                    call("/agent/context")
                self.assertEqual(unauthenticated.exception.code, 401)
                actors = {}
                for name in ("ivo", "mira"):
                    context = call("/agent/context", name)
                    actors[name] = context["character"]["id"]
                    self.assertEqual(context["character"]["name"].lower(), name)
                    self.assertNotIn("seed", context["character"])
                self.assertNotEqual(actors["ivo"], actors["mira"])
                with self.assertRaises(HTTPError) as invalid:
                    call("/agent/intents", "ivo", {"turn": 1, "action": "move", "target": "8.8.8.8"})
                self.assertEqual(invalid.exception.code, 400)

                clients = [[sys.executable, ROOT / "glitch-client", "--once", "--server", base,
                            "--token-file", directory / f"{name}.token"] for name in actors]
                for cmd in clients:
                    output = subprocess.check_output(cmd, text=True, timeout=4)
                    self.assertIn('"status": "queued"', output)
                self.assertIn("already queued", subprocess.check_output(clients[0], text=True, timeout=4))
                deadline = time.monotonic() + 8
                for name in actors:
                    while not (outcomes := call("/agent/results", name)["results"]):
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(.05)
                    self.assertEqual(len(outcomes), 1)
                    self.assertEqual(outcomes[0]["status"], "applied")
                    self.assertEqual(outcomes[0]["action"], "move")
                    self.assertIn("journey.moved", outcomes[0]["events"])
                    with closing(sqlite3.connect(directory / "world.sqlite3")) as db:
                        intent = json.loads(db.execute(
                            "SELECT intent FROM agent_intents WHERE resident=? AND turn=1",
                            (actors[name],)).fetchone()[0])
                    self.assertEqual(call("/agent/intents", name, intent), outcomes[0])

                with self.assertRaises(HTTPError) as stale:
                    call("/agent/intents", "ivo", {"turn": 1, "action": "wait", "target": None})
                self.assertEqual(stale.exception.code, 409)
            finally:
                server.terminate()
                server.wait(timeout=5)

    def test_ollama_adapter_requests_json_and_returns_an_intent(self):
        from argparse import Namespace
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from runpy import run_path
        from threading import Thread

        requests = []

        class LocalModel(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                response = json.dumps({"message": {"content": '{"action":"wait","target":null}'}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *args):
                pass

        choose = run_path(str(ROOT / "glitch-client"))["choose"]
        with HTTPServer(("127.0.0.1", 0), LocalModel) as model:
            worker = Thread(target=model.serve_forever)
            worker.start()
            try:
                args = Namespace(ollama_model="test-model", policy_timeout=3,
                                 ollama_url=f"http://127.0.0.1:{model.server_port}")
                self.assertEqual(choose({"turn": 7}, args),
                                 {"turn": 7, "action": "wait", "target": None})
            finally:
                model.shutdown()
                worker.join(timeout=3)
        [(route, body)] = requests
        self.assertEqual(route, "/api/chat")
        self.assertEqual(body["format"], "json")
        self.assertFalse(body["stream"])
        self.assertEqual(json.loads(body["messages"][1]["content"]), {"turn": 7})
