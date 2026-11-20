"""Bounded in-world hypotheses, staged experiments, and shared notebooks."""

import re
from ipaddress import IPv4Address

from event_store import Event

RULE_VERSION = 1
CAPABILITIES = ("mineral_assay", "terrain_classification")


def normalize_proposal(proposal):
    """Identify a test by structured terms; descriptive wording is inert."""
    if not isinstance(proposal, dict) or not {"kind", "site"} <= proposal.keys():
        raise ValueError("A proposal requires kind and site.")
    if proposal.keys() - {"kind", "site", "description"}:
        raise ValueError("Unrecognized proposal fields.")
    if any(not isinstance(value, str) for value in proposal.values()):
        raise ValueError("Proposal fields must be strings.")
    kind = re.sub(r"[\s_-]+", "_", proposal["kind"].strip().casefold())
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", kind):
        raise ValueError("Invalid capability name.")
    site = str(IPv4Address(proposal["site"].strip()))
    return f"v{RULE_VERSION}:{kind}:{site}", kind, site


def resolve_possibility(hypotheses, proposal, roll):
    key, kind, site = normalize_proposal(proposal)
    if key not in hypotheses:
        draw = roll(key, "possibility") if kind in CAPABILITIES else None
        status = "unknown" if draw is None else "possible" if draw < 90 else "impossible"
        hypotheses[key] = {"kind": kind, "site": site, "status": status,
                           "rule_version": RULE_VERSION, "draw": draw}
    return key, hypotheses[key]


def report(kind, resident, experiment, effects):
    name = resident["birth"]["name"]
    stage = kind.replace("_", " ")
    return Event(f"research.{kind}", f"{name} records research: {stage}.",
                 (resident["birth"]["id"],), effects, experiment["site"])


def candidate(world, research):
    """Prefer a peer's notebook, then an original proposal from a survey."""
    for notebook in research["notebooks"].values():
        key = notebook["hypothesis"]
        if notebook["attempt"] != 1 or research["attempts"][key] != 1:
            continue
        for resident in world["residents"]:
            if resident["birth"]["id"] != notebook["author"]:
                return resident, research["hypotheses"][key], notebook["id"]
    for resident in world["residents"]:
        for site, survey in resident.get("site_studied", {}).items():
            kind = CAPABILITIES[survey["day"] % len(CAPABILITIES)]
            proposal = {"kind": kind, "site": site}
            key, _, _ = normalize_proposal(proposal)
            if key not in research["hypotheses"]:
                return resident, proposal, None
    return None


def advance_research(world, roll):
    """Advance one research stage per day, with at most two attempts per test."""
    research = world.setdefault("research", {
        "hypotheses": {}, "attempts": {}, "notebooks": {}, "active": None,
    })
    experiment = research["active"]
    if experiment is None:
        selection = candidate(world, research)
        if selection is None:
            return []
        resident, proposal, source = selection
        terms = {"kind": proposal["kind"], "site": proposal["site"]}
        key, hypothesis = resolve_possibility(research["hypotheses"], terms, roll)
        evidence = {"hypothesis": key, "possibility": hypothesis, "source_notebook": source}
        if hypothesis["status"] != "possible":
            return [report("possibility", resident, hypothesis, evidence)]
        attempt = research["attempts"].get(key, 0) + 1
        research["attempts"][key] = attempt
        experiment = {"id": f"{key}:{attempt}", "hypothesis": key, "attempt": attempt,
                      "author": resident["birth"]["id"], "site": hypothesis["site"],
                      "source_notebook": source, "rule_version": RULE_VERSION,
                      "started_day": world["turn"], "stage": "experiment"}
        research["active"] = experiment
        return [report("resumed" if source else "started", resident, experiment,
                       {**evidence, "experiment_id": experiment["id"]})]

    resident = next(r for r in world["residents"] if r["birth"]["id"] == experiment["author"])
    key = experiment["hypothesis"]
    hypothesis = research["hypotheses"][key]
    if experiment["stage"] == "experiment":
        draw = roll(experiment["id"], "experiment")
        notebook = research["notebooks"].get(experiment["source_notebook"])
        threshold = 75 if notebook else 55
        success = draw < threshold
        field = "mineral_trace" if hypothesis["kind"] == "mineral_assay" else "terrain"
        result = None
        if success:
            if field == "mineral_trace":
                result = notebook["material"] if notebook else resident["site_studied"][experiment["site"]][field]
            else:
                result = world["map"][experiment["site"]][field]
        experiment.update(stage="interpretation", experiment_draw=draw, result=result,
                          completed=success, success_threshold=threshold)
        return [report("experiment", resident, experiment, {
            "experiment_id": experiment["id"], "draw": draw, "threshold": threshold,
            "success": success, "result": result, "rule_version": RULE_VERSION,
        })]

    draw = roll(experiment["id"], "interpretation")
    site = experiment["site"]
    source = research["notebooks"].get(experiment["source_notebook"])
    material = source["material"] if source else resident["site_studied"][site]["mineral_trace"]
    interpretation = ("inconclusive" if not experiment["completed"]
                      else "supported" if draw < 75 else "misread")
    notebook = {**experiment, "stage": "complete", "material": material,
                "interpretation": interpretation, "interpretation_draw": draw,
                "finished_day": world["turn"]}
    research["notebooks"][notebook["id"]] = notebook
    research["active"] = None
    return [report("notebook", resident, experiment, {"notebook": notebook})]
