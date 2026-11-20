"""Persistent resident goals and social decisions."""
from collections import defaultdict

from event_store import Event


def initialize(resident):
    resident.setdefault("energy", 4)
    resident.setdefault("goal", None)
    resident.setdefault("relationships", {})
    resident.setdefault("memories", [])


def daily_decisions(world, home):
    groups = defaultdict(list)
    for resident in world["residents"]:
        groups[resident.get("current_site_ip", home)].append(resident)
    for site, group in groups.items():
        for index, resident in enumerate(group):
            if resident["energy"] == 0:
                resident.update(energy=4, goal="rest")
                continue
            resident["energy"] -= 1
            if len(group) == 1:
                resident["goal"] = "observe"
                continue
            peer = group[(index + 1) % len(group)]
            actor, target = resident["birth"], peer["birth"]
            previous = resident["relationships"].get(target["id"], 0)
            goal = "share" if previous >= 2 else "greet"
            resident["goal"] = goal
            resident["relationships"][target["id"]] = min(4, previous + 1)
            memory = {"day": world["turn"], "action": goal, "resident_id": target["id"]}
            resident["memories"] = [*resident["memories"][-7:], memory]
            verb = "shares local news with" if goal == "share" else "greets"
            yield Event(f"resident.{goal}", f"{actor['name']} {verb} {target['name']}.",
                        (actor["id"], target["id"]), {"memory": memory,
                         "familiarity": [previous, resident["relationships"][target["id"]]]}, site)
