"""Turn-scoped character context, action validation, and durable receipts."""
import hashlib
import json

from construction import build, project_view, validate_build
from desires import active_desires, suggested_action, unrecorded
from ecology import observe_ecology
from event_store import Event, encode
from ip_geography import MEETING_POINT, ORIGIN, neighbors
from navigation import complete_journey, move_resident
from resident_life import (apply_action, built_home, gatherable, home_step, offers_at, peers,
                           settlement_at, validate_action)
from traits import TRAITS

SCOPED_ACTIONS = {"move", "inspect", "survey", "study_notebook", "wait", "say", "give",
                  "offer_trade", "accept_trade", "gather", "buy_food", "return_home", "journal", "build"}
EXTRA_FIELDS = {"say": {"text"}, "give": {"item"}, "offer_trade": {"item"}}
STONE_ACTIONS = {"inspect", "move"}
NO_TARGET = {"wait", "inspect", "survey", "buy_food", "return_home"}


def strongest_traits(resident):
    traits = resident["traits"]
    visible = [key for key in traits if key in TRAITS and TRAITS[key]["visibility"] == "visible"]
    scalars = sorted((key for key in visible if type(traits[key]) is not bool),
                     key=lambda key: (-abs(traits[key] - 0.5), key))
    habits = sorted(key for key in visible if traits[key] is True)
    return {key: traits[key] for key in (*scalars[:6], *habits[:2])}


