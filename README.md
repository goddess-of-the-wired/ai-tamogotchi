# AI Tamogotchi

Working title for the unnamed project. The CLI entry point is `glitch.py`.

A small town simulation in Python and SQLite. Your resident has ranked priorities
and a randomly seeded temperament. A broken workshop gives them a choice that
can affect their savings, their relationship with Mira, and Ivo’s employment.

## Run it

From this directory, with Python 3.10 or newer:

```sh
python3 glitch.py draft --name Alex --priorities loyalty generosity curiosity
python3 glitch.py init
python3 glitch.py run
```

The draft shows eight visible scalar traits and six visible habits. Run
`python3 glitch.py draft` again to reopen the saved sheet. To customize the birth,
append `--spend TRAIT=POINTS` and `--habit TRAIT=true` or `--habit TRAIT=false`
to `init`, using IDs from your sheet. Repeat either option to make more choices.
You have six points total, up to three per trait. A point adds 0.10, capped at
1.0. Set up to two habits. Plain `init` accepts all the rolls. The original
one-command `init --name ... --priorities ...` still creates an all-rolled town.

One world day lasts six real hours, starting at founding. The runner catches up
after a restart, committing up to four scheduled days in a burst before a
60-second pause. It keeps the backlog until all due days are committed.
Press Ctrl+C to stop the runner. In another terminal, inspect the town:

```sh
python3 glitch.py history
python3 glitch.py status
```

For fast testing, use `step --days 4` or `run --interval 5` with the default
runner stopped. These advance simulation days without moving the real-time
schedule. Use `--db another-town.sqlite3` before the subcommand for a separate town.

Each simulated day rolls a 25% chance of a consequential event. An active
workshop repair or its apprenticeship follow-up takes precedence. Otherwise,
a successful roll starts the workshop breakdown or later brings a merchant
rush order. Quiet days still produce routine activity.

## Traits

Residents have 150 traits. Scalars range from 0.0 to 1.0 and represent
strength of tendency. Binary traits are true or false and represent specific
default habits. Chosen life priorities are separate from rolled traits.

```sh
python3 glitch.py traits
python3 glitch.py status
python3 glitch.py status --show-hidden
```

The last command is for local inspection. Ordinary status omits hidden values
and seeds. Anyone with database access or the seed can recover hidden values.

To add a trait globally, append a stable snake_case ID to the appropriate group
in `traits.py`, or add a group with a visibility and type. Restart any running
simulation to read the new catalog. Existing residents get the new trait
automatically on load, and the next simulation turn saves it. Each roll uses the
resident seed and trait ID, so adding or reordering entries leaves existing
values intact. Keep existing IDs and types stable.

The workshop decision currently uses courage. The other traits are available
for future simulation rules. Pre-catalog residents retain their original
courage, sociability, and persistence rolls, converted from 1–5 to 0–1.

New births roll scalar values using a centered beta distribution. Binary habits
use per-trait probabilities where defined (for example, 80% for greeting
neighbors and 15% for keeping anonymous notes), with 50% as the default.

## Exploration

Each day rolls for an expedition. Curiosity and
adventurousness influence its odds and who goes. Each resident beyond the
settlement's capacity of three adds five percentage points to expedition
odds in legacy worlds, up to an overall cap of 80%.

Run `python3 glitch.py map` to see shared discoveries. Trips reveal cells
along the explored frontier or revisit saved cells. Terrain is seeded by
virtual site identifiers and coordinates. Connected river cells share a feature ID. Each discovery
records the first explorer and day. Existing saves keep their original
coordinate maps and continue exploring in their legacy namespace.

## Test and rehearse recovery

Use Python 3.10 or newer from the repository root. The test suite uses only the
standard library and creates its own temporary databases.

```sh
python3 -m unittest discover -s tests -v
```

The characterization baseline is commit
`ac7d8a80b95afa34021493f844be4af53af58ef5`. The tests control time directly to check six-hour boundaries and catch-up
without waiting.

For a manual smoke test and restore rehearsal, run this in a POSIX shell:

