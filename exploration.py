"""Shared, coordinate-based terrain and daily expeditions."""

from dataclasses import dataclass
from hashlib import sha256
from random import Random

from ip_geography import coordinates, day_trip_paths

HOME = "cell:0:0"
CAPACITY = 3
NEIGHBORS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def terrain_roll(seed, key):
    return Random(sha256(f"{seed}:terrain:v1:{key}".encode()).digest()).random()


def cell_id(x, y):
    return f"cell:{x}:{y}"


def discover(seed, x, y, day, explorer=None):
    return terrain_record(seed, cell_id(x, y), x, y, day, explorer)


def terrain_record(seed, identifier, x, y, day, explorer):
    feature = identifier
    if identifier == HOME:
        terrain = "settlement"
    elif y % 4 == 2 and terrain_roll(seed, f"river:{y}") < 0.7:
        terrain = "river"
        feature = f"river:{y}"
    else:
        roll = terrain_roll(seed, identifier)
        terrain = next(kind for cutoff, kind in (
            (0.45, "grassland"), (0.75, "forest"), (0.90, "mountain"), (1.0, "lake"),
        ) if roll < cutoff)
    return {
        "id": identifier, "coordinates": [x, y], "terrain": terrain, "feature_id": feature,
        "discovered_on": day, "discovered_by": explorer,
    }


def ensure_map(world):
    if "map" not in world:
        world["map"] = {HOME: discover(world["seed"], 0, 0, 0)}


@dataclass(frozen=True)
class Expedition:
    kind: str
    message: str
    resident: dict
    site_id: str
    effects: dict


def explore(world, rng):
    residents = world["residents"]
    cells = world["map"]
    crowding = max(0, len(residents) - CAPACITY)
    weights = [
        0.05 + (resident["traits"]["curiosity"] + resident["traits"]["adventurousness"]) / 2
        for resident in residents
    ]
    chance = min(0.8, 0.35 * sum(weights) / len(weights) + 0.05 * crowding)
    if rng.random() >= chance:
        return None

    resident = rng.choices(residents, weights=weights)[0]
    if "geography" in world:
        paths = day_trip_paths(cells)
        visited = sorted(paths.keys() & cells.keys())
        frontier = sorted(paths.keys() - cells.keys())
        revisit = bool(visited) and (not frontier or rng.random() < 0.25)
        identifier = rng.choice(visited if revisit else frontier)
        x, y = coordinates(identifier)
        return finish_expedition(world, resident, identifier, x, y, paths[identifier])

    known = sorted(tuple(cell["coordinates"]) for cell in cells.values())
    visited = [point for point in known if point != (0, 0)]
    revisit = bool(visited) and rng.random() < 0.25
    if revisit:
        x, y = rng.choice(visited)
    else:
        frontier = sorted({
            (x + dx, y + dy)
            for x, y in known
            for dx, dy in NEIGHBORS
            if cell_id(x + dx, y + dy) not in cells
        })
        x, y = rng.choice(frontier)
    return finish_expedition(world, resident, cell_id(x, y), x, y)


def finish_expedition(world, resident, identifier, x, y, path=None):
    cells = world["map"]
    revisit = identifier in cells
    origin = resident["birth"]
    day = world["turn"]
    if not revisit:
        cells[identifier] = terrain_record(world["seed"], identifier, x, y, day, origin["id"])
    previous_expedition = resident.get("last_expedition")
    resident["last_expedition"] = {"day": day, "cell_id": identifier}
    verb = "revisits" if revisit else "discovers"
    effects = {"last_expedition": [previous_expedition, resident["last_expedition"]]}
    if path is not None:
        effects["outbound_path"] = path
    if not revisit:
        effects["site"] = [None, cells[identifier]]
    kind = "site.revisited" if revisit else "site.discovered"
    location = identifier if path is not None else f"({x}, {y})"
    message = f"{origin['name']} {verb} {cells[identifier]['terrain']} at {location} and returns home."
    return Expedition(kind, message, resident, identifier, effects)
