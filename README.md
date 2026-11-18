# AI Tamogotchi

Working title for the unnamed project. The CLI entry point is `glitch.py`.

A small town simulation in Python and SQLite. Your resident has ranked priorities
and a randomly seeded temperament. A broken workshop gives them a choice that
can affect their savings, their relationship with Mira, and Ivo’s employment.

## Run it

From this directory, with Python 3:

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

One world day lasts seven real hours, starting at founding. The runner applies
elapsed days on restart and then waits for the next deadline. Towns created
with earlier versions start their real-time schedule on their first default run.
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
