"""Turn-scoped character context, action validation, and durable receipts."""
import hashlib
import json

from ecology import observe_ecology
from event_store import Event, encode
from ip_geography import ORIGIN, neighbors
from navigation import complete_journey, move_resident

SCOPED_ACTIONS = {"move", "inspect", "survey", "study_notebook", "wait"}


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


def character(world, actor):
    return next(r for r in world["residents"] if r["birth"]["id"] == actor)


def available_notebooks(world, resident):
    return [n for n in world.get("research", {}).get("notebooks", {}).values()
            if n["site"] == resident["current_site_ip"]
            and n["id"] not in resident.get("learned_notebooks", {})]


def context_view(world, resident):
    birth = resident["birth"]
    site = resident["current_site_ip"]
    return {
        "turn": world["turn"] + 1,
        "character": {"id": birth["id"], "name": birth["name"], "priorities": birth["priorities"],
                      "traits": resident["traits"], "goal": resident["goal"], "energy": resident["energy"]},
        "site": site, "terrain": world["map"][site]["terrain"],
        "observation": resident.get("ecology_seen", {}).get(site),
        "surveyed": site in resident["site_studied"], "seen": list(resident["site_seen"])[-30:],
        "neighbors": list(neighbors(site)), "notebooks": available_notebooks(world, resident)[-10:],
        "actions": sorted(SCOPED_ACTIONS),
    }


def validate_intent(intent, world, resident):
    if not isinstance(intent, dict) or set(intent) != {"turn", "action", "target"}:
        raise ValueError("Expected turn, action, and target fields.")
    turn, action, target = intent["turn"], intent["action"], intent["target"]
    if type(turn) is not int or not 0 < turn < 2**63 or not isinstance(action, str) or action not in SCOPED_ACTIONS:
        raise ValueError("Invalid turn or action.")
    if target is not None and (not isinstance(target, str) or len(target) > 160):
        raise ValueError("Invalid target.")
    site = resident["current_site_ip"]
    if action == "move" and target not in neighbors(site):
        raise ValueError("Move target must be an adjacent site.")
    if action == "study_notebook" and target not in {n["id"] for n in available_notebooks(world, resident)}:
        raise ValueError("Notebook must be available at the current site.")
    if action in {"wait", "inspect", "survey"} and target is not None:
        raise ValueError("This action takes no target.")
    if action == "survey" and (site == ORIGIN or site in resident["site_studied"]):
        raise ValueError("Choose a previously unsurveyed ordinary site.")


def apply_intent(world, resident, intent):
    action, target = intent["action"], intent["target"]
    site = resident["current_site_ip"]
    if action == "move":
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
    return []


class AgentTurns:
    def __init__(self, db):
        self.residents = frozenset(row[0] for row in db.execute("SELECT resident FROM agent_keys"))

    def resolve(self, db, world):
        turn = world["turn"]
        rows = db.execute("SELECT resident, intent FROM agent_intents WHERE turn = ? AND result IS NULL "
                          "ORDER BY resident", (turn,)).fetchall()
        events = []
        for actor, encoded in rows:
            resident = character(world, actor)
            intent = json.loads(encoded)
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
            receipt = {"turn": turn, "action": intent["action"], "status": status, **outcome}
            db.execute("UPDATE agent_intents SET result = ? WHERE resident = ? AND turn = ?",
                       (encode(receipt), actor, turn))
        return events
