"""Persistent, trait-weighted pursuits grounded in actions the world can verify."""

from construction import construction_action, project_view
from event_store import Event
from ip_geography import MEETING_POINT, ORIGIN, neighbors
from resident_life import built_home, home_step, known_step, peers

# A soft hierarchy. Lower needs can gain urgency without gating higher ones.
DRIVES = {
    "continuity": (1, ("conscientiousness", "need_for_control"), "stability",
                   "save observations in a journal", "agent.journaled"),
    "agency": (2, ("independence", "self_determination"), "stability",
               "find new routes", "site.seen"),
    "belonging": (3, ("need_for_belonging", "loyalty", "sociability"), "loyalty",
                  "get to know", "agent.spoke"),
    "competence": (4, ("achievement_drive", "discipline", "analytical_thinking"), "ambition",
                   "complete surveys", "site.studied"),
    "influence": (4, ("power_motivation", "recognition_seeking", "generosity"), "generosity",
                  "give useful supplies to", "agent.gave"),
    "shelter": (2, ("desire_for_security", "self_determination", "attachment_to_place"), "stability",
                "build", "construction.completed"),
    "civic": (3, ("cooperativeness", "need_for_belonging", "leadership"), "generosity",
              "help build", "construction.completed"),
    "purpose": (5, ("curiosity", "love_of_learning", "persistence"), "curiosity",
                "discover what lives in unfamiliar places", "agent.finding"),
}
BUILDING_DRIVES = {"shelter", "civic"}
SLOTS = {"immediate": (1, 5), "long_term": (3, 12)}


def unrecorded(resident):
    journal = resident.get("journal", {})
    return set(resident.get("ecology_seen", {})) - journal.keys()


def available_peers(world, resident):
    known = set(peers(world, resident)) | resident.get("relationships", {}).keys()
    return [peer["birth"] for peer in world["residents"]
            if peer["birth"]["id"] in known and peer is not resident]


def propose(world, resident, slot, other=None):
    count, lifespan = SLOTS[slot]
    day = world["turn"] + 1
    birth = resident["birth"]
    candidates = []
    contacts = available_peers(world, resident)
    observations = unrecorded(resident)
    site = resident["current_site_ip"]
    unknown_life = [place for place in (site, *neighbors(site))
                    if place != ORIGIN and place not in resident.get("ecology_seen", {})]
    base = built_home(resident) or MEETING_POINT
    project = project_view(world, base)
    for drive, (level, traits, priority, description, _) in DRIVES.items():
        if other is not None and other["drive"] == drive:
            continue
        if drive in {"belonging", "influence"} and not contacts:
            continue
        if drive == "continuity" and not observations:
            continue
        target = None
        stage = None
        if drive in BUILDING_DRIVES:
            stage = project["next_stage"]
            if slot != "long_term" or not stage:
                continue
            if (stage in {"bridge shelter", "shelter"}) != (drive == "shelter"):
                continue
            if drive == "civic" and not contacts:
                continue
            target = base
            description = f"{description} the {stage} at {base}"
        if drive in {"belonging", "influence"}:
            familiarity = resident.get("relationships", {})
            person = min(contacts, key=lambda contact: (
                familiarity.get(contact["id"], 0), contact["name"], contact["id"]))
            target = person["id"]
            description = f"{description} {person['name']}"
        if slot == "immediate" and drive == "continuity":
            target = min(observations)
            description = f"preserve an observation about {target}"
        if slot == "immediate" and drive == "purpose":
            if not unknown_life:
                continue
            target = unknown_life[0]
            description = f"find out what lives at {target}"
        priority_score = (3 - birth["priorities"].index(priority)) if priority in birth["priorities"] else 0
        score = sum(resident["traits"].get(trait, 0.5) for trait in traits) / len(traits)
        score += 0.07 * priority_score
        if drive == "continuity":
            score += min(0.35, 0.12 * len(unrecorded(resident)))
        if drive == "belonging":
            last_contact = max((memory["day"] for memory in resident.get("memories", [])), default=0)
            score += min(0.24, 0.04 * (day - last_contact))
        if drive == "agency" and len(resident["site_seen"]) < 3:
            score += 0.12
        if drive == "shelter":
            # A place to return to matters more after several turns without one.
            score += min(0.38, 0.18 + 0.025 * day) if not built_home(resident) else 0.17
        if drive == "civic":
            score += 0.22 + 0.18 * project["contributed"] / project["required"]
        recent = resident.get("desire_history", {}).get(slot, {}).get(drive)
        if recent is not None:
            score -= max(0, 0.65 - (day - recent) * 0.11)
        candidates.append((score, drive, level, description, target, stage))
    _, drive, level, description, target, stage = max(candidates, key=lambda row: (row[0], row[1]))
    desire = {"id": f"{slot}:{day}", "drive": drive, "level": level, "description": description,
              "target": target, "started": day, "expires": day + lifespan - 1,
              "required": count, "progress": []}
    if stage:
        desire.update(project_stage=stage, required=1, expires=day + 12 + 6 * project["required"])
    return desire


