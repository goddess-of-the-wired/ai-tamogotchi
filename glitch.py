"""A small, persistent simulation of one town."""
import argparse
import hashlib
import json
import random
import secrets
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from exploration import ensure_map, explore
from storage_backup import backup_database
from traits import BINARY_PROBABILITIES, TRAITS

PRIORITIES = ("loyalty", "stability", "ambition", "curiosity", "generosity")
WORLD_DAY_SECONDS = 7 * 60 * 60
CONSEQUENTIAL_CHANCE = 0.25
SCALAR_BUDGET = 6
SCALAR_LIMIT = 3
BINARY_LIMIT = 2


def rng_for(seed, purpose):
    digest = hashlib.sha256(f"{seed}:{purpose}".encode()).digest()
    return random.Random(digest)


def sync_traits(resident):
    origin = resident["birth"]
    current = resident.setdefault("traits", {})
    for name, definition in TRAITS.items():
        if name in current:
            continue
        if origin["version"] == 1 and name in origin["traits"]:
            # Preserve the original 1–5 roll in the birth record.
            current[name] = (origin["traits"][name] - 1) / 4
            continue
        if name in origin["traits"]:
            current[name] = origin["traits"][name]
            continue
        version = origin["version"]
        rng = rng_for(origin["seed"], f"trait:v{version}:{name}")
        if definition["type"] == "binary":
            current[name] = (
                bool(rng.getrandbits(1)) if version == 1
                else rng.random() < BINARY_PROBABILITIES.get(name, 0.50)
            )
        else:
            current[name] = round(rng.random() if version == 1 else rng.betavariate(3, 3), 4)


def world_view(world, show_hidden):
    if show_hidden:
        return world
    public = {key: value for key, value in world.items() if key not in {"seed", "residents"}}
    public["residents"] = []
    for resident in world["residents"]:
        origin = resident["birth"]
        visible = {
            name: value for name, value in resident["traits"].items()
            if TRAITS.get(name, {}).get("visibility") == "visible"
        }
        public["residents"].append({
            **resident,
            "birth": {
                **{key: value for key, value in origin.items() if key not in {"seed", "traits", "choices"}},
                "choices": {
                    kind: {name: value for name, value in choices.items() if name in visible}
                    for kind, choices in origin.get("choices", {}).items()
                },
                "traits": {
                    name: value for name, value in origin["traits"].items()
                    if name in visible and origin["version"] >= 2
                },
            },
            "traits": visible,
        })
    return public


def birth(name, priorities, seed):
    persona = rng_for(seed, "birth")
    resident = {
        "birth": {
            "id": str(uuid.uuid4()),
            "version": 2,
            "name": name,
            "seed": seed,
            "priorities": priorities,
            "traits": {},
        },
        "savings": persona.randint(5, 9),
        "trust": 0,
        "role": "resident",
    }
    sync_traits(resident)
    return resident


def character_sheet(resident):
    rng = rng_for(resident["birth"]["seed"], "birth-offer:v1")
    groups = {}
    habits = []
    for name, trait in sorted(TRAITS.items()):
        if trait["visibility"] == "visible":
            if trait["type"] == "scalar":
                groups.setdefault(trait["group"], []).append(name)
            else:
                habits.append(name)
    grouped = list(groups.values())
    scalars = [rng.choice(names) for names in rng.sample(grouped, min(8, len(grouped)))]
    remaining = sorted(set().union(*grouped) - set(scalars))
    scalars.extend(rng.sample(remaining, min(8 - len(scalars), len(remaining))))
    return {
        "scalar": {name: resident["traits"][name] for name in scalars},
        "binary": rng.sample(habits, min(6, len(habits))),
    }


def birth_choices(entries, offered, limit, allowed, flag):
    choices = {}
    for entry in entries:
        name, _, value = entry.partition("=")
        if name not in offered or name in choices or value not in allowed:
            raise SystemExit(f"Invalid {flag} {entry!r}. Use an offered trait once with {', '.join(allowed)}.")
        choices[name] = allowed[value]
    if len(choices) > limit:
        raise SystemExit(f"Choose at most {limit} traits with {flag}.")
    return choices


def finish_birth(resident, sheet, args):
    spend = birth_choices(args.spend, sheet["scalar"], SCALAR_BUDGET,
                          {str(points): points for points in range(1, SCALAR_LIMIT + 1)}, "--spend")
    habits = birth_choices(args.habit, sheet["binary"], BINARY_LIMIT,
                           {"true": True, "false": False}, "--habit")
    if sum(spend.values()) > SCALAR_BUDGET:
        raise SystemExit(f"Spend at most {SCALAR_BUDGET} points.")
    for name, points in spend.items():
        resident["traits"][name] = round(min(1.0, sheet["scalar"][name] + points / 10), 4)
    resident["traits"].update(habits)
    resident["birth"]["choices"] = {"points": spend, "habits": habits}
    resident["birth"]["traits"] = resident["traits"].copy()


