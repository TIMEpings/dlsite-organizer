"""Offline append reconciliation timings over real ignored sandbox directories."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import uuid4

from dlsite_organizer.domain.organizer import ScanResult
from dlsite_organizer.domain.work import Work
from dlsite_organizer.services.organizer import OrganizerPreview, OrganizerService
from dlsite_organizer.services.rename_planner import RenamePlanner, RenamePlanningRequest
from dlsite_organizer.ui.state.organizer_state import merge_previews


class NoLookup:
    calls = 0

    def lookup(self, code):
        self.calls += 1
        raise AssertionError(f"Reconciliation must reuse metadata: {code}")


def main():
    output = Path.cwd() / "research" / f"phase652-perf-{uuid4().hex[:8]}"
    output.mkdir(parents=True)
    lookup = NoLookup()
    service = OrganizerService(lookup)
    planner = RenamePlanner()
    work = Work(workno="RJ000001", title="Offline benchmark")
    evidence = {"samples": 5, "component": "merge_previews + service.reevaluate_preview",
                "scenarios": []}
    for count in (100, 1000, 5000):
        root = output / str(count)
        root.mkdir()
        sources = [root / f"source-{i:05}" for i in range(count + 20)]
        for source in sources:
            source.mkdir()
        plans = planner.plan_batch(root, [RenamePlanningRequest(
            source, work.workno, work, f"target-{i:05}")
            for i, source in enumerate(sources)])
        current = OrganizerPreview(root, ScanResult(root, (), ()), (), plans[:count])
        incoming = replace(current, plans=plans[count:count + 1])
        samples = []
        for _ in range(5):
            started = perf_counter()
            result = service.reevaluate_preview(
                merge_previews(current, incoming), recheck_ready=True)
            samples.append((perf_counter() - started) * 1000)
            assert len(result.plans) == count + 1
        evidence["scenarios"].append({"existing": count, "append": 1,
                                      "median_ms": round(median(samples), 3),
                                      "samples_ms": [round(v, 3) for v in samples]})
        if count == 100:
            samples = []
            for _ in range(5):
                result = current
                started = perf_counter()
                for plan in plans[count:]:
                    result = service.reevaluate_preview(merge_previews(
                        result, replace(current, plans=(plan,))), recheck_ready=True)
                samples.append((perf_counter() - started) * 1000)
                assert len(result.plans) == 120
            evidence["repeated_100_plus_20"] = {
                "median_ms": round(median(samples), 3),
                "samples_ms": [round(v, 3) for v in samples]}
    assert lookup.calls == 0
    evidence["lookup_calls"] = lookup.calls
    (output / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), **evidence}, indent=2))


if __name__ == "__main__":
    main()
