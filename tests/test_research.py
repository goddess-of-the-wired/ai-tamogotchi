import unittest

import test_baseline
from research import advance_research, resolve_possibility


class ResearchRulesTests(unittest.TestCase):
    def test_equivalent_proposals_share_locked_result(self):
        registry = {}
        rolls = []

        def roll(*args):
            rolls.append(args)
            return 99

        first = resolve_possibility(registry, {"kind": "Mineral assay", "site": "10.0.0.8",
                                               "description": "Check the mineral"}, roll)
        second = resolve_possibility(registry, {"kind": "mineral-assay", "site": "10.0.0.8",
                                                "description": "Try different words"}, roll)
        self.assertEqual(first, second)
        self.assertEqual(first[1]["status"], "impossible")
        self.assertEqual(len(rolls), 1)
        _, unknown = resolve_possibility(registry, {"kind": "flight", "site": "10.0.0.8"}, roll)
        self.assertEqual(unknown["status"], "unknown")
        self.assertEqual(len(rolls), 1)
        with self.assertRaises(ValueError):
            resolve_possibility(registry, {"kind": "flight", "site": "10.0.0.8",
                                           "effects": "change resident powers"}, roll)

    def test_failed_experiment_can_be_retested_and_misread(self):
        site = "10.0.0.8"
        world = {"map": {site: {"terrain": "mountain"}}, "residents": [
            {"birth": {"id": "a", "name": "Alex"},
             "site_studied": {site: {"day": 2, "mineral_trace": "quartz"}}},
            {"birth": {"id": "b", "name": "Robin"}},
        ]}
        draws = iter([0, 80, 0, 60, 85])
        events = []
        for world["turn"] in range(3, 9):
            events.extend(advance_research(world, lambda *_: next(draws)))
        a, b = world["research"]["notebooks"].values()
        self.assertFalse(a["completed"])
        self.assertEqual(a["interpretation"], "inconclusive")
        self.assertEqual(b["source_notebook"], a["id"])
        self.assertNotEqual(a["author"], b["author"])
        self.assertEqual(b["result"], "quartz")
        self.assertEqual(b["interpretation"], "misread")
        self.assertEqual(events[3].type, "research.resumed")
        self.assertEqual(world["map"], {site: {"terrain": "mountain"}})


class ResearchPersistenceTests(test_baseline.SimulationFixture):
    def test_autonomous_research_and_notebook_survive_cli_restarts(self):
        self.found_town()
        for _ in range(8):
            self.command("step", "--days", "8")
            state = self.view("status").get("research", {})
            linked = [n for n in state.get("notebooks", {}).values() if n["source_notebook"]]
            if linked:
                break
        self.assertTrue(linked)
        notebooks = state["notebooks"]
        continued = linked[0]
        source = notebooks[continued["source_notebook"]]
        self.assertNotEqual(source["author"], continued["author"])
        self.assertEqual(source["hypothesis"], continued["hypothesis"])
        self.assertLess(source["finished_day"], continued["started_day"])
        history = self.view("history", "--json")
        self.assertTrue(any(e["type"] == "research.resumed" for e in history))
        self.assertIn(continued["id"], self.view("status")["research"]["notebooks"])
