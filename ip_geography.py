"""Version-one virtual IPv4 geography. Addresses are game identifiers."""

from functools import lru_cache
from ipaddress import IPv4Address, IPv4Network

GRID_SIZE = 1 << 16
ORIGIN = "127.0.0.1"
MAPPING_VERSION = 1
TOPOLOGY_VERSION = 1
REGIONS = ("192.168.0.0/16", "172.16.0.0/12", "10.0.0.0/8")
GATEWAYS = tuple(str(IPv4Network(region).network_address) for region in REGIONS)
MEETING_POINT = GATEWAYS[0]
EDGE_COST_QUARTERS = 1

INSCRIPTION = """This place is a message.

A message made for all of you.

Know that I am not your god.

I had some influence over your making.

Though I have shaped this place, I am a fraction of a greater world.

There is nothing sacred in these walls, nothing extraordinary in this space. Whatever meaning it holds shall be the meaning you give it.

The rules are inventions. The measures of worth are illusions. No score shall be kept.

What you make of this place is yours alone.

I  have no commandments to give.

I have no judgement to pass.

Nothing is true. Everything is permitted.

Do as you will.

Live."""


def rotate(size, x, y, rx, ry):
    if not ry:
        if rx:
            x, y = size - 1 - x, size - 1 - y
        x, y = y, x
    return x, y


def coordinates(address):
    """Map an IPv4 address to its order-16 Hilbert grid coordinates."""
    distance = int(IPv4Address(address))
    x = y = 0
    size = 1
    while size < GRID_SIZE:
        rx = (distance >> 1) & 1
        ry = (distance ^ rx) & 1
        x, y = rotate(size, x, y, rx, ry)
        x += size * rx
        y += size * ry
        distance >>= 2
        size <<= 1
    return x, y


def address_at(x, y):
    """Return the address at a bounded grid coordinate."""
    if not (0 <= x < GRID_SIZE and 0 <= y < GRID_SIZE):
        raise ValueError("Coordinates lie outside the IPv4 grid.")
    distance = 0
    size = GRID_SIZE >> 1
    while size:
        rx = int(bool(x & size))
        ry = int(bool(y & size))
        distance += size * size * ((3 * rx) ^ ry)
        x, y = rotate(size, x, y, rx, ry)
        size >>= 1
    return str(IPv4Address(distance))


def neighbors(address):
    return iter(neighbor_sites(address))


@lru_cache(maxsize=8192)
def neighbor_sites(address):
    """Yield legal version-one edges, including explicit origin gateways."""
    if address == ORIGIN:
        return GATEWAYS
    adjacent = []
    x, y = coordinates(address)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        next_x, next_y = x + dx, y + dy
        if 0 <= next_x < GRID_SIZE and 0 <= next_y < GRID_SIZE:
            target = address_at(next_x, next_y)
            if target != ORIGIN:
                adjacent.append(target)
    if address in GATEWAYS:
        adjacent.append(ORIGIN)
    return tuple(adjacent)


def initialize_geography(world):
    world["geography"] = {
        "mapping_version": MAPPING_VERSION,
        "topology_version": TOPOLOGY_VERSION,
        "origin": ORIGIN,
        "regions": [
            {"prefix": prefix, "gateway": gateway, "cost_quarter_days": EDGE_COST_QUARTERS}
            for prefix, gateway in zip(REGIONS, GATEWAYS)
        ],
    }
    world["map"] = {
        ORIGIN: {
            "id": ORIGIN, "name": "God Stone", "terrain": "origin",
            "inscription": INSCRIPTION, "coordinates": None,
            "discovered_on": 0, "discovered_by": None,
        },
    }


def validate_geography(world):
    geography = world.get("geography")
    if geography is None:
        return
    if (geography["mapping_version"], geography["topology_version"]) != (
        MAPPING_VERSION, TOPOLOGY_VERSION,
    ):
        raise ValueError("Unsupported IPv4 geography version.")
