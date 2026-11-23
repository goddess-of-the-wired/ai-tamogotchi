"""A small, persistent simulation of one town."""
import argparse
import hashlib
import json
import random
import secrets
import sqlite3
import subprocess
import sys
import tarfile
import time
import uuid
from contextlib import closing, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from checkpoints import export_checkpoint, verify_checkpoint
from desires import advance_desires, begin_desires
from economy import advance_economy
from event_store import Event, append_events, canonical_history, encode, migrate
from exploration import HOME, ensure_map, explore
from ip_geography import MEETING_POINT, ORIGIN, initialize_geography, validate_geography
from navigation import advance_journeys, initialize_resident_location
from observer import (PUBLIC_EVENTS, birth_request, load_request, observer_status,
                      public_key_der, read_artifact, store_notebooks, verify_birth)
from public_proofs import (confirmation_status, confirm_batch, publish_batch,
                           verify_manifest, witness_payload)
from research import advance_research
from resident_life import expire_offers
from residents import daily_decisions, initialize as initialize_resident
from scholarship import record_scholarship
from storage_backup import backup_database
from traits import BINARY_PROBABILITIES, TRAITS
from world_clock import (
    CATCH_UP_LIMIT, CATCH_UP_PAUSE_SECONDS, claim_due_day, clock_status, initialize_clock,
)

PRIORITIES = ("loyalty", "stability", "ambition", "curiosity", "generosity")
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
    public = {key: value for key, value in world.items() if key not in {"seed", "residents", "creator_admissions"}}
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
            "memories": [{key: value for key, value in memory.items() if key != "text"}
                         for memory in resident.get("memories", [])],
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
        initialize_resident(resident)
        if "geography" in world:
            initialize_resident_location(resident)
    validate_geography(world)
    ensure_map(world)
    return world


def save(db, world):
    db.execute(
        "INSERT INTO world VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET state = excluded.state",
        (json.dumps(world),),
    )


def advance(world, managed=(), controlled=(), *, resolve=None):
    world["turn"] += 1
    turn = world["turn"]
    rng = rng_for(world["seed"], turn)
    events = []
    population = world["residents"]
    lead = population[0]
    staff = world.setdefault("workshop_staff", {
        "mechanic": next((r["birth"]["id"] for r in population[1:]
                          if r["role"] == "mechanic"), None),
        "filled": any(r["role"] == "apprentice" for r in population),
    })
    mechanic = next((r for r in population if r["birth"]["id"] == staff["mechanic"]), lead)
    crew = (lead,) if mechanic is lead else (lead, mechanic)
    applicant = next((r for r in population if r not in crew and r["role"] == "resident"), None)

    home = MEETING_POINT if resolve is not None else world.get("geography", {}).get("origin", HOME)

    def record(kind, message, actors, effects, *, site_id=home, cause_type=None):
        events.append(Event(kind, message, tuple(r["birth"]["id"] for r in actors),
                            effects, site_id, cause_type))

    consequential = rng_for(world["seed"], f"consequential:{turn}").random() < CONSEQUENTIAL_CHANCE
    if world["workshop"] == "open" and consequential:
        roles = {r["birth"]["id"]: [r["role"], "resident"] for r in crew}
        world["workshop"] = "closed"
        lead["role"] = "resident"
        mechanic["role"] = "resident"
        record("workshop.closed", "A broken drive shaft closes the workshop. The crew asks for help repairing it.",
               crew, {"workshop": ["open", "closed"], "roles": roles})

    if world["workshop"] == "closed":
        origin = lead["birth"]
        name = origin["name"]
        motives = {value: 3 - index for index, value in enumerate(origin["priorities"])}
        help_score = (
            motives.get("loyalty", 0) + motives.get("generosity", 0)
            + (1 + 4 * lead["traits"]["courage"]) + lead["trust"]
        )
        work_score = 4 + motives.get("stability", 0) + motives.get("ambition", 0)
        before = {key: lead[key] for key in ("savings", "trust", "role")}
        repair_before = world["repair"]
        if lead["savings"] >= 3 and help_score >= work_score:
            lead["savings"] -= 3
            lead["trust"] += 1
            lead["role"] = "workshop hand"
            world["repair"] += 2
            message = f"{name} spends 3 savings repairing the workshop with the crew. Trust grows."
        else:
            lead["savings"] += 2
            lead["role"] = "market courier"
            message = f"{name} takes a market shift for 2 savings while the crew works on the repair."
        world["repair"] += 1
        effects = {key: [value, lead[key]] for key, value in before.items()}
        effects["repair"] = [repair_before, world["repair"]]
        record("resident.savings_changed", message, crew, effects,
               cause_type="workshop.closed")
        if world["repair"] >= 4:
            world["workshop"] = "reopened"
            role = mechanic["role"]
            mechanic["role"] = "mechanic"
            record("workshop.reopened", "The crew reopens the workshop and posts a paid apprenticeship.",
                   (mechanic,), {"workshop": ["closed", "reopened"], "role": [role, mechanic["role"]]},
                   cause_type="resident.savings_changed")
    elif world["workshop"] == "reopened" and not staff["filled"] and applicant is not None:
        applicant["role"] = "apprentice"
        staff["filled"] = True
        record("resident.job_assigned", f"{applicant['birth']['name']} takes the apprenticeship created by the workshop repair.",
               (applicant,), {"role": ["resident", "apprentice"]}, cause_type="workshop.reopened")
    else:
        local = [r for r in population if not managed or r.get("current_site_ip", home) == home
                 or (r.get("current_site_ip") != ORIGIN
                     and r.get("current_site_ip") in world.get("economy", {}).get("settlements", {}))]
        workers = [r for r in local if r["role"] != "resident"] or local
        if workers:
            worker = rng.choice(workers)
            savings = worker["savings"]
            coins = 3 if consequential else 1
            worker["savings"] += coins
            name = worker["birth"]["name"]
            message = (f"A traveling merchant pays {name} 3 for a rush order." if consequential
                       else f"{name} finishes a paid shift and saves 1.")
            record("resident.savings_changed", message, (worker,), {"savings": [savings, worker["savings"]]})
    events.extend(daily_decisions(world, home, controlled=controlled,
                                  excluded_site=ORIGIN if resolve is not None else None))
    expedition_rng = rng_for(world["seed"], f"expedition:{turn}")
    if "geography" in world:
        events.extend(advance_economy(world, controlled, resident_built=resolve is not None))
        if resolve:
            events.extend(resolve(world))
        events.extend(advance_journeys(world, expedition_rng, managed, depart_origin=resolve is not None))

        def research_roll(key, stage):
            return rng_for(world["seed"], f"research:{key}:{stage}").randrange(100)

        findings = advance_research(world, research_roll)
        events.extend(findings)
        events.extend(record_scholarship(world, findings))
        return events
    expedition = explore(world, expedition_rng)
    if expedition:
        record(expedition.kind, expedition.message, (expedition.resident,), expedition.effects,
               site_id=expedition.site_id)
    return events


