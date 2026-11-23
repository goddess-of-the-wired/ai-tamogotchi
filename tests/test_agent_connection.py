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
                unauthenticated.exception.close()
                duplicate = subprocess.run(
                    [sys.executable, ROOT / "glitch-server", "serve",
                     "--directory", directory, "--port", str(port)],
                    capture_output=True, text=True, timeout=2,
                )
                self.assertEqual(duplicate.returncode, 1)
                self.assertIn("Another agent server", duplicate.stderr)
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
                invalid.exception.close()

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
                stale.exception.close()
            finally:
                server.terminate()
                server.wait(timeout=5)

    def test_resident_life_persists_across_turns(self):
        from io import BytesIO
        from runpy import run_path

        import glitch
        from agent_protocol import AgentTurns, context_view
        from event_store import canonical_history
        from ip_geography import GATEWAYS, ORIGIN

        server = run_path(str(ROOT / "glitch-server"))
        with TemporaryDirectory() as temporary:
            directory = Path(temporary) / "world"
            server["initialize"](directory, "resident-life")
            with closing(sqlite3.connect(directory / "world.sqlite3")) as db:
                controller = AgentTurns(db)

                def submit(actor, turn, action, target=None, **extra):
                    body = json.dumps(dict(turn=turn, action=action, target=target, **extra)).encode()
                    return server["AgentAPI"].submit(db, actor, {
                        "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(body)),
                        "wsgi.input": BytesIO(body),
                    })

                world = glitch.load(db)
                _, mira, ivo = world["residents"]
                speaker, listener = (resident["birth"]["id"] for resident in (mira, ivo))
                mira["inventory"] = {"fiber": 2, "stone": 1}
                with db:
                    glitch.save(db, world)
                view = context_view(world, mira)
                self.assertIn(listener, {peer["id"] for peer in view["peers"]})
                self.assertLessEqual(len(view["character"]["traits"]), 8)
                text = "I saw fish beyond the gateway."
                submit(speaker, 1, "say", listener, text=text)
                controller.last_seen_turn[listener] = 1
                glitch.step(db, controller=controller)
                world = glitch.load(db)
                self.assertEqual(world["residents"][2]["memories"][-1]["text"], text)
                self.assertNotIn(text, json.dumps(glitch.world_view(world, False)))
                self.assertNotIn(text, json.dumps(canonical_history(db)))
                stored = db.execute("SELECT intent FROM agent_intents WHERE turn = 1").fetchone()[0]
                self.assertTrue(stored.startswith("sha256:"))
                self.assertEqual(submit(speaker, 1, "say", listener, text=text)["status"], "applied")
                self.assertFalse({"resident.greet", "resident.share"} &
                                 {event["type"] for event in canonical_history(db)})

                submit(speaker, 2, "offer_trade", listener, item="fiber")
                submit(listener, 2, "wait")
                glitch.step(db, controller=controller)
                world = glitch.load(db)
                self.assertEqual(context_view(world, world["residents"][2])["offers"][speaker]["item"],
                                 "fiber")
                submit(speaker, 3, "offer_trade", listener, item="stone")
                submit(listener, 3, "accept_trade", speaker)
                glitch.step(db, controller=controller)
                world = glitch.load(db)
                self.assertEqual(world["residents"][2]["inventory"]["fiber"], 1)
                self.assertEqual(world["residents"][2]["hunger"], 3)
                submit(listener, 4, "buy_food")
                submit(speaker, 4, "give", listener, item="stone")
                glitch.step(db, controller=controller)
                world = glitch.load(db)
                self.assertEqual(world["residents"][2]["inventory"]["stone"], 1)
                self.assertEqual(world["residents"][2]["hunger"], 2)

                site = GATEWAYS[0]
                submit(listener, 5, "move", site)
                glitch.step(db, controller=controller)
                world = glitch.load(db)
                ivo = world["residents"][2]
                ivo["ecology_seen"][site] = {"day": 5, "label": world["site_features"][site]["label"]}
                with db:
                    glitch.save(db, world)
                self.assertIn("honey", context_view(world, ivo)["resources"])
                with self.assertRaisesRegex(ValueError, "hungry"):
                    submit(listener, 6, "survey")
                submit(listener, 6, "gather", "honey")
                glitch.step(db, controller=controller)
                ivo = glitch.load(db)["residents"][2]
                self.assertEqual(ivo["inventory"]["honey"], 1)
                self.assertEqual(ivo["hunger"], 2)
                submit(listener, 7, "return_home")
                glitch.step(db, controller=controller)
                ivo = glitch.load(db)["residents"][2]
                self.assertEqual(ivo["current_site_ip"], ORIGIN)
                self.assertEqual(ivo["hunger"], 1)

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