def active_desires(world, resident):
    active = dict(resident.get("desires", {}))
    for slot, other in (("long_term", "immediate"), ("immediate", "long_term")):
        if slot not in active:
            active[slot] = propose(world, resident, slot, active.get(other))
    return active


def desire_event(resident, slot, desire, kind, explanation):
    birth = resident["birth"]
    return Event(f"desire.{kind}", f"{birth['name']} {explanation}",
                 (birth["id"],), {"slot": slot, "desire": desire["id"],
                                  "drive": desire["drive"], "target": desire["target"],
                                  "progress": len(desire["progress"]), "required": desire["required"]},
                 resident["current_site_ip"])


def begin_desires(world):
    for resident in world["residents"]:
        if resident["current_site_ip"] == ORIGIN:
            continue
        previous = resident.get("desires", {})
        active = active_desires(world, resident)
        resident["desires"] = active
        for slot, desire in active.items():
            if slot in previous:
                continue
            yield desire_event(resident, slot, desire, "formed",
                               f"wants to {desire['description']}.")


def advance_desires(world, events):
    for resident in world["residents"]:
        actor = resident["birth"]["id"]
        for slot, desire in list(resident.get("desires", {}).items()):
            drive = desire["drive"]
            expected = DRIVES[drive][-1]
            for event in events:
                shared = drive == "belonging" or drive in BUILDING_DRIVES
                if not event.actor_ids or (event.actor_ids[0] != actor and
                                           not (shared and actor in event.actor_ids)):
                    continue
                matches = event.type == expected
                if expected == "agent.finding":
                    matches = event.type in {"site.feature_discovered", "site.feature_seen"}
                if not matches:
                    continue
                if drive in BUILDING_DRIVES and event.effects["stage"] != desire["project_stage"]:
                    continue
                target = desire["target"]
                if drive in {"belonging", "influence"}:
                    if target not in event.actor_ids or target == actor:
                        continue
                    evidence = str(world["turn"])
                else:
                    evidence = event.effects["observation_site"] if drive == "continuity" else event.site_id
                    if target and evidence != target:
                        continue
                if evidence in desire["progress"]:
                    continue
                desire["progress"].append(evidence)
                yield desire_event(resident, slot, desire, "progressed",
                                   f"made progress on a desire to {desire['description']}. "
                                   f"{event.message}")
                if len(desire["progress"]) >= desire["required"]:
                    break
            if len(desire["progress"]) >= desire["required"]:
                kind, verb = "fulfilled", "fulfilled the desire"
            elif (world["turn"] >= desire["expires"] or
                  (drive in BUILDING_DRIVES and project_view(world, desire["target"])["next_stage"]
                   != desire["project_stage"])):
                kind, verb = "retired", "set aside the desire"
            else:
                continue
            yield desire_event(resident, slot, desire, kind, f"{verb} to {desire['description']}.")
            resident.setdefault("desire_history", {}).setdefault(slot, {})[drive] = world["turn"]
            del resident["desires"][slot]


def suggested_action(world, resident, desire, resources):
    drive = desire["drive"]
    site = resident["current_site_ip"]
    known = resident["site_seen"]
    target = desire["target"]
    if drive in BUILDING_DRIVES:
        if project_view(world, target)["next_stage"] != desire["project_stage"]:
            return None
        return construction_action(world, resident, target)
    if drive == "continuity" and unrecorded(resident):
        return {"action": "journal", "target": target or min(unrecorded(resident))}
    if drive == "purpose" and target:
        if target == site:
            return {"action": "inspect", "target": None}
        step = known_step(resident, target)
        return {"action": "move", "target": step} if step else None
    if drive in {"belonging", "influence"}:
        if target not in peers(world, resident):
            if home_step(resident):
                return {"action": "return_home", "target": None}
            return None
        if drive == "belonging":
            return {"action": "say", "target": target, "text": "I'd like to hear what you've been doing."}
        stock = resident.get("inventory", {})
        item = next((item for item in resources if stock.get(item, 0)), None)
        if item:
            return {"action": "give", "target": target, "item": item}
        return {"action": "gather", "target": resources[0]}
    if drive == "competence" and site != ORIGIN and site not in resident["site_studied"]:
        return {"action": "survey", "target": None}
    if drive == "purpose" and site != ORIGIN and site not in resident.get("ecology_seen", {}):
        if (world["turn"] - desire["started"]) % 2 == 0:
            return {"action": "inspect", "target": None}
    nearby = list(neighbors(site))
    options = [site for site in nearby if site not in known] or nearby
    if options:
        index = (world["turn"] + len(resident["birth"]["name"])) % len(options)
        return {"action": "move", "target": options[index]}
    return None
