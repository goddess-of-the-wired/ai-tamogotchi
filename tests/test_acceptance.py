"""Seeded acceptance scenario driven entirely through the public CLI."""
import hashlib

from event_store import encode
from ip_geography import GATEWAYS, ORIGIN, neighbors
import test_baseline
import test_observer


class InheritedResearchTests(test_baseline.SimulationFixture):
    signed_request = test_observer.ObserverTests.signed_request

    def test_mountain_notebook_handoff_after_restart(self):
        self.command("init", "--name", "Alex", "--seed", "acceptance-15",
                     "--priorities", "loyalty", "generosity", "curiosity")
        self.assertEqual(self.view("observer", "map")[ORIGIN]["name"], "God Stone")
        self.command("step", "--days", "17")
        halfway = self.view("status")
        pending = next(r for r in halfway["residents"] if r["birth"]["name"] == "Ivo")
        self.assertGreater(pending["journey"]["route_index"], 0)
        self.assertLess(pending["journey"]["route_index"], 10)
        self.command("step", "--days", "26")

        site = "172.16.0.51"
        author = pending["birth"]["id"]
        history = self.view("history", "--json")
        previous = None
        for event in history:
            self.assertEqual(event["previous_hash"], previous)
            payload = {k: v for k, v in event.items() if k not in ("event_id", "event_hash")}
            actual = hashlib.sha256(b"glitch-event-v1\0" + encode(payload).encode()).hexdigest()
            self.assertEqual(event["event_hash"], f"sha256:{actual}")
            previous = event["event_hash"]

        path = pending["journey"]["path"]
        self.assertEqual(path[0], ORIGIN)
        self.assertIn(path[1], GATEWAYS)
        moves = [e["effects"]["current_site_ip"] for e in history
                 if e["type"] == "journey.moved"
                 and e["effects"]["journey_id"] == pending["journey_id"]]
        self.assertEqual(moves, [[a, b] for a, b in zip(path, path[1:])])
        for start, end in moves:
            self.assertIn(end, neighbors(start))
        mountain = self.view("observer", "map")[site]
        self.assertEqual(mountain["terrain"], "mountain")
        self.assertEqual(mountain["discovered_by"], author)
        self.assertEqual(sum(e["type"] == "site.discovered" and e["site_id"] == site
                             for e in history), 1)
        started, resumed = [e for e in history if e["site_id"] == site and
                            e["type"] in ("research.started", "research.resumed")]
        self.assertEqual(started["effects"]["possibility"], resumed["effects"]["possibility"])
        source, continuation = [e["effects"]["notebook"] for e in history
                                if e["type"] == "research.notebook" and e["site_id"] == site]
        self.assertIsNone(source["result"])
        self.assertEqual(continuation["result"], "mountain")
        self.assertEqual(continuation["interpretation"], "misread")
        self.assertEqual(continuation["source_notebook"], source["id"])
        self.assertNotEqual(continuation["author"], source["author"])
        state = self.view("status")
        self.assertNotEqual(next(r for r in state["residents"] if r["birth"]["id"] == author)
                            ["current_site_ip"], site)

        request, signature = self.signed_request("Observer")
        resident = self.command("birth-admit", str(request), str(signature)).stdout.strip()
        self.assertTrue(self.view("observer", "timeline", "--resident", resident))
        args = ("observer", "timeline", "--resident", continuation["author"])
        self.assertTrue(any(e["type"] == "research.resumed" for e in self.view(*args)))
        self.assertNotEqual(self.command("observer", "step", check=False).returncode, 0)

        snapshot = self.db.with_name("acceptance.sqlite3")
        self.command("backup", str(snapshot))
        self.assertEqual(self.view("status", database=snapshot), self.view("status"))
        self.assertEqual(self.view("observer", "map", database=snapshot)[site], mountain)
        for notebook in (source, continuation):
            digest = notebook["artifact"]
            data = self.command("observer", "artifact", digest, database=snapshot).stdout
            self.assertEqual(hashlib.sha256(data.encode()).hexdigest(), digest)
