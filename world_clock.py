"""Persist a wall-clock schedule independently of manual simulation turns."""

WORLD_DAY_SECONDS = 6 * 60 * 60
CLOCK_POLICY_VERSION = 1
CATCH_UP_LIMIT = 4
CATCH_UP_PAUSE_SECONDS = 60


def initialize_clock(world, now):
    """Start policy 1, retaining a legacy save's next deadline when present."""
    if "clock_policy_version" in world:
        return False
    deadline = world.get("next_turn_at")
    genesis = now if deadline is None else deadline - WORLD_DAY_SECONDS
    world.update(
        genesis_at=genesis,
        world_day_seconds=WORLD_DAY_SECONDS,
        clock_policy_version=CLOCK_POLICY_VERSION,
        scheduled_days=0,
        last_committed_day=world["turn"],
        next_turn_at=genesis + WORLD_DAY_SECONDS if deadline is None else deadline,
    )
    return True


def claim_due_day(world, now):
    """Advance one schedule slot inside the caller's SQLite write transaction."""
    if world["clock_policy_version"] != CLOCK_POLICY_VERSION:
        raise ValueError(f"Unsupported clock policy {world['clock_policy_version']}")
    delay = world["next_turn_at"] - now
    if delay > 0:
        return delay
    world["scheduled_days"] += 1
    world["next_turn_at"] = (
        world["genesis_at"] + (world["scheduled_days"] + 1) * world["world_day_seconds"]
    )
    return 0


def clock_status(world):
    keys = ("genesis_at", "world_day_seconds", "clock_policy_version",
            "scheduled_days", "last_committed_day", "next_turn_at")
    status = {key: world.get(key) for key in keys}
    status["last_committed_day"] = world.get("last_committed_day", world["turn"])
    status["migration_required"] = status["clock_policy_version"] is None
    return status
