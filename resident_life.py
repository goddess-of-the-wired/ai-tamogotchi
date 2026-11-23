"""Local resources, conversation, and voluntary exchange in connected worlds."""

from collections import deque

from economy import RESOURCE_BY_TERRAIN, identity
from event_store import Event
from ip_geography import neighbors

FOOD = {"food", "fish", "honey"}
ITEMS = set(RESOURCE_BY_TERRAIN.values()) | FOOD
FEATURE_FOOD = {
    "a colony of ground-nesting bees": "honey",
    "fish feeding in the shallows": "fish",
    "a gravel bed used by spawning fish": "fish",
}


def home_step(resident):
    return known_step(resident, resident["birth"]["site_id"])


def known_step(resident, destination):
    start = resident["current_site_ip"]
    if start == destination:
        return None
    known = resident["site_seen"]
    frontier = deque([(start, None)])
    visited = {start}
    while frontier:
        site, first = frontier.popleft()
        for neighbor in neighbors(site):
            if neighbor == destination:
                return first or destination
            if neighbor in known and neighbor not in visited:
                visited.add(neighbor)
                frontier.append((neighbor, first or neighbor))
    return None


def peers(world, resident):
    return {identity(peer): peer for peer in world["residents"]
            if peer is not resident and peer["current_site_ip"] == resident["current_site_ip"]}


def gatherable(world, resident):
    site = resident["current_site_ip"]
    terrain = world["map"][site]["terrain"]
    items = {RESOURCE_BY_TERRAIN.get(terrain, "stone")}
    if terrain in {"grassland", "forest", "origin"}:
        items.add("food")
    observation = resident.get("ecology_seen", {}).get(site) or {}
    food = FEATURE_FOOD.get(observation.get("label"))
    if food:
        items.add(food)
    return sorted(items)


def settlement_at(world, site):
    return world.get("economy", {}).get("settlements", {}).get(site)


def offers_at(world, resident):
    return {actor: offer for actor, peer in peers(world, resident).items()
            if (offer := peer.get("trade_offer"))
            and offer["buyer"] == identity(resident)
            and offer["day"] in {world["turn"] - 1, world["turn"]}
            and offer["site"] == resident["current_site_ip"]
            and peer.get("inventory", {}).get(offer["item"], 0)}


def validate_action(world, resident, intent):
    action, target = intent["action"], intent["target"]
    item = intent.get("item")
    stock = resident.get("inventory", {})
    site = resident["current_site_ip"]
    if action in {"say", "give", "offer_trade", "accept_trade"} and target not in peers(world, resident):
        raise ValueError("Choose a resident at the current site.")
    if action == "say":
        message = intent.get("text")
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 160 or not message.isprintable():
            raise ValueError("Speech must contain 1 to 160 printable characters.")
    if action in {"give", "offer_trade"}:
        if not isinstance(item, str) or item not in ITEMS or not stock.get(item, 0):
            raise ValueError("Choose an item from your inventory.")
    if action == "accept_trade" and (target not in offers_at(world, resident) or resident["savings"] < 1):
        raise ValueError("Accept a current offer when you have at least one coin.")
    if action == "gather" and (target not in gatherable(world, resident) or stock.get(target, 0) >= 8):
        raise ValueError("Choose a local resource with room in your inventory.")
    if action == "buy_food" and (not settlement_at(world, site) or resident["savings"] < 1):
        raise ValueError("Buying food requires a settlement and one coin.")
    if action == "return_home" and home_step(resident) is None:
        raise ValueError("There is no known step toward home.")


def record_memory(resident, memory):
    resident["memories"] = [*resident.get("memories", [])[-7:], memory]


def transfer(source, recipient, item):
    source[item] -= 1
    recipient[item] = recipient.get(item, 0) + 1


def apply_action(world, resident, intent):
    action, target = intent["action"], intent["target"]
    site = resident["current_site_ip"]
    actor = identity(resident)
    name = resident["birth"]["name"]
    stock = resident.setdefault("inventory", {})
    actors = (actor,)
    if action == "gather":
        quantity = min(8 - stock.get(target, 0), 2 if target in {"fish", "honey"} else 1)
        stock[target] = stock.get(target, 0) + quantity
        kind, message, effects = "gathered", f"{name} gathers {target}.", {
            "item": target, "quantity": quantity,
        }
    elif action == "buy_food":
        resident["savings"] -= 1
        stock["food"] = stock.get("food", 0) + 1
        kind, message, effects = "food_bought", f"{name} buys a meal.", {"coins": 1}
    else:
        peer = peers(world, resident)[target]
        actors = (actor, target)
        peer_name = peer["birth"]["name"]
        if action == "say":
            for speaker, listener in ((resident, target), (peer, actor)):
                relationships = speaker.setdefault("relationships", {})
                relationships[listener] = min(4, relationships.get(listener, 0) + 1)
                record_memory(speaker, {"day": world["turn"], "action": "say",
                                        "resident_id": listener, "speaker": actor,
                                        "text": intent["text"].strip()})
            kind, message, effects = "spoke", f"{name} speaks with {peer_name}.", {}
        elif action == "give":
            item = intent["item"]
            transfer(stock, peer.setdefault("inventory", {}), item)
            kind, message, effects = "gave", f"{name} gives {peer_name} {item}.", {"item": item}
        elif action == "offer_trade":
            item = intent["item"]
            resident["trade_offer"] = {"buyer": target, "item": item, "site": site, "day": world["turn"]}
            kind, message, effects = "trade_offered", f"{name} offers {peer_name} {item}.", {"item": item}
        else:
            item = peer.pop("trade_offer")["item"]
            transfer(peer["inventory"], stock, item)
            resident["savings"] -= 1
            peer["savings"] += 1
            kind, message, effects = "trade_accepted", f"{name} buys {item} from {peer_name}.", {
                "item": item, "coins": 1,
            }
    return [Event(f"agent.{kind}", message, actors, effects, site)]


def expire_offers(world):
    for resident in world["residents"]:
        offer = resident.get("trade_offer")
        if offer and offer["day"] < world["turn"]:
            del resident["trade_offer"]
