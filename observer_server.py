"""Loopback preview of the observer beta and creator-signed admission API."""

import argparse
import base64
import json
import sqlite3
import subprocess
import time
from collections import Counter
from contextlib import closing
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIServer, make_server

import glitch
from construction import project_view
from event_store import encode
from observer import PUBLIC_EVENTS, birth_request, observer_status, verify_birth

ASSETS = Path(__file__).with_name("observer_web")
CHALLENGE_TTL = 300
CHALLENGE_LIMIT = 20
PAGE_SIZE = 50


class LanObserverServer(ThreadingMixIn, WSGIServer):
    """An idle browser connection cannot block other observer requests."""

    daemon_threads = True
    request_queue_size = 32

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address


class HTTPError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class ObserverApp:
    def __init__(self, database, port, *, clock=time.monotonic, lan=None):
        self.database = Path(database).resolve()
        self.hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
        self.lan = lan
        if lan:
            self.hosts = {f"{lan.ip}:{port}"}
        self.clock = clock
        self.challenges = {}
        self.recent_birth_requests = []

    def open_world(self, writable=False):
        mode = "rw" if writable else "ro"
        return closing(sqlite3.connect(self.database.as_uri() + "?mode=" + mode, uri=True))

    def __call__(self, env, start_response):
        try:
            if env.get("HTTP_HOST") not in self.hosts:
                raise HTTPError("403 Forbidden", "Use the configured loopback address.")
            if self.lan and IPv4Address(env["REMOTE_ADDR"]) not in self.lan.network:
                raise HTTPError("403 Forbidden", "This preview is limited to the local subnet.")
            method, path = env["REQUEST_METHOD"], env["PATH_INFO"]
            if self.lan and method != "GET":
                raise HTTPError("405 Method Not Allowed", "The LAN preview is read-only.")
            if method == "POST" and path in ("/api/birth/challenge", "/api/birth/admit"):
                origin = env.get("HTTP_ORIGIN")
                if origin and origin != "http://" + env["HTTP_HOST"]:
                    raise HTTPError("403 Forbidden", "Birth requests must be same-origin.")
                result = self.birth_route(path, self.read_body(env))
                status, mime, data = "200 OK", "application/json", encode(result).encode()
            elif method == "GET":
                status, mime, data = self.read_route(path, env.get("QUERY_STRING", ""))
            else:
                raise HTTPError("405 Method Not Allowed", "Method unavailable.")
        except HTTPError as error:
            status, mime, data = error.status, "application/json", encode({"error": str(error)}).encode()
        except (ValueError, TypeError) as error:
            status, mime, data = "400 Bad Request", "application/json", encode({"error": str(error)}).encode()
        except (OSError, sqlite3.Error, subprocess.TimeoutExpired):
            status, mime, data = "503 Service Unavailable", "application/json", b'{"error":"World unavailable."}'
        start_response(status, [
            ("Content-Type", mime), ("Content-Length", str(len(data))), ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
            ("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'"),
        ])
        return [data]

    @staticmethod
    def read_body(env):
        if env.get("CONTENT_TYPE", "").split(";")[0] != "application/json":
            raise HTTPError("415 Unsupported Media Type", "Use JSON.")
        length = int(env.get("CONTENT_LENGTH") or "0")
        if not 0 < length <= 4096:
            raise HTTPError("413 Content Too Large", "Invalid request size.")
        body = json.loads(env["wsgi.input"].read(length))
        if not isinstance(body, dict):
            raise ValueError("Expected a JSON object.")
        return body

    @staticmethod
    def load_world(db):
        world = glitch.load(db)
        if world is None:
            raise HTTPError("503 Service Unavailable", "Initialize a world first.")
        return world

    def birth_route(self, path, body):
        now = self.clock()
        self.challenges = {key: state for key, state in self.challenges.items() if state[0] > now}
        if path.endswith("/challenge"):
            self.recent_birth_requests = [deadline for deadline in self.recent_birth_requests if deadline > now]
            if len(self.recent_birth_requests) >= CHALLENGE_LIMIT:
                raise HTTPError("429 Too Many Requests", "Try again after the challenge window.")
            if set(body) != {"name", "priorities", "key"}:
                raise ValueError("Provide a name, three priorities, and an Ed25519 public key.")
            name, priorities, key = body["name"], body["priorities"], body["key"]
            if (not isinstance(name, str) or not 0 < len(name.strip()) <= 80 or not name.strip().isprintable()
                    or not isinstance(priorities, list) or len(priorities) != 3
                    or any(not isinstance(p, str) or p not in glitch.PRIORITIES for p in priorities)
                    or len(set(priorities)) != 3 or not isinstance(key, str) or len(key) != 60):
                raise ValueError("Invalid name, priorities or public key.")
            with self.open_world() as db:
                world = self.load_world(db)
            request = birth_request(world["residents"][0]["birth"]["id"],
                                    world.get("geography", {}).get("origin", glitch.HOME),
                                    name.strip(), priorities, key)
            identifier = request["resident_id"]
            self.challenges[identifier] = (now + CHALLENGE_TTL, request)
            self.recent_birth_requests.append(now + CHALLENGE_TTL)
            return {"id": identifier, "payload": encode(request)}

        if set(body) != {"id", "signature"}:
            raise ValueError("Provide a challenge ID and signature.")
        if not isinstance(body["id"], str) or not isinstance(body["signature"], str):
            raise ValueError("Invalid challenge ID or signature.")
        issued = self.challenges.pop(body["id"], None)
        if issued is None:
            raise HTTPError("409 Conflict", "Challenge expired or already used.")
        signature = base64.b64decode(body["signature"], validate=True)
        request = issued[1]
        certificate = verify_birth(request, signature, glitch.PRIORITIES)
        with self.open_world(writable=True) as db:
            db.execute("PRAGMA foreign_keys = ON")
            glitch.migrate(db)
            try:
                resident = glitch.admit_signed_birth(db, request, certificate)
            except ValueError as error:
                if "limit" in str(error):
                    raise HTTPError("429 Too Many Requests", str(error)) from error
                raise
        return {"resident": resident}

    def read_route(self, path, query):
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/observer.css": ("observer.css", "text/css; charset=utf-8"),
                  "/observer.js": ("observer.js", "text/javascript; charset=utf-8")}
        if path in assets:
            file, mime = assets[path]
            return "200 OK", mime, (ASSETS / file).read_bytes()
        if path not in ("/api/status", "/api/map", "/api/chronicle", "/api/digest"):
            raise HTTPError("404 Not Found", "Observer route unavailable.")
        options = parse_qs(query, max_num_fields=4)
        offset = int(options.get("offset", ["0"])[0])
        if not 0 <= offset <= 1_000_000:
            raise ValueError("Invalid page offset.")
        resident = options.get("resident", [None])[0]
        with self.open_world() as db:
            db.execute("BEGIN")
            if path == "/api/status":
                result = observer_status(self.load_world(db))
                result["total"] = len(result["residents"])
                result["residents"] = result["residents"][offset:offset + PAGE_SIZE]
                result["priorities"] = glitch.PRIORITIES
                result["birth_enabled"] = self.lan is None
            elif path == "/api/map":
                world = self.load_world(db)
                origin = world.get("geography", {}).get("origin", glitch.HOME)
                sites = world["map"]
                keys = sorted(sites)
                features = world.get("site_features", {})
                page = [sites[key].copy() for key in keys[offset:offset + PAGE_SIZE]]
                projects = world.get("construction", {})
                for cell in page:
                    site = cell["id"]
                    if site in features:
                        cell["finding"] = features[site]["label"]
                    if site in projects:
                        view = project_view(world, site)
                        cell["construction"] = {key: value for key, value in view.items()
                                                if key != "builders"}
                result = {"total": len(keys), "origin": sites.get(origin), "sites": page}
            else:
                entries = self.public_entries(db, resident, offset)
                if path == "/api/digest":
                    counts = Counter(entry["type"] for entry in entries)
                    result = {"sample_size": len(entries), "counts": counts, "latest": entries[:5]}
                else:
                    result = {"events": entries, "next_offset": offset + PAGE_SIZE
                              if len(entries) == PAGE_SIZE else None}
        return "200 OK", "application/json", encode(result).encode()

    @staticmethod
    def public_entries(db, resident, offset):
        if db.execute("PRAGMA user_version").fetchone()[0] == 0:
            return []
        timeline_events = PUBLIC_EVENTS | {"desire.formed", "desire.progressed", "desire.fulfilled",
                                           "desire.retired", "construction.contributed",
                                           "construction.completed"}
        placeholders = ",".join("?" for _ in timeline_events)
        actor_filter = """AND EXISTS (
            SELECT 1 FROM json_each(e.record, '$.actor_ids') WHERE value = ?
        )""" if resident else ""
        matches = db.execute(f"""SELECT e.record, p.message FROM canonical_events e
            JOIN events p ON p.turn = e.turn AND p.position = e.position
            WHERE e.type IN ({placeholders}) {actor_filter}
            ORDER BY e.turn DESC, e.position DESC LIMIT ? OFFSET ?""",
            (*sorted(timeline_events), *((resident,) if resident else ()), PAGE_SIZE, offset))
        return [{"id": record["event_id"], "turn": record["turn"], "type": record["type"],
                 "site": record["site_id"], "actors": record["actor_ids"], "message": message}
                for encoded, message in matches for record in [json.loads(encoded)]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--lan-read-only", type=IPv4Interface, metavar="ADDRESS/PREFIX",
                        help="serve reads on an IPv4 private subnet")
    args = parser.parse_args()
    lan = args.lan_read_only
    private = tuple(map(IPv4Network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))
    if lan and not any(lan.network.subnet_of(network) for network in private):
        parser.error("The LAN subnet must fit within an RFC 1918 range.")
    app = ObserverApp(args.db, args.port, lan=lan)
    host = str(lan.ip) if lan else "127.0.0.1"
    server_class = LanObserverServer if lan else WSGIServer
    with make_server(host, args.port, app, server_class=server_class) as server:
        print(f"Glitch World observer at http://{host}:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