def load(db):
    row = db.execute("SELECT state FROM world WHERE id = 1").fetchone()
    if row is None:
        return None
    world = json.loads(row[0])
    for resident in world["residents"]:
        sync_traits(resident)
    ensure_map(world)
    return world


def save(db, world):
    db.execute(
        "INSERT INTO world VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET state = excluded.state",
        (json.dumps(world),),
    )


def advance(world):
    world["turn"] += 1
    turn = world["turn"]
    rng = rng_for(world["seed"], turn)
    events = []
    player, mira, ivo = world["residents"]

    consequential = rng_for(world["seed"], f"consequential:{turn}").random() < CONSEQUENTIAL_CHANCE
    if world["workshop"] == "open" and consequential:
        world["workshop"] = "closed"
        player["role"] = "resident"
        mira["role"] = "resident"
        events.append("A broken drive shaft closes the workshop. Mira asks for help repairing it.")

    if world["workshop"] == "closed":
        origin = player["birth"]
        name = origin["name"]
        motives = {value: 3 - index for index, value in enumerate(origin["priorities"])}
        help_score = (
            motives.get("loyalty", 0) + motives.get("generosity", 0)
            + (1 + 4 * player["traits"]["courage"]) + player["trust"]
        )
        work_score = 4 + motives.get("stability", 0) + motives.get("ambition", 0)
        if player["savings"] >= 3 and help_score >= work_score:
            player["savings"] -= 3
            player["trust"] += 1
            player["role"] = "workshop hand"
            world["repair"] += 2
            events.append(f"{name} spends 3 savings repairing the workshop with Mira. Trust grows.")
        else:
            player["savings"] += 2
            player["role"] = "market courier"
            events.append(f"{name} takes a market shift for 2 savings while Mira works on the repair.")
        world["repair"] += 1
        if world["repair"] >= 4:
            world["workshop"] = "reopened"
            mira["role"] = "mechanic"
            events.append("Mira reopens the workshop and posts a paid apprenticeship.")
    elif world["workshop"] == "reopened" and ivo["role"] == "resident":
        ivo["role"] = "apprentice"
        events.append("Ivo takes the apprenticeship created by the workshop repair.")
    else:
        worker = rng.choice([resident for resident in world["residents"] if resident["role"] != "resident"])
        if consequential:
            worker["savings"] += 3
            events.append(f"A traveling merchant pays {worker['birth']['name']} 3 for a rush order.")
        else:
            worker["savings"] += 1
            events.append(f"{worker['birth']['name']} finishes a paid shift and saves 1.")

    expedition = explore(world, rng_for(world["seed"], f"expedition:{turn}"))
    if expedition:
        events.append(expedition)
    return events


