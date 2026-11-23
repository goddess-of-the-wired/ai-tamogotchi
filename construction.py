"""Resident-built places in connected worlds."""

from economy import RESOURCE_BY_TERRAIN, identity
from event_store import Event
from ip_geography import MEETING_POINT, ORIGIN
from resident_life import built_home, home_step

# Each stage takes fresh materials and a separate contribution from each required builder.
STAGES = (
    ("bridge shelter", 2, 1),
    ("shelter", 4, 1),
    ("settlement", 8, 2),
    ("community", 12, 2),
    ("village", 18, 2),
)


def project_view(world, site):
    if site == ORIGIN:
        return {"stage": None, "next_stage": None, "members": 0}
    project = world.get("construction", {}).get(site, {})
    completed = project.get("completed", 0)
    stage = STAGES[completed - 1][0] if completed else None
    summary = {"stage": stage, "members": len(project.get("members", []))}
    if completed == len(STAGES):
        return {**summary, "next_stage": None}
    name, cost, builders = STAGES[completed]
    return {**summary, "next_stage": name, "material": RESOURCE_BY_TERRAIN.get(world["map"].get(site, {}).get("terrain"), "stone"),
            "required": cost, "contributed": project.get("contributed", 0),
            "builders_required": builders, "builders": sorted(project.get("builders", []))}


def construction_action(world, resident):
    site = resident["current_site_ip"]
    home = built_home(resident) or MEETING_POINT
    view = project_view(world, home)
    if view["next_stage"] is None:
        return None
    if site != home:
        return {"action": "return_home", "target": None} if home_step(resident) else None
    if remaining_for(resident, view) <= 0:
        return None
    material = view["material"]
    if resident.get("inventory", {}).get(material, 0):
        return {"action": "build", "target": view["next_stage"]}
    return {"action": "gather", "target": material}


def remaining_for(resident, view):
    stock_slots = view["required"] - view["contributed"]
    missing_builders = max(0, view["builders_required"] - len(view["builders"]))
    # Leave room for another builder so one resident cannot fill all the slots.
    if identity(resident) in view["builders"]:
        stock_slots -= missing_builders
    return stock_slots


def validate_build(world, resident, stage):
    view = project_view(world, resident["current_site_ip"])
    if view["next_stage"] is None or stage != view["next_stage"]:
        raise ValueError("Choose the current stage of the local building project.")
    material = view["material"]
    if remaining_for(resident, view) <= 0:
        raise ValueError("This stage needs a contribution from another resident.")
    if not resident.get("inventory", {}).get(material, 0):
        raise ValueError("Gather the building material before contributing it.")
    return view


def build(world, resident, stage):
    site = resident["current_site_ip"]
    view = validate_build(world, resident, stage)
    material = view["material"]
    projects = world.setdefault("construction", {})
    project = projects.setdefault(site, {"completed": 0, "contributed": 0, "builders": []})
    actor = identity(resident)
    resident["inventory"][material] -= 1
    project["contributed"] += 1
    if actor not in project["builders"]:
        project["builders"].append(actor)
    events = [Event("construction.contributed",
                    f"{resident['birth']['name']} puts one {material} into the {stage} at {site} "
                    f"({project['contributed']}/{view['required']}).",
                    (actor,), {"stage": stage, "material": material,
                               "contributed": project["contributed"], "required": view["required"]}, site)]
    if project["contributed"] != view["required"]:
        return events

    builders = tuple(project["builders"])
    project.update(completed=project["completed"] + 1, contributed=0, builders=[])
    if stage in {"bridge shelter", "shelter"}:
        for peer in world["residents"]:
            if identity(peer) in builders:
                if built_home(peer) is None:
                    peer["home_site"] = site
    events.append(Event("construction.completed", f"Residents finish the {stage} at {site}.",
                        builders, {"stage": stage}, site))
    if stage in {"community", "village"}:
        project["members"] = sorted(set(project.get("members", [])) | set(builders))
    if stage == "settlement":
        settlements = world["economy"]["settlements"]
        if site not in settlements:
            settlements[site] = {"founded_day": world["turn"], "last_occupied": world["turn"],
                                 "treasury": {}}
            events.append(Event("settlement.founded", f"Residents found a settlement at {site}.",
                                builders, {"day": world["turn"]}, site))
    return events
