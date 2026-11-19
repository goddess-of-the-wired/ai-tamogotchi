"""Bounded route selection and one-edge-per-turn resident journeys."""

from collections import deque
from hashlib import sha256

from event_store import Event
from exploration import expedition_weight, terrain_record
from ip_geography import coordinates, neighbors

ROUTE_EDGES = 10


def choose_route(start, rng):
    """Find a shortest path to a randomly selected site ten legal edges away."""
    frontier = deque([(start,)])
    visited = {start}
    destinations = []
    while frontier:
        path = frontier.popleft()
        if len(path) == ROUTE_EDGES + 1:
            destinations.append(path)
            continue
        for site in neighbors(path[-1]):
            if site not in visited:
                visited.add(site)
                frontier.append((*path, site))
    return list(rng.choice(destinations))


def initialize_resident_location(resident):
    """MVP 3 residents always returned to their recorded birth site."""
    home = resident["birth"]["site_id"]
    resident.setdefault("current_site_ip", home)
    resident.setdefault("journey_id", None)
    resident.setdefault("journey", None)
    resident.setdefault("site_seen", {home: {"day": 0, "terrain": "origin"}})
    resident.setdefault("site_studied", {})


def start_journey(resident, day, rng):
    site = resident["current_site_ip"]
    path = choose_route(site, rng)
    actor = resident["birth"]["id"]
    journey_id = f"{actor}:{day}"
    resident["journey_id"] = journey_id
    resident["journey"] = {"destination": path[-1], "path": path, "route_index": 0}
    name = resident["birth"]["name"]
    return Event("journey.started", f"{name} plans a journey to {path[-1]}.", (actor,),
                 {"journey_id": journey_id, "path": path}, site)


def move_resident(world, resident):
    journey = resident["journey"]
    path = journey["path"]
    index = journey["route_index"]
    previous = resident["current_site_ip"]
    site = path[index + 1]
    if previous != path[index] or site not in neighbors(previous):
        raise ValueError("Saved journey contains an invalid next edge.")

    resident["current_site_ip"] = site
    journey["route_index"] = index + 1
    actor = resident["birth"]["id"]
    name = resident["birth"]["name"]
    journey_id = resident["journey_id"]
    effects = {"journey_id": journey_id, "current_site_ip": [previous, site],
               "route_index": [index, index + 1]}
    events = [Event("journey.moved", f"{name} travels from {previous} to {site}.",
                    (actor,), effects, site)]
    cells = world["map"]
    day = world["turn"]
    if site not in cells:
        cells[site] = terrain_record(world["seed"], site, *coordinates(site), day, actor)
        events.append(Event("site.discovered",
                            f"{name} discovers {cells[site]['terrain']} at {site}.",
                            (actor,), {"site": [None, cells[site]]}, site))
    if site not in resident["site_seen"]:
        observation = {"day": day, "terrain": cells[site]["terrain"]}
        resident["site_seen"][site] = observation
        events.append(Event("site.seen", f"{name} observes {observation['terrain']} at {site}.",
                            (actor,), {"site_seen": [None, observation]}, site))
    return events


def complete_journey(world, resident):
    site = resident["current_site_ip"]
    actor = resident["birth"]["id"]
    name = resident["birth"]["name"]
    journey_id = resident["journey_id"]
    resident["journey"] = None
    resident["journey_id"] = None
    if site in resident["site_studied"]:
        return Event("journey.completed", f"{name} completes the journey to {site}.",
                     (actor,), {"journey_id": [journey_id, None]}, site)
    cell = world["map"][site]
    digest = sha256(f"{world['seed']}:survey:v1:{site}".encode()).digest()
    minerals = ("quartz", "mica", "feldspar", "iron oxide")
    observation = {"day": world["turn"], "feature_id": cell["feature_id"],
                   "mineral_trace": minerals[digest[0] % len(minerals)]}
    resident["site_studied"][site] = observation
    return Event("site.studied", f"{name} surveys {site} and records a sample.", (actor,),
                 {"site_studied": [None, observation], "journey_id": [journey_id, None]}, site)


def advance_journeys(world, rng):
    events = []
    idle = []
    for resident in world["residents"]:
        journey = resident["journey"]
        if journey is None:
            idle.append(resident)
        elif journey["route_index"] < len(journey["path"]) - 1:
            events.extend(move_resident(world, resident))
        else:
            events.append(complete_journey(world, resident))
    if idle:
        weights = [expedition_weight(resident) for resident in idle]
        if rng.random() < min(0.8, 0.35 * sum(weights) / len(weights)):
            resident = rng.choices(idle, weights=weights)[0]
            events.append(start_journey(resident, world["turn"], rng))
            events.extend(move_resident(world, resident))
    return events
