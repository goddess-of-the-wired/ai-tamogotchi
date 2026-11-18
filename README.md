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

Crossing starts at (0, 0). Each day rolls for an expedition. Curiosity and
adventurousness influence its odds and who goes. Each resident beyond the
settlement's capacity of three adds five percentage points to expedition
odds, up to an overall cap of 80%. Explorers take day trips and return home.

Run `python3 glitch.py map` to see shared discoveries. Trips reveal cells
along the explored frontier or revisit saved cells. Terrain is seeded by
coordinates. Connected river cells share a feature ID. Each discovery
records the first explorer and day. Existing towns begin with Crossing and
expand on subsequent turns.

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