```sh
(
  set -eu
  scratch=$(mktemp -d)
  trap 'rm -rf -- "$scratch"' EXIT
  source="$scratch/source.sqlite3"
  restored="$scratch/restored.sqlite3"

  python3 glitch.py --help
  python3 glitch.py traits
  python3 glitch.py --db "$source" draft --name Baseline --seed test \
    --priorities loyalty generosity curiosity
  python3 glitch.py --db "$source" init
  python3 glitch.py --db "$source" step --days 4
  python3 glitch.py --db "$source" backup "$restored"

  for command in status history map; do
    python3 glitch.py --db "$source" "$command" > "$scratch/source-$command"
    python3 glitch.py --db "$restored" "$command" > "$scratch/restored-$command"
    diff -u "$scratch/source-$command" "$scratch/restored-$command"
  done

  python3 glitch.py --db "$restored" step
  python3 glitch.py --db "$source" status | python3 -c 'import json, sys; assert json.load(sys.stdin)["turn"] == 4'
  python3 glitch.py --db "$restored" status | python3 -c 'import json, sys; assert json.load(sys.stdin)["turn"] == 5'
  echo "Restore rehearsal passed."
)
```

A successful run prints no differences and ends with `Restore rehearsal passed.`
The backup command uses SQLite's online backup API and checks the snapshot
before publishing it. Choose a new destination on a filesystem that supports
hard links. A snapshot includes private traits and seeds. Keep it private.
Use a separate copy for experiments so you retain an unmodified recovery point.

## Verify structured history on an upgraded save

`history` continues to print the prose chronicle. `history --json` shows
canonical records ordered by turn and position. Existing prose stays intact.
The first canonical event on an upgraded save is the first event committed
after migration, and its `previous_hash` is null.

To rehearse the upgrade with an actual pre-migration database, check out this
version and run from the repository root in a POSIX shell:

```sh
(
  set -eu
  scratch=$(mktemp -d)
  trap 'rm -rf -- "$scratch"' EXIT
  db="$scratch/world.sqlite3"
  git archive --format=tar 17e6b6a > "$scratch/baseline.tar"
  tar -xf "$scratch/baseline.tar" -C "$scratch"

  python3 "$scratch/glitch.py" --db "$db" init --name Baseline --seed test \
    --priorities loyalty generosity curiosity
  python3 "$scratch/glitch.py" --db "$db" step --days 4
  python3 "$scratch/glitch.py" --db "$db" backup "$scratch/pre-upgrade.sqlite3"
  python3 "$scratch/glitch.py" --db "$db" history > "$scratch/before"

  python3 glitch.py --db "$db" history > "$scratch/after"
  diff -u "$scratch/before" "$scratch/after"
  python3 glitch.py --db "$db" history --json
  python3 glitch.py --db "$db" step
  python3 glitch.py --db "$db" history --json
)
```

Expect no prose differences, then an empty JSON array, then one or more
records at turn 5. To roll back, run the baseline CLI against a copy of the
pre-upgrade snapshot. The outbox records pending events locally. This version
does not publish them. Hashes cover versioned records encoded as sorted,
compact UTF-8 JSON with the `glitch-event-v1` domain prefix and a null byte.

## Verify six-hour clock migration

`clock status` shows the clock policy, schedule anchor, next scheduled deadline,
completed schedule slots, and latest committed simulation day. Timestamps are
UTC Unix seconds. Manual `step` and `run --interval` advance the simulation
day counter without consuming scheduled slots.

An existing seven-hour save keeps its next deadline, even if overdue. Its
six-hour schedule is anchored six hours before that deadline. For a save
without a deadline, migration uses the migration time as the schedule anchor.
Run `clock migrate` to apply the policy explicitly without advancing a day.
The normal scheduler also applies the policy on its first use.

To rehearse with an actual seven-hour save, run from the repository root:

```sh
(
  set -eu
  scratch=$(mktemp -d)
  trap 'rm -rf -- "$scratch"' EXIT
  db="$scratch/world.sqlite3"
  git archive --format=tar ed66fb2 > "$scratch/baseline.tar"
  tar -xf "$scratch/baseline.tar" -C "$scratch"

  python3 "$scratch/glitch.py" --db "$db" init --name Baseline --seed test \
    --priorities loyalty generosity curiosity
  python3 glitch.py --db "$db" backup "$scratch/pre-clock.sqlite3"
  python3 glitch.py --db "$db" clock status > "$scratch/before.json"
  python3 glitch.py --db "$db" clock migrate > "$scratch/after.json"
  python3 glitch.py --db "$db" clock migrate > "$scratch/repeated.json"
  diff -u "$scratch/after.json" "$scratch/repeated.json"
  python3 - "$scratch" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
before = json.loads((root / "before.json").read_text())
after = json.loads((root / "after.json").read_text())
assert before["migration_required"] and not after["migration_required"]
assert before["next_turn_at"] == after["next_turn_at"]
assert after["next_turn_at"] - after["genesis_at"] == 21600
assert after["scheduled_days"] == after["last_committed_day"] == 0
print("Clock migration rehearsal passed.")
PY
)
```

