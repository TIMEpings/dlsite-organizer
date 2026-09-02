from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from dlsite_organizer.app.quick_action_controller import (
    QuickActionAdmissionStatus,
    QuickActionController,
    QuickActionFinished,
    QuickActionRequest,
)


@dataclass
class FakeRunner:
    request: QuickActionRequest
    completion: object
    started_paths: list[Path]
    start_error: Exception | None = None

    def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started_paths.append(self.request.path)

    def finish(self, result: object | None = None, error: object | None = None) -> None:
        self.completion(result, error)  # type: ignore[operator]


@pytest.fixture
def controller_factory():
    runners: list[FakeRunner] = []
    started_paths: list[Path] = []

    def factory(request: QuickActionRequest, completion) -> FakeRunner:
        runner = FakeRunner(request, completion, started_paths)
        runners.append(runner)
        return runner

    return factory, runners, started_paths


def test_first_starts_and_distinct_requests_are_fifo(controller_factory, tmp_path: Path) -> None:
    factory, runners, started_paths = controller_factory
    controller = QuickActionController(factory)
    finished: list[QuickActionFinished] = []
    controller.request_finished.connect(finished.append)

    first = tmp_path / "X"
    second = tmp_path / "Y"
    third = tmp_path / "Z"
    assert controller.submit(first).status is QuickActionAdmissionStatus.ACCEPTED
    assert controller.submit(second).status is QuickActionAdmissionStatus.ACCEPTED
    assert controller.submit(third).status is QuickActionAdmissionStatus.ACCEPTED
    assert started_paths == [first.absolute()]
    assert controller.queue_depth == 2

    runners[0].finish("x")
    runners[1].finish("y")
    runners[2].finish("z")

    assert started_paths == [first.absolute(), second.absolute(), third.absolute()]
    assert [item.request.path for item in finished] == [
        first.absolute(),
        second.absolute(),
        third.absolute(),
    ]
    assert controller.active_request is None
    assert controller.queue_depth == 0


def test_capacity_is_one_active_plus_eight_pending(controller_factory, tmp_path: Path) -> None:
    factory, runners, _started_paths = controller_factory
    controller = QuickActionController(factory)

    paths = [tmp_path / f"work-{index}" for index in range(10)]
    results = [controller.submit(path) for path in paths]

    assert [result.status for result in results[:9]] == [
        QuickActionAdmissionStatus.ACCEPTED
    ] * 9
    assert results[9].status is QuickActionAdmissionStatus.QUEUE_FULL
    assert len(runners) == 1
    assert controller.queue_depth == 8


def test_active_and_pending_duplicates_are_rejected(controller_factory, tmp_path: Path) -> None:
    factory, runners, _started_paths = controller_factory
    controller = QuickActionController(factory)
    path = tmp_path / "Work"

    assert controller.submit(path).accepted
    assert controller.submit(path).status is QuickActionAdmissionStatus.DUPLICATE
    assert controller.submit(path.parent / "./Work").status is QuickActionAdmissionStatus.DUPLICATE

    runners[0].finish()
    assert controller.submit(path).status is QuickActionAdmissionStatus.ACCEPTED


def test_start_failure_clears_active_state_and_allows_next_request(
    tmp_path: Path,
) -> None:
    started_paths: list[Path] = []

    def factory(request: QuickActionRequest, _completion):
        return FakeRunner(request, _completion, started_paths, RuntimeError("boom"))

    controller = QuickActionController(factory)
    finished: list[QuickActionFinished] = []
    controller.request_finished.connect(finished.append)

    result = controller.submit(tmp_path / "X")

    assert result.accepted
    assert controller.active_request is None
    assert controller.queue_depth == 0
    assert len(finished) == 1
    assert finished[0].error is not None and "runner start failed" in finished[0].error
    assert controller.submit(tmp_path / "X").accepted


