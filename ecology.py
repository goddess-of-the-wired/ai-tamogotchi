"""Resolve persistent signs of wildlife and vegetation after perception rolls."""

import hashlib
from random import Random

from event_store import Event

ECOLOGY_VERSION = 1
HABITATS = {
    "grassland": ("a vole burrow", "a trail of bison tracks", "a patch of wildflowers",
                  "a colony of ground-nesting bees", "a stand of tall seed grass"),
    "forest": ("a woodpecker nest in a dead trunk", "deer tracks in soft soil",
               "a stand of old oaks", "a cluster of shelf fungi", "a fox den beneath roots"),
    "mountain": ("a narrow trail of goat tracks", "an eagle nest on a ledge",
                 "a patch of alpine flowers", "a marmot burrow among the rocks",
                 "a seam of exposed quartz"),
    "lake": ("a frog spawning pool", "a reed bed sheltering waterfowl",
             "a cluster of freshwater mussel shells", "fish feeding in the shallows",
             "a patch of water lilies"),
    "river": ("an otter slide on the bank", "a beaver dam", "a colony of mayflies",
              "a gravel bed used by spawning fish", "a stand of willows"),
}


def ecology_rng(seed, purpose):
    key = f"{seed}:ecology:v{ECOLOGY_VERSION}:{purpose}".encode()
    return Random(hashlib.sha256(key).digest())


def observe_ecology(world, resident, site):
    terrain = world["map"][site]["terrain"]
    if terrain not in HABITATS:
        return []
    noticed = resident.setdefault("ecology_seen", {})
    if site in noticed:
        return []
    day = world["turn"]
    birth = resident["birth"]
    traits = resident["traits"]
    skill = (traits["observational_skill"] + traits["attention_to_detail"]) / 2
    bonus = round(6 * skill)
    difficulty = 13 if terrain == "forest" else 11
    roll = ecology_rng(birth["seed"], f"observe:{site}:{day}").randint(1, 20)
    evidence = {"roll": roll, "bonus": bonus, "difficulty": difficulty,
                "rule_version": ECOLOGY_VERSION}
    actor = (birth["id"],)
    if roll + bonus < difficulty:
        return [Event("site.observation_missed",
                      f"{birth['name']} passes through without noticing a distinctive feature.",
                      actor, evidence, site)]

    features = world.setdefault("site_features", {})
    events = []
    if site not in features:
        label = ecology_rng(world["seed"], f"feature:{site}").choice(HABITATS[terrain])
        features[site] = {"label": label, "day": day, "discoverer": birth["id"],
                          "rule_version": ECOLOGY_VERSION}
        events.append(Event("site.feature_discovered", f"{birth['name']} discovers {label} at {site}.",
                            actor, {"feature": features[site], **evidence}, site))
    else:
        label = features[site]["label"]
        events.append(Event("site.feature_seen", f"{birth['name']} notices {label} at {site}.",
                            actor, {"feature": label, **evidence}, site))
    noticed[site] = {"day": day, "label": label}
    return events
