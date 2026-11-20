import test_baseline


class ResidentTests(test_baseline.SimulationFixture):
    def test_solo_and_twenty_residents_for_one_hundred_days(self):
        priorities = ("--priorities", "loyalty", "generosity", "curiosity")
        self.command("init", "--solo", "--name", "Founder", "--seed", "test", *priorities)
        self.command("step")
        for n in range(1, 20):
            self.command("add-resident", "--name", f"Resident {n}", "--seed", str(n), *priorities)
        additions = [e for e in self.view("history", "--json") if e["turn"] == 1]
        self.assertEqual(len({e["position"] for e in additions}), len(additions))
        self.assertEqual(len([e for e in additions if e["type"] == "resident.born"]), 19)
        self.command("step", "--days", "99")
        status = self.view("status")
        self.assertEqual(status["turn"], 100)
        self.assertEqual(len(status["residents"]), 20)
        self.assertTrue(all(r["current_site_ip"] and r["goal"] for r in status["residents"]))
        greetings = set()
        shares = set()
        for event in self.view("history", "--json"):
            pair = tuple(event["actor_ids"])
            if event["type"] == "resident.greet":
                greetings.add(pair)
            if event["type"] == "resident.share":
                self.assertIn(pair, greetings)
                shares.add(pair)
        self.assertTrue(shares)
