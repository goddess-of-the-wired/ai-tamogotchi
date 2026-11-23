"""Local production, barter, and resident-founded councils."""

from collections import defaultdict

from event_store import Event
from ip_geography import ORIGIN

RESOURCE_BY_TERRAIN = {
    "origin": "fiber", "grassland": "fiber", "forest": "fiber", "mountain": "stone",
    "river": "clay", "lake": "clay",
}


def identity(resident):
    return resident["birth"]["id"]


def advance_economy(world, controlled=(), *, resident_built=False):
    groups = defaultdict(list)
    for resident in world["residents"]:
        groups[resident["current_site_ip"]].append(resident)
    economy = world.setdefault("economy", {"settlements": {}, "institutions": {}})
    day = world["turn"]
    for site, group in groups.items():
        if resident_built and site == ORIGIN:
            continue
        material = RESOURCE_BY_TERRAIN.get(world["map"][site]["terrain"], "stone")
        fallback = [r for r in group if identity(r) not in controlled]
        for resident in fallback:
            stock = resident.setdefault("inventory", {})
            # Work alternates with rest and caps personal stock.
            if resident["goal"] != "rest":
                stock[material] = min(stock.get(material, 0) + 1, 8)

        for buyer, seller in zip(fallback[::2], fallback[1::2]):
            hold = buyer["inventory"]
            supply = seller["inventory"]
            surplus = next((kind for kind in sorted(supply)
                            if supply[kind] >= 2 and hold.get(kind, 0) == 0), None)
            if surplus is None or buyer["savings"] < 1:
                continue
            supply[surplus] -= 1
            hold[surplus] = 1
            buyer["savings"] -= 1
            seller["savings"] += 1
            yield Event("economy.trade", f"Residents exchange {surplus} for one coin.",
                        (identity(buyer), identity(seller)), {"quantity": 1,
                         "material": surplus, "coins": 1}, site)

        settlement = economy["settlements"].get(site)
        if settlement is None and len(group) >= 2 and not resident_built:
            settlement = {"founded_day": day, "last_occupied": day, "treasury": {}}
            economy["settlements"][site] = settlement
            yield Event("settlement.founded", f"A settlement forms at {site}.",
                        tuple(map(identity, group)), {"day": day}, site)
        if settlement is not None and (resident_built or len(group) >= 2):
            settlement["last_occupied"] = day
        council = economy["institutions"].get(site)
        if council or not settlement:
            continue
        occupants = {identity(r) for r in fallback}
        eligible = [
            r for r in fallback if r["inventory"].get(material, 0) >= 2
            and any(peer in occupants and peer != identity(r) and score >= 2
                    for peer, score in r["relationships"].items())
        ]
        if len(eligible) >= 2:
            members = eligible[:2]
            for member in members:
                member["inventory"][material] -= 1
            treasury = settlement["treasury"]
            treasury[material] = treasury.get(material, 0) + len(members)
            council = {"members": list(map(identity, members)), "formed_day": day,
                       "motion": "maintain communal stock", "votes_for": len(members)}
            economy["institutions"][site] = council
            yield Event("institution.formed", f"Residents vote to form a council at {site}.",
                        tuple(council["members"]), {**council, "contribution": material}, site)

    for site, council in list(economy["institutions"].items()):
        if resident_built and site == ORIGIN:
            continue
        occupants = {identity(r) for r in groups.get(site, ())}
        if occupants.intersection(council["members"]):
            council["last_quorum"] = day
        elif day - council.get("last_quorum", council["formed_day"]) >= 3:
            del economy["institutions"][site]
            yield Event("institution.dissolved", f"The council at {site} dissolves.",
                        tuple(council["members"]), {"formed_day": council["formed_day"],
                         "remaining_stock": economy["settlements"][site]["treasury"].copy()}, site)
