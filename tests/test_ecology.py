from unittest.mock import patch

import test_baseline
from ecology import HABITATS, observe_ecology


class EcologyTests(test_baseline.SimulationFixture):
    def test_failed_check_leaves_site_unresolved_and_peers_share_a_finding(self):
        site = "10.0.0.12"
        world = {"seed": "world", "turn": 1, "map": {site: {"terrain": "forest"}}}

        def visitor(name):
            return {"birth": {"name": name, "id": name, "seed": name},
                    "traits": {"observational_skill": .5, "attention_to_detail": .5}}

        ivo = visitor("Ivo")
        mira = visitor("Mira")
        with patch("ecology.ecology_rng") as rng:
            rng.return_value.randint.return_value = 4
            missed, = observe_ecology(world, ivo, site)
            self.assertEqual(missed.type, "site.observation_missed")
            self.assertEqual(missed.effects["roll"], 4)
            self.assertNotIn("site_features", world)
            rng.return_value.randint.return_value = 18
            rng.return_value.choice.return_value = HABITATS["forest"][0]
            world["turn"] = 2
            discovery, = observe_ecology(world, mira, site)
            world["turn"] = 3
            seen, = observe_ecology(world, ivo, site)
            self.assertEqual(discovery.type, "site.feature_discovered")
            self.assertEqual(seen.type, "site.feature_seen")
            self.assertEqual(seen.effects["feature"], discovery.effects["feature"]["label"])
            self.assertEqual(world["site_features"][site]["discoverer"], "Mira")
            rng.return_value.choice.assert_called_once()
            self.assertEqual(observe_ecology(world, ivo, site), [])

    def test_real_turns_record_habitat_findings_and_keep_misses_private(self):
        self.found_town()
        self.command("step", "--days", "24")
        status = self.view("status")
        features = status["site_features"]
        self.assertTrue(features)
        self.assertEqual(features, self.view("status")["site_features"])
        for site, record in features.items():
            self.assertIn(record["label"], HABITATS[status["map"][site]["terrain"]])
        full = self.view("history", "--json")
        public = self.view("observer", "timeline")
        self.assertTrue(any(event["type"] == "site.observation_missed" for event in full))
        self.assertFalse(any(event["type"] == "site.observation_missed" for event in public))
        self.assertEqual(sum(event["type"] == "site.feature_discovered" for event in public), len(features))