The automated suite checks exact boundaries, catch-up limits, competing runners,
and retry after an interrupted turn. Run it with
`python3 -m unittest discover -s tests -v`.

## Verify the God Stone and IPv4 geography

New worlds begin at the God Stone (`127.0.0.1`). The order-16 Hilbert mapping
covers the full IPv4 namespace on a bounded 65,536 × 65,536 grid. The origin is
reserved as a separate location and connects only to the three founding
region gateways. Every other address, including special-use addresses, is
ordinary virtual terrain. The simulation makes no network requests.

Only visited sites appear on the map. Residents choose destinations up to
ten edges away and travel one legal edge per turn. Structured history
records the planned route, each movement, and any discoveries.

To check a fresh world on a temporary database, run:

```sh
(
  set -eu
  scratch=$(mktemp -d)
  trap 'rm -rf -- "$scratch"' EXIT
  db="$scratch/world.sqlite3"
  python3 glitch.py --db "$db" init --name Baseline --seed test \
    --priorities loyalty generosity curiosity
  python3 glitch.py --db "$db" map
  python3 glitch.py --db "$db" step --days 24
  python3 glitch.py --db "$db" history --json
)
```

The initial map should contain only `127.0.0.1` and its inscription. The
birth event should name that site. Look for a `journey.started` route
beginning at the origin and continuing through a founding gateway. Run `python3 -m unittest discover -s tests -v` to check round trips,
gateway legality, the exact inscription, and legacy-save compatibility.

## Verify persistent journeys

Run the following from the repository root. Each `step` starts a fresh
process against the same temporary save.

```sh
(
  set -eu
  scratch=$(mktemp -d)
  trap 'rm -rf -- "$scratch"' EXIT
  db="$scratch/world.sqlite3"
  python3 glitch.py --db "$db" init --name Baseline --seed test \
    --priorities loyalty generosity curiosity
  python3 glitch.py --db "$db" step --days 8
  python3 glitch.py --db "$db" status
  python3 glitch.py --db "$db" step --days 16
  python3 glitch.py --db "$db" history
)
```

Look for a resident with a journey ID, a route index, and a current site
matching that route position. In the chronicle, the resident moves one edge
per day and surveys the destination the day after arriving. In `status`,
`site_seen` holds the terrain observed on passage, and `site_studied` holds
survey details. The current travel policy uses one edge per simulated day
for both gateways and ordinary steps. Terrain-specific travel costs are
future work.

Existing IPv4 saves resume at their birth site because all previous outings
returned home. Their maps and old events remain available. Coordinate-based
legacy worlds continue using their original expedition model.

Run `python3 -m unittest discover -s tests -v` to check restart continuity,
legal edges, distinct observation and survey records, and shared discoveries.

## Verify autonomous residents

Use a fresh temporary save. The default founding cast includes two examples.
Pass `--solo` to omit them. Add residents to either world with `add-resident`.

```sh
(
  set -eu
  scratch=$(mktemp -d)
  trap 'rm -rf -- "$scratch"' EXIT
  db="$scratch/world.sqlite3"
  python3 glitch.py --db "$db" init --solo --name Alex --seed test \
    --priorities loyalty generosity curiosity
  python3 glitch.py --db "$db" add-resident --name Robin --seed robin \
    --priorities stability curiosity generosity
  python3 glitch.py --db "$db" step --days 4
  python3 glitch.py --db "$db" status
)
```

Look for both residents, daily goals, energy, memories, and relationships.
Repeated greetings build familiarity, which can change a later greeting
into a conversation. The automated suite runs 20 residents for 100 days.

## Verify autonomous research

Residents propose research after surveying an IPv4 site. The prototype
supports mineral assays and terrain classification. It locks possibility by
capability, site, and rule version. An experiment and its interpretation run
on successive days. The durable notebook preserves evidence even when the
experiment fails. A different resident can read it and make one follow-up
attempt. Research runs locally with seeded draws and a one-stage-per-day budget.