def step(db, scheduled=False):
    db.execute("BEGIN IMMEDIATE")
    with db:
        world = load(db)
        if world is None:
            raise SystemExit("Initialize the town with the init command first.")
        if scheduled:
            now = time.time()
            if "next_turn_at" not in world:
                world["next_turn_at"] = now + WORLD_DAY_SECONDS
                save(db, world)
            if world["next_turn_at"] > now:
                return world["next_turn_at"] - now
            world["next_turn_at"] += WORLD_DAY_SECONDS
        events = advance(world)
        save(db, world)
        db.executemany(
            "INSERT INTO events (turn, position, message) VALUES (?, ?, ?)",
            [(world["turn"], index, event) for index, event in enumerate(events)],
        )
    for event in events:
        print(f"Day {world['turn']}: {event}", flush=True)
    return 0


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def prepare_birth(db, args):
    row = db.execute("SELECT data FROM birth_draft WHERE id = 1").fetchone()
    if row is not None:
        if args.name is not None or args.priorities is not None or args.seed is not None:
            raise SystemExit("A birth sheet is already saved. Run 'draft' to view it or 'init' to finish it.")
        return json.loads(row[0])
    if not args.name or not args.name.strip() or args.priorities is None or len(set(args.priorities)) != 3:
        raise SystemExit("Provide --name and three distinct --priorities to start a birth.")
    seed = args.seed if args.seed is not None else secrets.token_hex(16)
    resident = birth(args.name.strip(), args.priorities, seed)
    return resident, character_sheet(resident)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="glitch.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    draft = commands.add_parser("draft", help="draw or reopen a resident's birth sheet")
    init = commands.add_parser("init", help="finish a birth sheet and found the town")
    for command in (draft, init):
        command.add_argument("--name")
        command.add_argument("--priorities", nargs=3, choices=PRIORITIES)
        command.add_argument("--seed", help="reuse a seed to reproduce initial traits")
    init.add_argument("--spend", action="append", default=[], metavar="TRAIT=POINTS")
    init.add_argument("--habit", action="append", default=[], metavar="TRAIT=true|false")
    backup = commands.add_parser("backup", help="save a consistent SQLite snapshot to a new file")
    backup.add_argument("destination", type=Path)
    status = commands.add_parser("status", help="show current world state and visible traits")
    status.add_argument("--show-hidden", action="store_true", help="include private traits and seeds")
    commands.add_parser("traits", help="list global trait definitions")
    commands.add_parser("history", help="read the town chronicle")
    commands.add_parser("map", help="show the shared discovered world")
    turn = commands.add_parser("step", help="advance the town by a fixed number of days")
    turn.add_argument("--days", type=positive, default=1)
    run = commands.add_parser("run", help="advance continuously until interrupted")
    run.add_argument("--interval", type=positive, help="advance immediately and repeat at this many seconds for testing")
    args = parser.parse_args()

    if args.command == "traits":
        print(json.dumps(TRAITS, indent=2))
        return

    if args.command == "backup":
        try:
            backup_database(Path(args.db), args.destination)
        except (OSError, sqlite3.Error) as error:
            parser.exit(1, f"Backup failed: {error}\n")
        print(f"Backup saved to {args.destination}")
        return

    with closing(sqlite3.connect(args.db)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS world (id INTEGER PRIMARY KEY, state TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS birth_draft (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS events (
            turn INTEGER NOT NULL, position INTEGER NOT NULL, message TEXT NOT NULL,
            PRIMARY KEY (turn, position)
        )""")

        if args.command in {"draft", "init"}:
            db.execute("BEGIN IMMEDIATE")
            with db:
                if load(db) is not None:
                    raise SystemExit("This town already exists. Use --db to create a separate town.")
                player, sheet = prepare_birth(db, args)
                if args.command == "draft":
                    db.execute("INSERT OR IGNORE INTO birth_draft VALUES (1, ?)", (json.dumps([player, sheet]),))
                else:
                    if args.name is not None and (args.spend or args.habit):
                        raise SystemExit("Draw a sheet with 'draft' before applying points or habits.")
                    found_town(db, player, sheet, args)
            if args.command == "draft":
                print(json.dumps(sheet, indent=2))
                print("Finish with init. Spend up to 6 points (3 per scalar); set up to 2 offered habits.")
            else:
                print(f"Crossing is ready. Resident ID {player['birth']['id']}")
        elif args.command in {"status", "map"}:
            world = load(db)
            if world is None:
                raise SystemExit("Initialize the town with the init command first.")
            view = world["map"] if args.command == "map" else world_view(world, args.show_hidden)
            print(json.dumps(view, indent=2))
        elif args.command == "history":
            if load(db) is None:
                raise SystemExit("Initialize the town with the init command first.")
            for day, message in db.execute("SELECT turn, message FROM events ORDER BY turn, position"):
                print(f"Day {day}: {message}")
        elif args.command == "step":
            for _ in range(args.days):
                step(db)
        else:
            try:
                if args.interval is None:
                    print("World clock running. One world day every seven hours.", flush=True)
                while True:
                    if args.interval is not None:
                        step(db)
                        time.sleep(args.interval)
                    else:
                        delay = step(db, scheduled=True)
                        if delay > 0:
                            due = datetime.fromtimestamp(time.time() + delay, tz=timezone.utc)
                            print(f"Next turn at {due:%Y-%m-%d %H:%M:%S UTC}.", flush=True)
                            time.sleep(delay)
            except KeyboardInterrupt:
                print("\nTown paused.")


def found_town(db, player, sheet, args):
    sync_traits(player)
    finish_birth(player, sheet, args)
    seed = player["birth"]["seed"]
    player["role"] = "workshop hand"
    mira = birth("Mira", ["loyalty", "stability", "generosity"], f"{seed}:mira")
    mira["role"] = "mechanic"
    ivo = birth("Ivo", ["curiosity", "ambition", "stability"], f"{seed}:ivo")
    for resident in (mira, ivo):
        resident["birth"]["traits"] = resident["traits"].copy()
    world = {
        "name": "Crossing", "seed": seed, "turn": 0,
        "next_turn_at": time.time() + WORLD_DAY_SECONDS,
        "workshop": "open", "repair": 0, "residents": [player, mira, ivo],
    }
    ensure_map(world)
    save(db, world)
    db.execute("INSERT INTO events VALUES (0, 0, ?)", (f"{player['birth']['name']} arrives in Crossing.",))
    db.execute("DELETE FROM birth_draft WHERE id = 1")


if __name__ == "__main__":
    main()
