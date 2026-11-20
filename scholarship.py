"""Compose bounded findings while preserving resident interpretations."""

from event_store import Event


def record_scholarship(world, research_events):
    for event in research_events:
        if event.type != "research.notebook":
            continue
        notebook = event.effects["notebook"]
        resident = next(r for r in world["residents"] if r["birth"]["id"] == notebook["author"])
        subject = notebook["hypothesis"]
        interpretation = notebook["interpretation"]
        resident.setdefault("beliefs", {})[subject] = {
            "assessment": interpretation, "notebook": notebook["id"],
        }
        if notebook["result"] is None or not notebook["source_notebook"]:
            continue
        first = world["research"]["notebooks"][notebook["source_notebook"]]
        authors = {notebook["author"], first["author"]}
        reviewer = next((r for r in world["residents"] if r["birth"]["id"] not in authors), None)
        if reviewer is None:
            continue
        site = notebook["site"]
        facts = world["map"][site]
        # Closed composition: an observed terrain category plus a mineral trace
        # yields a reference profile, with no transition or ability semantics.
        profile = {"terrain": facts["terrain"], "mineral_trace": notebook["material"],
                   "method": "terrain-and-mineral-profile.v1"}
        reviewer_id = reviewer["birth"]["id"]
        entry = {"day": world["turn"], "site": site, "facts": profile,
                 "sources": [first["id"], notebook["id"]],
                 "lineage": [first["author"], notebook["author"], reviewer_id],
                 "interpretation": interpretation}
        world.setdefault("scholarship", {})[subject] = entry
        yield Event("research.synthesized",
                    f"{reviewer['birth']['name']} combines inherited evidence into a site profile.",
                    (reviewer_id,), entry, site)