```sh
(
  set -eu
  scratch=$(mktemp -d)
  trap 'rm -rf -- "$scratch"' EXIT
  db="$scratch/world.sqlite3"
  python3 glitch.py --db "$db" init --name Alex --seed test \
    --priorities loyalty generosity curiosity
  python3 glitch.py --db "$db" step --days 32 > /dev/null
  python3 glitch.py --db "$db" step --days 32 > /dev/null
  python3 glitch.py --db "$db" history | grep 'records research'
)
```

Look for research started, experiment, notebook, and resumed lines. Use
`status` to inspect the notebook authors, source notebook IDs, experiment
draws, and interpretations. The automated suite also exercises a failed
assay followed by a successful peer attempt with a mistaken interpretation.

## Verify creator linkage and observer reads

Signed births use Ed25519 and the OpenSSL 3 command-line tools. Run in a
temporary working directory with an initialized database. The private key
stays with the creator.

```sh
openssl genpkey -algorithm ED25519 -out creator.pem
openssl pkey -in creator.pem -pubout -out creator.pub
python3 glitch.py --db world.sqlite3 birth-request --public-key creator.pub \
  --name Robin --priorities loyalty curiosity stability > birth.json
openssl pkeyutl -sign -inkey creator.pem -rawin -in birth.json -out birth.sig
python3 glitch.py --db world.sqlite3 birth-admit birth.json birth.sig
python3 glitch.py --db world.sqlite3 observer status
python3 glitch.py --db world.sqlite3 observer timeline
```

Verify that Robin has a certificate and no private traits or seed in the
observer view. Replaying the request fails. Admission permits one signed
birth per key and twenty in total per simulation turn. The observer
interface provides reads only. Trusted local operators still control the
simulation through the existing commands.

Notebook bytes are stored in SQLite by SHA-256 so backups include them.
Read or export an artifact with
`python3 glitch.py --db world.sqlite3 observer artifact DIGEST`. The command
verifies the hash before emitting the exact bytes.

## Verify the inherited research scenario

Run `python3 -m unittest discover -s tests -p test_acceptance.py -v`. This
rehearsal starts a temporary world using seed `acceptance-15`, stops midway
through a journey, and resumes the same save. The chronicle should record
Ivo's mountain discovery at `172.16.0.51`, a failed classification attempt,
and Alex continuing from the notebook. Alex's test finds mountain terrain
but the interpretation is mistaken. The rehearsal checks the observer
timeline and both artifact hashes on a reopened SQLite backup.

## Verify a public history batch

Use a temporary directory and run these commands from the repository root:

```sh
(
  set -eu
  scratch=$(mktemp -d)
  trap 'rm -rf -- "$scratch"' EXIT
  db="$scratch/world.sqlite3"
  python3 glitch.py --db "$db" init --name Alex --seed test \
    --priorities loyalty generosity curiosity
  python3 glitch.py --db "$db" step --days 6 > /dev/null
  python3 glitch.py --db "$db" proof publish "$scratch/manifests" > "$scratch/receipt.json"
  python3 - "$scratch/receipt.json" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

receipt = json.loads(Path(sys.argv[1]).read_text())
manifest = json.loads(Path(receipt["manifest"]).read_text())
birth = next(event for event in manifest["records"] if event["type"] == "resident.born")
discovery = next(event for event in manifest["records"] if event["type"] == "site.discovered")
subprocess.run([sys.executable, "glitch.py", "proof", "verify", receipt["manifest"],
                "--root", receipt["root"], "--event", birth["event_id"],
                "--event", discovery["event_id"]], check=True)
PY
)
```

The verifier does not open a database. To verify a shareable manifest, retain
its root separately and supply the trusted value with `--root`. The local
dry-run publisher processes at most 64 public events per batch and stores
immutable files named by root. If publication fails, retry with the same
directory. The publisher reuses the pending batch even if the world has
advanced. Publication runs outside the turn transaction. This release has
only a local dry-run adapter.

Leaves hash canonical event JSON with prefix byte 0. Internal nodes hash
the ordered child hashes with prefix byte 1; odd levels duplicate their
last child. The final digest hashes prefix byte 2, the four-byte big-endian
event count, and the tree root. Only allowlisted observer events enter these
batches. The full private event chain stays in the database.
