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