def step(db, scheduled=False, *, now=None, controller=None):
    db.execute("BEGIN IMMEDIATE")
    with db:
        world = load(db)
        if world is None:
            raise SystemExit("Initialize the town with the init command first.")
        if scheduled:
            current_time = time.time() if now is None else now
            migrated = initialize_clock(world, current_time)
            delay = claim_due_day(world, current_time)
            if delay > 0:
                if migrated:
                    save(db, world)
                return delay
        controlled = controller.controlled(db, world["turn"] + 1) if controller else ()
        if controller:
            for resident in world["residents"]:
                resident.pop("hunger", None)
            events = list(begin_desires(world))
            outcomes = advance(world, controller.residents, controlled,
                               resolve=lambda state: controller.resolve(db, state))
            events.extend(outcomes)
            events.extend(advance_desires(world, outcomes))
            expire_offers(world)
        else:
            events = advance(world)
        if "clock_policy_version" in world:
            world["last_committed_day"] = world["turn"]
        store_notebooks(db, world)
        save(db, world)
        append_events(db, world["residents"][0]["birth"]["id"], world["turn"], events)
    for event in events:
        print(f"Day {world['turn']}: {event.message}", flush=True)
    return 0


def run_scheduled(db):
    """Process a bounded burst, yielding between bursts during a backlog."""
    print("World clock running. One world day every six hours.", flush=True)
    while True:
        for _ in range(CATCH_UP_LIMIT):
            delay = step(db, scheduled=True)
            if delay > 0:
                due = datetime.fromtimestamp(time.time() + delay, tz=timezone.utc)
                print(f"Next turn at {due:%Y-%m-%d %H:%M:%S UTC}.", flush=True)
                time.sleep(delay)
                break
        else:
            print(f"Processed {CATCH_UP_LIMIT} scheduled days. Pausing catch-up for "
                  f"{CATCH_UP_PAUSE_SECONDS} seconds.", flush=True)
            time.sleep(CATCH_UP_PAUSE_SECONDS)


