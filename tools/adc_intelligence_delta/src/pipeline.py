"""ADCSeed and ADCEvent extraction pipeline.

Integrates seed and event extraction into a unified workflow:
1. Load EvidenceRecords from any source
2. Extract ADCSeeds (target × indication hypotheses)
3. Extract ADCEvents (typed changes with dates)
4. Deduplicate and normalize
5. Output as JSON for downstream use

This is the v0.1 skeleton; fine-grained LLM event typing and entity
resolution (matching seeds/events to known assets) are later-PR work.
"""

from __future__ import annotations

from contracts import ADCEvent, ADCSeed, EvidenceRecord
from seed_extraction import extract_seeds_from_record, dedup_seeds
from event_extraction import extract_events_from_record


def process_records(
    records: list[EvidenceRecord],
) -> tuple[dict[str, ADCSeed], list[ADCEvent]]:
    """Process a batch of EvidenceRecords into seeds and events.

    Args:
        records: Normalized evidence records from any source

    Returns:
        (seeds_dict, events_list) where:
        - seeds_dict: {seed_id -> ADCSeed} (deduplicated by hypothesis)
        - events_list: [ADCEvent, ...] (one event per record that could be typed)
    """
    all_seeds = []
    all_events = []

    for record in records:
        # Extract seeds
        seeds = extract_seeds_from_record(record)
        all_seeds.extend(seeds)

        # Extract events
        # TODO: When entity resolution is implemented, look up asset_id
        # for this record here, rather than always passing None.
        events = extract_events_from_record(
            record,
            asset_id=None,  # TODO: entity resolution
            seed_id=None,   # TODO: seed matching against extracted seeds
        )
        all_events.extend(events)

    # Deduplicate seeds by hypothesis
    seeds_dict = dedup_seeds(all_seeds)

    return seeds_dict, all_events


def summarize_output(seeds: dict[str, ADCSeed], events: list[ADCEvent]) -> dict:
    """Generate summary statistics for a pipeline run."""
    event_types = {}
    for e in events:
        event_types[e.event_type] = event_types.get(e.event_type, 0) + 1

    return {
        "seeds_unique": len(seeds),
        "events_total": len(events),
        "event_breakdown": event_types,
    }