def test_controlled_failure_advances_fifo_queue(controller_factory, tmp_path: Path) -> None:
    factory, runners, started_paths = controller_factory
    controller = QuickActionController(factory)
    finished: list[QuickActionFinished] = []
    controller.request_finished.connect(finished.append)
    first = tmp_path / "X"
    second = tmp_path / "Y"

    controller.submit(first)
    controller.submit(second)
    runners[0].finish(error="controlled failure")

    assert started_paths == [first.absolute(), second.absolute()]
    assert finished[0].error == "controlled failure"
    assert controller.active_request == runners[1].request


def test_shutdown_rejects_new_clears_pending_and_does_not_start_next(
    controller_factory, tmp_path: Path
) -> None:
    factory, runners, started_paths = controller_factory
    controller = QuickActionController(factory)
    first = tmp_path / "X"
    second = tmp_path / "Y"
    third = tmp_path / "Z"

    controller.submit(first)
    controller.submit(second)
    controller.submit(third)
    controller.begin_shutdown()

    assert controller.queue_depth == 0
    assert controller.submit(tmp_path / "new").status is QuickActionAdmissionStatus.SHUTTING_DOWN
    runners[0].finish()

    assert started_paths == [first.absolute()]
    assert controller.active_request is None
    assert len(runners) == 1


def test_signals_report_state_and_duplicate_completion_is_ignored(
    controller_factory, tmp_path: Path
) -> None:
    factory, runners, _started_paths = controller_factory
    controller = QuickActionController(factory)
    started: list[Path] = []
    finished: list[QuickActionFinished] = []
    depths: list[int] = []
    active: list[QuickActionRequest | None] = []
    controller.request_started.connect(lambda request: started.append(request.path))
    controller.request_finished.connect(finished.append)
    controller.queue_depth_changed.connect(depths.append)
    controller.active_changed.connect(active.append)

    controller.submit(tmp_path / "X")
    controller.submit(tmp_path / "Y")
    runners[0].finish("done")
    runners[0].finish("stale")
    runners[1].finish("done")

    assert started == [(tmp_path / "X").absolute(), (tmp_path / "Y").absolute()]
    assert [item.result for item in finished] == ["done", "done"]
    assert depths == [1, 0]
    assert active[-1] is None


def test_invalid_path_shape_is_rejected_without_runner(controller_factory) -> None:
    factory, runners, _started_paths = controller_factory
    controller = QuickActionController(factory)

    result = controller.submit(None)  # type: ignore[arg-type]

    assert result.status is QuickActionAdmissionStatus.REJECTED
    assert not runners


def test_one_batch_stays_one_fifo_request_and_exposes_all_paths(
    controller_factory, tmp_path: Path
) -> None:
    factory, runners, started_paths = controller_factory
    controller = QuickActionController(factory)
    first = tmp_path / "X"
    second = tmp_path / "Y"
    third = tmp_path / "Z"

    admission = controller.submit((first, second))
    queued = controller.submit(third)

    assert admission.accepted
    assert admission.paths == (first.absolute(), second.absolute())
    assert admission.request is not None
    assert admission.request.path == first.absolute()
    assert admission.request.paths == (first.absolute(), second.absolute())
    assert queued.accepted
    assert controller.queue_depth == 1
    assert len(runners) == 1
    assert started_paths == [first.absolute()]

    runners[0].finish("batch")
    assert len(runners) == 2
    assert runners[1].request.paths == (third.absolute(),)


def test_duplicate_identity_inside_batch_is_rejected_without_partial_admission(
    controller_factory, tmp_path: Path
) -> None:
    factory, runners, _started_paths = controller_factory
    controller = QuickActionController(factory)
    path = tmp_path / "Work"

    result = controller.submit((path, path.parent / "." / path.name))

    assert result.status is QuickActionAdmissionStatus.REJECTED
    assert controller.active_request is None
    assert controller.queue_depth == 0
    assert not runners
