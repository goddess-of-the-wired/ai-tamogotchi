import base64
import io
import json
import subprocess
from ipaddress import IPv4Interface
from threading import Thread
from urllib.request import urlopen
from wsgiref.simple_server import make_server

import test_baseline
from observer_server import CHALLENGE_LIMIT, ObserverApp


class ObserverServerTests(test_baseline.SimulationFixture):
    def setUp(self):
        super().setUp()
        self.found_town()
        self.now = 0
        self.app = ObserverApp(self.db, 8765, clock=lambda: self.now)

    def call(self, path, body=None, *, host="localhost:8765", origin=None, method=None, query="", peer="127.0.0.1"):
        data = json.dumps(body).encode() if body is not None else b""
        env = {"HTTP_HOST": host, "REQUEST_METHOD": method or ("POST" if body is not None else "GET"),
               "PATH_INFO": path, "REMOTE_ADDR": peer, "QUERY_STRING": query, "CONTENT_TYPE": "application/json",
               "CONTENT_LENGTH": str(len(data)), "wsgi.input": io.BytesIO(data)}
        if origin:
            env["HTTP_ORIGIN"] = origin
        status = []
        response = b"".join(self.app(env, lambda value, headers: status.extend((value, headers))))
        return int(status[0][:3]), json.loads(response)

    def make_challenge(self, name):
        root = self.db.parent
        private, request, signature = [root / item for item in ("web.pem", "payload", "signature")]
        if not private.exists():
            subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", private],
                           check=True, capture_output=True)
        key = subprocess.check_output(["openssl", "pkey", "-in", private, "-pubout", "-outform", "DER"])
        status, body = self.call("/api/birth/challenge", {
            "name": name, "priorities": ["loyalty", "curiosity", "stability"],
            "key": base64.b64encode(key).decode(),
        })
        self.assertEqual(status, 200)
        request.write_text(body["payload"])
        subprocess.run(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", private,
                        "-in", request, "-out", signature], check=True, capture_output=True)
        return {"id": body["id"], "signature": base64.b64encode(signature.read_bytes()).decode()}

    def test_signed_birth_scoped_timeline_and_no_action_routes(self):
        signature = self.make_challenge("Robin")
        status, birth = self.call("/api/birth/admit", signature)
        self.assertEqual(status, 200)
        actor = birth["resident"]
        status, roster = self.call("/api/status")
        self.assertEqual(status, 200)
        resident = next(r for r in roster["residents"] if r["id"] == actor)
        self.assertEqual(resident["name"], "Robin")
        self.assertFalse({"traits", "seed", "relationships"} & resident.keys())
        _, timeline = self.call("/api/chronicle", query="resident=" + actor)
        self.assertEqual(len(timeline["events"]), 1)
        self.assertEqual(timeline["events"][0]["actors"], [actor])
        self.assertNotIn("effects", timeline["events"][0])
        _, digest = self.call("/api/digest", query="resident=" + actor)
        self.assertEqual(digest["counts"], {"resident.born": 1})
        self.assertEqual(self.call("/api/birth/admit", signature)[0], 409)
        self.assertEqual(self.call("/api/birth/admit", self.make_challenge("Second"))[0], 429)
        self.assertEqual(self.call("/api/step", {}, method="POST")[0], 405)
        self.assertEqual(self.call("/api/status", query="show_hidden=true")[1]["turn"], 0)

    def test_challenge_expiry_signature_checks_quotas_and_origin(self):
        signed = self.make_challenge("Expired")
        self.now += 301
        self.assertEqual(self.call("/api/birth/admit", signed)[0], 409)
        forged = self.make_challenge("Forged")
        forged["signature"] = base64.b64encode(bytes(64)).decode()
        self.assertEqual(self.call("/api/birth/admit", forged)[0], 400)
        for _ in range(CHALLENGE_LIMIT - 1):
            self.make_challenge("Queued")
        self.assertEqual(self.call("/api/birth/challenge", {})[0], 429)
        self.assertEqual(self.call("/api/birth/challenge", {}, origin="https://other.example")[0], 403)
        self.assertEqual(self.call("/api/status", host="other.example")[0], 403)
        self.assertEqual(self.call("/api/map", query="offset=-1")[0], 400)

    def test_lan_preview_limits_clients_and_rejects_all_writes(self):
        self.app = ObserverApp(self.db, 8765, lan=IPv4Interface("192.168.0.2/24"))
        host = "192.168.0.2:8765"
        status, data = self.call("/api/status", host=host, peer="192.168.0.4")
        self.assertEqual(status, 200)
        self.assertFalse(data["birth_enabled"])
        self.assertEqual(self.call("/api/status", host=host, peer="203.0.113.1")[0], 403)
        self.assertEqual(self.call("/api/birth/challenge", {}, host=host, peer="192.168.0.4")[0], 405)

    def test_live_loopback_serves_assets_map_and_security_headers(self):
        with make_server("127.0.0.1", 0, None) as server:
            port = server.server_port
            server.set_app(ObserverApp(self.db, port))
            thread = Thread(target=server.serve_forever)
            thread.start()
            try:
                base = f"http://127.0.0.1:{port}"
                with urlopen(base, timeout=3) as response:
                    self.assertIn(b"Glitch World", response.read())
                    self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
                with urlopen(base + "/observer.js", timeout=3) as response:
                    self.assertIn(b"Ed25519", response.read())
                with urlopen(base + "/api/map", timeout=3) as response:
                    self.assertEqual(json.load(response)["origin"]["name"], "God Stone")
            finally:
                server.shutdown()
                thread.join(timeout=3)