def inspect_clock(db, migrate_clock):
    if migrate_clock:
        db.execute("BEGIN IMMEDIATE")
    with db:
        world = load(db)
        if world is None:
            raise SystemExit("Initialize the town with the init command first.")
        if migrate_clock and initialize_clock(world, time.time()):
            save(db, world)
        return clock_status(world)


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


def add_observer_commands(commands):
    request = commands.add_parser("birth-request", help="generate a request to sign with a creator key")
    request.add_argument("--name", required=True)
    request.add_argument("--priorities", required=True, nargs=3, choices=PRIORITIES)
    request.add_argument("--public-key", required=True, type=Path)
    admit = commands.add_parser("birth-admit", help="submit a creator-signed birth request")
    admit.add_argument("request", type=Path)
    admit.add_argument("signature", type=Path)
    observer = commands.add_parser("observer", help="read the public view")
    reads = observer.add_subparsers(dest="view", required=True)
    reads.add_parser("status")
    reads.add_parser("map")
    timeline = reads.add_parser("timeline")
    timeline.add_argument("--resident")
    artifact = reads.add_parser("artifact")
    artifact.add_argument("digest")


def read_observer(db, world, args):
    if args.command == "birth-request":
        origin = world.get("geography", {}).get("origin", HOME)
        request = birth_request(world["residents"][0]["birth"]["id"], origin,
                                args.name.strip(), args.priorities, public_key_der(args.public_key))
        sys.stdout.write(encode(request))
    elif args.view == "artifact":
        sys.stdout.buffer.write(read_artifact(db, args.digest))
    else:
        if args.view == "status":
            view = observer_status(world)
        elif args.view == "map":
            view = world["map"]
        else:
            view = [event for event in canonical_history(db) if event["type"] in PUBLIC_EVENTS
                    and (not args.resident or args.resident in event["actor_ids"])]
        print(json.dumps(view, indent=2))


def admit_signed_birth(db, request, certificate):
    db.execute("BEGIN IMMEDIATE")
    with db:
        world = load(db)
        if world is None:
            raise ValueError("Initialize the town first.")
        world_id = world["residents"][0]["birth"]["id"]
        site = world.get("geography", {}).get("origin", HOME)
        if request["world_id"] != world_id or request["site"] != site:
            raise ValueError("Birth request targets another world or origin.")
        resident_id = request["resident_id"]
        if any(r["birth"]["id"] == resident_id for r in world["residents"]):
            raise ValueError("This birth request was already admitted or the ID exists.")
        turn = world["turn"]
        admissions = world.setdefault("creator_admissions", {"turn": turn, "keys": []})
        if admissions["turn"] != turn:
            admissions.update(turn=turn, keys=[])
        key = request["key"]
        if key in admissions["keys"] or len(admissions["keys"]) >= 20:
            raise ValueError("Signed birth admission limit reached for this turn.")
        resident = birth(request["name"], request["priorities"], secrets.token_hex(16))
        resident["birth"].update(id=resident_id, day=turn, site_id=site,
                                 traits=resident["traits"].copy(), certificate=certificate)
        initialize_resident(resident)
        if "geography" in world:
            initialize_resident_location(resident)
        world["residents"].append(resident)
        admissions["keys"].append(key)
        save(db, world)
        append_events(db, world_id, turn, [Event(
            "resident.born", f"{request['name']} joins Crossing with a creator certificate.",
            (resident_id,), {"certificate": certificate}, site,
        )])
    return resident_id