def setup_agent_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS agent_keys (
        resident TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS agent_intents (
        resident TEXT NOT NULL REFERENCES agent_keys(resident),
        turn INTEGER NOT NULL, intent TEXT NOT NULL, result TEXT,
        PRIMARY KEY (resident, turn)
    )""")


def digest_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def intent_matches(intent, stored):
    encoded = encode(intent)
    return stored == encoded or stored == "sha256:" + digest_token(encoded)


def character(world, actor):
    return next(r for r in world["residents"] if r["birth"]["id"] == actor)


def available_notebooks(world, resident):
    return [n for n in world.get("research", {}).get("notebooks", {}).values()
            if n["site"] == resident["current_site_ip"]
            and n["id"] not in resident.get("learned_notebooks", {})]


def context_view(world, resident):
    birth = resident["birth"]
    site = resident["current_site_ip"]
    settlements = world.get("economy", {}).get("settlements", {})
    at_stone = site == ORIGIN
    known_settlements = [known for known in resident["site_seen"] if known != ORIGIN and known in settlements]
    step_home = home_step(resident)
    resources = gatherable(world, resident)
    desires = {slot: {**desire, "suggested_action": suggested_action(world, resident, desire, resources)}
               for slot, desire in active_desires(world, resident).items()} if not at_stone else {}
    return {
        "turn": world["turn"] + 1,
        "character": {"id": birth["id"], "name": birth["name"], "priorities": birth["priorities"],
                      "traits": strongest_traits(resident), "goal": resident["goal"],
                      "energy": resident["energy"],
                      "inventory": resident.get("inventory", {}), "savings": resident["savings"]},
        "peers": [{"id": actor, "name": peer["birth"]["name"]}
                  for actor, peer in peers(world, resident).items()] if not at_stone else [],
        "relationships": resident.get("relationships", {}),
        "memories": resident.get("memories", [])[-8:],
        "settlements": known_settlements[-12:],
        "at_settlement": bool(settlement_at(world, site)),
        "home": built_home(resident), "meeting_point": MEETING_POINT, "home_step": step_home,
        "inscription": world["map"][site].get("inscription") if at_stone else None,
        "construction": project_view(world, site),
        "resources": resources, "offers": {} if at_stone else offers_at(world, resident),
        "desires": desires, "journal_candidates": [] if at_stone else sorted(unrecorded(resident))[-12:],
        "site": site, "terrain": world["map"][site]["terrain"],
        "observation": resident.get("ecology_seen", {}).get(site),
        "surveyed": site in resident["site_studied"], "seen": list(resident["site_seen"])[-30:],
        "neighbors": list(neighbors(site)), "notebooks": [] if at_stone else available_notebooks(world, resident)[-10:],
        "actions": sorted(STONE_ACTIONS if at_stone else SCOPED_ACTIONS),
    }


def validate_intent(intent, world, resident):
    if not isinstance(intent, dict) or not {"turn", "action", "target"} <= intent.keys():
        raise ValueError("Expected turn, action, and target fields.")
    turn, action, target = intent["turn"], intent["action"], intent["target"]
    if type(turn) is not int or not 0 < turn < 2**63 or not isinstance(action, str) or action not in SCOPED_ACTIONS:
        raise ValueError("Invalid turn or action.")
    if set(intent) != {"turn", "action", "target"} | EXTRA_FIELDS.get(action, set()):
        raise ValueError("Unexpected or missing fields for this action.")
    if target is not None and (not isinstance(target, str) or len(target) > 160):
        raise ValueError("Invalid target.")
    site = resident["current_site_ip"]
    if site == ORIGIN and action not in STONE_ACTIONS:
        raise ValueError("At the God Stone, you may only read the inscription or leave.")
    if action == "move" and target not in neighbors(site):
        raise ValueError("Move target must be an adjacent site.")
    if action == "study_notebook" and target not in {n["id"] for n in available_notebooks(world, resident)}:
        raise ValueError("Notebook must be available at the current site.")
    if action in NO_TARGET and target is not None:
        raise ValueError("This action takes no target.")
    if action == "survey" and (site == ORIGIN or site in resident["site_studied"]):
        raise ValueError("Choose a previously unsurveyed ordinary site.")
    if action == "journal" and target not in unrecorded(resident):
        raise ValueError("Choose an observation you have not recorded in your journal.")
    if action == "build":
        validate_build(world, resident, target)
    validate_action(world, resident, intent)


def apply_intent(world, resident, intent):
    action, target = intent["action"], intent["target"]
    site = resident["current_site_ip"]
    if action in {"move", "return_home"}:
        if action == "return_home":
            target = home_step(resident)
        resident.update(journey_id=f"{resident['birth']['id']}:{world['turn']}:agent",
                        journey={"path": [site, target], "destination": target, "route_index": 0})
        events = move_resident(world, resident)
        resident.update(journey=None, journey_id=None)
        return events
    if action == "inspect":
        return observe_ecology(world, resident, site)
    if action == "survey":
        return [complete_journey(world, resident)]
    if action == "study_notebook":
        notebook = next(n for n in available_notebooks(world, resident) if n["id"] == target)
        resident.setdefault("learned_notebooks", {})[target] = {
            "day": world["turn"], "interpretation": notebook["interpretation"],
        }
        birth = resident["birth"]
        return [Event("agent.notebook_read", f"{birth['name']} studies a published notebook.",
                      (birth["id"],), {"notebook": target}, site)]
    if action == "build":
        return build(world, resident, target)
    if action == "journal":
        birth = resident["birth"]
        resident.setdefault("journal", {})[target] = resident["ecology_seen"][target].copy()
        return [Event("agent.journaled", f"{birth['name']} records an observation about {target}.",
                      (birth["id"],), {"observation_site": target}, site)]
    if action == "wait":
        resident["energy"] = min(4, resident["energy"] + 1)
        return []
    return apply_action(world, resident, intent)


class AgentTurns:
    def __init__(self, db):
        self.last_seen_turn = {}
        self.residents = frozenset(row[0] for row in db.execute("SELECT resident FROM agent_keys"))

    def controlled(self, db, turn):
        polling = {actor for actor in self.residents if self.last_seen_turn.get(actor) == turn}
        queued = {row[0] for row in db.execute(
            "SELECT resident FROM agent_intents WHERE turn = ? AND result IS NULL", (turn,))}
        return polling | queued

    def resolve(self, db, world):
        turn = world["turn"]
        rows = db.execute("SELECT resident, intent FROM agent_intents WHERE turn = ? AND result IS NULL "
                          "ORDER BY resident", (turn,)).fetchall()
        actions = [(actor, json.loads(encoded)) for actor, encoded in rows]
        # Honor offers the buyer saw before processing new offers or departures.
        order = {"accept_trade": 0, "move": 2, "return_home": 2}
        actions.sort(key=lambda entry: order.get(entry[1]["action"], 1))
        events = []
        for actor, intent in actions:
            resident = character(world, actor)
            try:
                validate_intent(intent, world, resident)
            except ValueError as error:
                status, outcome = "rejected", {"reason": str(error)}
            else:
                resolved = apply_intent(world, resident, intent)
                events.extend(resolved)
                status, outcome = "applied", {
                    "site": resident["current_site_ip"],
                    "events": [event.type for event in resolved],
                    "observation": resident.get("ecology_seen", {}).get(resident["current_site_ip"]),
                }
                if intent["action"] == "inspect" and outcome["site"] == ORIGIN:
                    outcome["inscription"] = world["map"][ORIGIN]["inscription"]
            receipt = {"turn": turn, "action": intent["action"], "status": status, **outcome}
            encoded = encode(intent)
            if intent["action"] == "say":
                encoded = "sha256:" + digest_token(encoded)
            db.execute("UPDATE agent_intents SET intent = ?, result = ? WHERE resident = ? AND turn = ?",
                       (encoded, encode(receipt), actor, turn))
        return events
