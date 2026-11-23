# Run connected Glitch characters

Both executables require Python 3.10 or newer and a Unix-like shell.
The demo uses the local simulation engine and two independently
authenticated characters. The default client policy needs no model setup.

## Start the server

Extract the distribution on the server and run these from its directory.
Choose a directory that does not exist yet.

```sh
world="$HOME/glitch-agent-world"
./glitch-server init --directory "$world"
./glitch-server serve --directory "$world" --tick-seconds 30
```

This creates a fresh IPv4 world and two mode-0600 token files, `ivo.token`
and `mira.token`. Keep that directory private. An existing demo save
can be resumed by rerunning `serve` with the same directory. The demo clock
advances while the server is running. Ctrl+C stops it.

## Start the clients

For a same-machine smoke test, use a separate terminal for each command:

```sh
./glitch-client --token-file "$HOME/glitch-agent-world/ivo.token"
./glitch-client --token-file "$HOME/glitch-agent-world/mira.token"
```

Look for `queued` followed by `applied`. Use `--once` for a single context
fetch and submission. Clients can restart and fetch their past receipts.
The server keeps at most one intent per character and turn, even when an
identical request is retried after the turn has closed.

## Move one client to another computer

Copy only `glitch-client` and that character's token to the client.
For Theseus, adjust the SSH name and repository path if yours differ:

```sh
umask 077
scp avara@192.168.0.2:~/glitch-agent-world/ivo.token ./ivo.token
chmod 600 ivo.token
ssh -N -L 127.0.0.1:8770:127.0.0.1:8770 avara@192.168.0.2
```

Leave the SSH session open and run `./glitch-client --token-file ivo.token`
in another terminal on that client. Run Mira's process wherever it can reach
the server. The server binds only to loopback, and SSH protects the connection
between machines. Use the same port on both sides of the forward.

## Run with an LLM

If Ollama is already running on a client and has a model available:

```sh
./glitch-client --token-file ivo.token --ollama-model YOUR_INSTALLED_MODEL
```

The client sends the character context to Ollama's local HTTP API by default.
The model returns a JSON action and target, and the server validates them.
Use a longer server turn window and `--policy-timeout` for slower models.
When a model fails or misses its turn, the client waits for the next turn.

For another model or policy, pass `--policy-command /path/to/executable`
and optional repeated `--policy-arg`. The executable receives one JSON
character context on standard input and should write only one JSON object
to standard output, such as:

```json
{"action":"inspect","target":null}
```

Actions include moving to a neighboring address, inspecting the current
site, surveying, reading a notebook at the current site, and waiting.
Terrain, observation rolls, and turn outcomes come from the server. The
existing research rules handle experiments separately from character intents.
This is a connection prototype with fixed test residents and a disposable
clock.

## Observe and verify

To follow the same demo from the host, point a separate terminal at its save:

```sh
python3 observer_server.py --db "$HOME/glitch-agent-world/world.sqlite3"
```

Open `http://localhost:8765`. The running LAN preview uses its own save
unless you explicitly point a separate preview at this world. Avoid running
the simulation `step` or `run` commands on this database while the agent
server owns its clock.

Run `python3 -m unittest discover -s tests -p test_agent_connection.py -v`
for a real HTTP smoke test and an Ollama protocol test with a test endpoint.

## Resident life playtest

Character context includes co-located peers, personal inventory and coins,
familiarity, eight recent memories, visited settlements, a known step toward
birthplace, nearby resources, and incoming trade offers. The trait profile
contains up to six visible scalar traits furthest from neutral and up to two
enabled visible habits.

Each action takes one turn. A character can move one edge toward home with
`return_home` and a null target. Other new policy outputs include:

| Action | Target | Additional field |
| --- | --- | --- |
| `say` | Local peer ID | `text`, up to 160 printable characters |
| `give` | Local peer ID | `item`, one unit from your inventory |
| `offer_trade` | Local peer ID | `item`, one unit offered for one coin |
| `accept_trade` | Local seller ID | |
| `gather` | Listed resource | |
| `buy_food` | `null` | |
| `journal` | Site with an unrecorded observation | |
| `build` | The local project’s `next_stage` | |

An offer can be accepted on the following turn if both residents are
co-located and can complete the exchange. Speaking builds familiarity and
records the encounter. The conversation text appears in the participants'
recent memories. Public history and public proofs omit the text.

### Playtest evolving desires

From the repository directory, start a fresh playtest on a separate port.
Choose a world directory that does not yet exist.

```sh
./glitch-server init --directory "$HOME/glitch-desires-playtest"
./glitch-server serve --directory "$HOME/glitch-desires-playtest" --port 8771 --tick-seconds 3
```

Run each client and the observer in its own terminal from the repository.

```sh
./glitch-client --server http://127.0.0.1:8771 --token-file "$HOME/glitch-desires-playtest/mira.token"
./glitch-client --server http://127.0.0.1:8771 --token-file "$HOME/glitch-desires-playtest/ivo.token"
python3 observer_server.py --db "$HOME/glitch-desires-playtest/world.sqlite3" --port 8766
```

Open `http://127.0.0.1:8766`. Let the built-in policies play at least
12 turns. Look for **wants to**, **made progress**, **fulfilled**, and
**set aside** in the timeline. For Ollama, use the longer turn and policy
timeouts described above. End the session with Ctrl+C in each terminal.

Each resident carries an immediate desire (up to five turns) and a longer
pursuit (up to twelve turns). The hierarchy starts with continuity and agency,
then adds belonging, competence and influence, and purpose. Traits, ranked
priorities, available experiences, and recently retired desires guide
selection. The client gets optional actions to advance either desire. It can
choose a different valid action. A completed or retired desire is replaced
as new experiences become available.

Fish, honey, and food remain inventory goods for gifts and trade. Residents
keep those goods until they transfer them. Existing demo saves acquire desires
on the next turn, and historical meals remain in the chronicle.

A context poll or queued intent marks a character as controlled for that turn.
Otherwise, the scripted social and economic systems can act for them. Existing
workshop, settlement founding, and research systems still run in the server.


### Watch residents build a village

Start a fresh save using the playtest commands above. Everyone starts at
the God Stone, where the character context shows the inscription. The only
actions there are `inspect` to read it and `move` to leave. The built-in
clients head to the meeting point at `192.168.0.0` on their first turn and
look for somewhere to establish a home. Residents who value security or
attachment to place can take up a shelter as an agency desire. After a
shelter exists, residents with ties to others can take up civic projects
as belonging desires. These compete with exploration, journaling, and
relationships. Follow Mira in the timeline to see which projects she
chooses and what she contributes. The seeded rehearsal can take more
than 120 turns to reach the village. The observer map
also shows the site's current stage and the next project's progress.

| Stage | Material units | Different builders needed | Result |
| --- | ---: | ---: | --- |
| Bridge shelter | 2 | 1 | A temporary roof. Its builders can call the site home. |
| Shelter | 4 | 1 | A more permanent home for its builders. |
| Settlement | 8 | 2 | A recognized settlement with a market and treasury. |
| Community | 12 | 2 | A persistent membership roster drawn from the builders. |
| Village | 18 | 2 | The final stage of this playtest, with its community membership carried forward. |

Each successful build turn spends one unit of the terrain's material.
Connected clients can choose other ordinary sites to build at. The bridge shelter is the prototype’s temporary shelter stage.
The world has no bridge routing or population growth mechanics yet.