def initialize_storage(db):
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("CREATE TABLE IF NOT EXISTS world (id INTEGER PRIMARY KEY, state TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS birth_draft (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
    db.execute("""CREATE TABLE IF NOT EXISTS events (
        turn INTEGER NOT NULL, position INTEGER NOT NULL, message TEXT NOT NULL,
        PRIMARY KEY (turn, position)
    )""")

    migrate(db)


def benchmark(population, days):
    """Measure the simulation against an isolated on-disk world."""
    with TemporaryDirectory() as directory:
        database = Path(directory) / "bench.sqlite3"
        with closing(sqlite3.connect(database)) as db:
            initialize_storage(db)
            founder = birth("Benchmark founder", list(PRIORITIES[:3]), "benchmark")
            options = argparse.Namespace(spend=[], habit=[], solo=True)
            with db:
                found_town(db, founder, character_sheet(founder), options)
                world = load(db)
                for number in range(1, population):
                    resident = birth(f"Resident {number}", list(PRIORITIES[:3]), str(number))
                    resident["birth"]["site_id"] = ORIGIN
                    initialize_resident_location(resident)
                    initialize_resident(resident)
                    world["residents"].append(resident)
                save(db, world)
            start = time.perf_counter()
            with open(Path(directory) / "steps.log", "w") as sink, redirect_stdout(sink):
                for _ in range(days):
                    step(db)
            elapsed = time.perf_counter() - start
            count, history_bytes = db.execute(
                "SELECT COUNT(*), SUM(length(CAST(record AS BLOB))) FROM canonical_events"
            ).fetchone()
            snapshot_bytes, = db.execute("SELECT length(CAST(state AS BLOB)) FROM world").fetchone()
        return {"population": population, "days": days, "elapsed_seconds": round(elapsed, 6),
                "database_bytes": database.stat().st_size, "snapshot_bytes": snapshot_bytes,
                "events": count, "event_payload_bytes": history_bytes, "inference_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="glitch.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    draft = commands.add_parser("draft", help="draw or reopen a resident's birth sheet")
    init = commands.add_parser("init", help="finish a birth sheet and found the town")
    init.add_argument("--solo", action="store_true", help="omit the two example residents")
    add = commands.add_parser("add-resident", help="create a resident in an existing world")
    for command in (draft, init, add):
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
    history = commands.add_parser("history", help="read the town chronicle")
    history.add_argument("--json", action="store_true", help="show structured events recorded after migration")
    commands.add_parser("map", help="show the shared discovered world")
    turn = commands.add_parser("step", help="advance the town by a fixed number of days")
    turn.add_argument("--days", type=positive, default=1)
    clock = commands.add_parser("clock", help="inspect or migrate the world clock")
    clock_commands = clock.add_subparsers(dest="clock_command", required=True)
    clock_commands.add_parser("status", help="show saved schedule metadata")
    clock_commands.add_parser("migrate", help="adopt the six-hour policy without advancing a turn")
    run = commands.add_parser("run", help="advance continuously until interrupted")
    run.add_argument("--interval", type=positive, help="advance immediately and repeat at this many seconds for testing")
    add_observer_commands(commands)
    proof = commands.add_parser("proof", help="publish or verify public event manifests")
    proof_commands = proof.add_subparsers(dest="proof_command", required=True)
    publish = proof_commands.add_parser("publish", help="dry-run publication to a local directory")
    publish.add_argument("directory", type=Path)
    verify = proof_commands.add_parser("verify", help="verify a manifest against a trusted root")
    verify.add_argument("manifest", type=Path)
    verify.add_argument("--root", required=True)
    verify.add_argument("--event", action="append", default=[])
    witness = proof_commands.add_parser("witness-payload", help="verify records and emit witness signing bytes")
    witness.add_argument("manifest", type=Path)
    witness.add_argument("--root", required=True)
    witness.add_argument("--event", action="append", default=[])
    confirm = proof_commands.add_parser("confirm", help="record an independent signed confirmation")
    confirm.add_argument("root")
    confirm.add_argument("signature", type=Path)
    confirm.add_argument("--public-key", required=True, type=Path)
    proof_status = proof_commands.add_parser("status", help="show publication and confirmation state")
    proof_status.add_argument("root")
    checkpoint = commands.add_parser("checkpoint", help="export, verify or restore a private archive")
    checkpoint_commands = checkpoint.add_subparsers(dest="operation", required=True)
    for operation in ("export", "verify", "restore"):
        command = checkpoint_commands.add_parser(operation)
        command.add_argument("archive", type=Path)
        if operation != "export":
            command.add_argument("--digest", required=True)
        if operation == "restore":
            command.add_argument("destination", type=Path)
    bench = commands.add_parser("benchmark", help="measure an isolated simulation")
    bench.add_argument("--population", type=positive, default=20)
    bench.add_argument("--days", type=positive, default=30)
    args = parser.parse_args()

    if args.command == "benchmark":
        print(json.dumps(benchmark(args.population, args.days)))
        return

    if args.command == "checkpoint":
        try:
            if args.operation == "export":
                receipt = export_checkpoint(Path(args.db), args.archive)
            else:
                receipt = verify_checkpoint(args.archive, args.digest,
                                            getattr(args, "destination", None))
            print(json.dumps(receipt))
        except (OSError, ValueError, KeyError, tarfile.TarError, sqlite3.Error) as error:
            parser.exit(1, f"{error}\n")
        return

    if args.command == "proof" and args.proof_command in {"verify", "witness-payload"}:
        try:
            manifest = json.loads(args.manifest.read_text())
            if args.proof_command == "verify":
                print(json.dumps(verify_manifest(manifest, args.root, args.event)))
            else:
                sys.stdout.write(encode(witness_payload(manifest, args.root, args.event)))
        except (OSError, ValueError, TypeError) as error:
            parser.exit(1, f"{error}\n")
        return

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

    if args.command in {"observer", "birth-request"}:
        try:
            uri = Path(args.db).resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as db:
                world = load(db)
                if world is None:
                    raise ValueError("Initialize the town first.")
                read_observer(db, world, args)
        except (OSError, ValueError, sqlite3.Error, subprocess.TimeoutExpired) as error:
            parser.exit(1, f"{error}\n")
        return

    if (args.command == "history" and args.json) or (
        args.command == "clock" and args.clock_command == "status"
    ):
        database_uri = Path(args.db).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(database_uri, uri=True)) as db:
            if args.command == "clock":
                result = inspect_clock(db, migrate_clock=False)
            else:
                if load(db) is None:
                    raise SystemExit("Initialize the town with the init command first.")
                result = canonical_history(db)
            print(json.dumps(result, indent=2))
        return

    with closing(sqlite3.connect(args.db)) as db:
        initialize_storage(db)

        if args.command == "proof":
            try:
                if args.proof_command == "publish":
                    result = publish_batch(db, args.directory)
                elif args.proof_command == "confirm":
                    with args.signature.open("rb") as stream:
                        signature = stream.read(65)
                    result = confirm_batch(db, args.root, signature, public_key_der(args.public_key))
                else:
                    result = confirmation_status(db, args.root)
                print(json.dumps(result))
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                parser.exit(1, f"{error}\n")
        elif args.command in {"draft", "init"}:
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
        elif args.command == "birth-admit":
            try:
                request = load_request(args.request)
                with args.signature.open("rb") as stream:
                    signature = stream.read(65)
                certificate = verify_birth(request, signature, PRIORITIES)
                print(admit_signed_birth(db, request, certificate))
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                parser.exit(1, f"{error}\n")
        elif args.command == "add-resident":
            db.execute("BEGIN IMMEDIATE")
            with db:
                world = load(db)
                if world is None:
                    raise SystemExit("Initialize the town with the init command first.")
                if not args.name or not args.name.strip() or not args.priorities or len(set(args.priorities)) != 3:
                    raise SystemExit("Provide --name and three distinct --priorities.")
                seed = args.seed if args.seed is not None else secrets.token_hex(16)
                resident = birth(args.name.strip(), args.priorities, seed)
                resident["birth"]["traits"] = resident["traits"].copy()
                resident["birth"]["day"] = world["turn"]
                initialize_resident(resident)
                site = world.get("geography", {}).get("origin", HOME)
                if "geography" in world:
                    resident["birth"]["site_id"] = site
                    initialize_resident_location(resident)
                world["residents"].append(resident)
                actor = resident["birth"]["id"]
                message = f"{resident['birth']['name']} joins Crossing."
                append_events(db, world["residents"][0]["birth"]["id"], world["turn"], [
                    Event("resident.born", message, (actor,), {"name": resident["birth"]["name"]}, site)
                ])
                save(db, world)
            print(f"{message} Resident ID {actor}")
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
        elif args.command == "clock":
            print(json.dumps(inspect_clock(db, migrate_clock=True), indent=2))
        elif args.command == "step":
            for _ in range(args.days):
                step(db)
        else:
            try:
                if args.interval is None:
                    run_scheduled(db)
                else:
                    while True:
                        step(db)
                        time.sleep(args.interval)
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
        "workshop": "open", "repair": 0, "residents": [player] if args.solo else [player, mira, ivo],
    }
    initialize_clock(world, time.time())
    initialize_geography(world)
    for resident in world["residents"]:
        resident["birth"]["site_id"] = ORIGIN
        initialize_resident_location(resident)
        initialize_resident(resident)
    save(db, world)
    birth_events = [
        Event("resident.born", f"{player['birth']['name']} arrives at the God Stone in Crossing.",
              tuple(r["birth"]["id"] for r in world["residents"]),
              {"residents": [[], [
                  {"id": r["birth"]["id"], "name": r["birth"]["name"],
                   "savings": r["savings"], "role": r["role"]}
                  for r in world["residents"]
              ]]}, site_id=ORIGIN)
    ]
    append_events(db, player["birth"]["id"], 0, birth_events)
    db.execute("DELETE FROM birth_draft WHERE id = 1")


if __name__ == "__main__":
    main()
