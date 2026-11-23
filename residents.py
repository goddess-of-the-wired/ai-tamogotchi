"""Persistent resident goals and social decisions."""
from collections import defaultdict

from event_store import Event


def initialize(resident):
    resident.setdefault("energy", 4)
    resident.setdefault("goal", None)
    resident.setdefault("relationships", {})
    resident.setdefault("memories", [])


def regional_groups(residents, home):
    regions = {}
    for resident in residents:
        site = resident.get("current_site_ip", home)
        region = site.partition(".")[0] if "." in site else "legacy"
        regions.setdefault(region, defaultdict(list))[site].append(resident)
    for sites in regions.values():
        yield from sites.items()


def daily_decisions(world, home, *, reduced=True, controlled=(), excluded_site=None):
    for site, group in regional_groups(world["residents"], home):
        if site == excluded_site:
            continue
        group = [r for r in group if r["birth"]["id"] not in controlled]
        if reduced and len(group) == 1:
            resident = group[0]
            energy = resident["energy"]
            if energy:
                resident.update(energy=energy - 1, goal="observe")
            else:
                resident.update(energy=4, goal="rest")
            continue
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
