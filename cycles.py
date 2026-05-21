"""Segment a contact's stage-transition history into funnel cycles.

A cycle is one pass through the conversion funnel (MCL -> ... -> Customer). Because
contacts can run the funnel repeatedly (resell), the same stage recurs across
cycles. Rules:

- A new cycle begins each time the contact ENTERS MCL. This covers both net-new
  organic entry (straight to MCL) and resell re-entry (reset, then MCL again).
- MTL is a pre-funnel/resting state, not a funnel step. An MTL entry is attached to
  the cycle it leads into (the next MCL). A trailing MTL with no following MCL
  becomes its own pending cycle (an active target not yet captured).
- cycle_type is "resell" once any earlier cycle reached Customer, else "new".

Property-history semantics make this clean: lifecyclestage history records a row
only when the value CHANGES, so MCL appears once per pass (never duplicated), and
each MCL in history is a genuine new pass.
"""

from __future__ import annotations

import config


def value_stage_map(mapping: dict) -> dict:
    """{(property, value): canonical_stage} from a stage_mapping.json structure."""
    out = {}
    for stage, d in mapping["stages"].items():
        out[(d["property"], d["value"])] = stage
    return out


def segment(transitions) -> list[dict]:
    """transitions: iterable of (entered_at_iso, canonical_stage).

    Returns event dicts: {cycle, cycle_type, stage, entered_at}, one per
    (cycle, stage) first-entry. Includes MTL events (attached to their cycle).
    """
    target = config.TARGET_STAGE          # "MTL"
    stages = config.FUNNEL_STAGES          # MCL..Customer
    anchor = stages[0]                      # "MCL"
    customer = stages[-1]                   # "Customer"
    valid = set(stages) | {target}

    txns = sorted((t for t in transitions if t[1] in valid), key=lambda x: x[0])

    cycles: list[dict] = []
    cur: dict | None = None
    pending_mtl = None

    for ts, stage in txns:
        if stage == target:
            pending_mtl = ts                # remember; attach to the next cycle
            continue
        if stage == anchor or cur is None:  # MCL starts a pass; or history began mid-funnel
            cur = {}
            cycles.append(cur)
            if pending_mtl is not None:
                cur[target] = pending_mtl
                pending_mtl = None
        cur.setdefault(stage, ts)

    if pending_mtl is not None:             # trailing target with no capture yet
        cycles.append({target: pending_mtl})

    events = []
    reached_customer = False
    for i, c in enumerate(cycles, start=1):
        ctype = "resell" if reached_customer else "new"
        for stage, ts in c.items():
            events.append({"cycle": i, "cycle_type": ctype, "stage": stage, "entered_at": ts})
        if customer in c:
            reached_customer = True
    return events
