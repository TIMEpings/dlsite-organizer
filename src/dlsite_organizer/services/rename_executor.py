"""Safe execution of already validated RenamePlan objects."""

from __future__ import annotations

import logging
import ntpath
import os
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from dlsite_organizer.domain.organizer import RenamePlan, RenamePlanStatus
from dlsite_organizer.domain.rename_execution import (
    ExecutionStatus,
    RenameExecutionResult,
    RenameOperation,
    RenameTransaction,
    TransactionStatus,
)
from dlsite_organizer.persistence.rename_journal import RenameJournal
from dlsite_organizer.services.mutation_gate import MutationGate, shared_mutation_gate
from dlsite_organizer.services.mutation_history import (
    MutationHistoryChanged,
    notify_mutation_history_changed,
)

logger = logging.getLogger(__name__)

ExecutionProgressCallback = Callable[[int, int, Path, Path], None]


class RenameFilesystem(Protocol):
    """Minimal mutation seam used by the executor and deterministic tests."""

    def rename(self, source: Path, target: Path) -> None:
        """Perform one same-parent rename without replacing the target."""
        ...


class LocalRenameFilesystem:
    """Use the standard same-filesystem Path.rename primitive."""

    def rename(self, source: Path, target: Path) -> None:
        # The preflight check is repeated at the mutation seam.  On Windows,
        # Path.rename maps to a non-replacing rename operation.  The explicit
        # check also gives test doubles and non-Windows runs the same policy.
        if _lexists(target):
            raise FileExistsError(str(target))
        source.rename(target)


class RenameExecutor:
    """Execute only confirmed READY plans after a batch-wide revalidation."""

    def __init__(
        self,
        journal: RenameJournal | None,
        *,
        filesystem: RenameFilesystem | None = None,
        case_insensitive: bool = True,
        mutation_history_changed: MutationHistoryChanged | None = None,
        mutation_gate: MutationGate | None = None,
    ) -> None:
        self._journal = journal
        self._filesystem = filesystem or LocalRenameFilesystem()
        self._case_insensitive = case_insensitive
        self._mutation_history_changed = mutation_history_changed
        self._mutation_gate = mutation_gate or shared_mutation_gate()

    @property
    def mutation_gate(self) -> MutationGate:
        """Return the gate protecting this executor's complete transactions."""
        return self._mutation_gate

    @property
    def available(self) -> bool:
        """Whether a durable journal is available for a mutation."""
        return (
            self._journal is not None
            and _journal_available(self._journal)
            and (os.name == 'nt' or not isinstance(self._filesystem, LocalRenameFilesystem))
        )

    def unresolved_transaction(self) -> RenameTransaction | None:
        if self._journal is None or not _journal_available(self._journal):
            return None
        return self._journal.find_unresolved_transaction()

    def execute(
        self,
        root_path: Path | str,
        plans: Sequence[RenamePlan],
        *,
        confirmed: bool = False,
        progress_callback: ExecutionProgressCallback | None = None,
    ) -> RenameExecutionResult:
        """Serialize and execute one complete rename transaction."""
        with self._mutation_gate.acquire():
            return self._execute(
                root_path,
                plans,
                confirmed=confirmed,
                progress_callback=progress_callback,
            )

    def _execute(
        self,
        root_path: Path | str,
        plans: Sequence[RenamePlan],
        *,
        confirmed: bool = False,
        progress_callback: ExecutionProgressCallback | None = None,
    ) -> RenameExecutionResult:
        """Execute a selected batch, or return a no-mutation rejection.

        Confirmation is an explicit application input, not inferred from the
        presence of plans.  The journal intent is committed before the first
        filesystem mutation, and every successful mutation is committed before
        the next one starts.
        """
        ordered = tuple(sorted(plans, key=self._plan_sort_key))
        if not ordered:
            return RenameExecutionResult(
                status=TransactionStatus.FAILED,
                transaction=None,
                error='没有选中的 READY 重命名计划。',
            )
        if not confirmed:
            return self._no_mutation_result(
                ordered,
                ExecutionStatus.REJECTED,
                '未收到明确确认，未执行任何文件操作。',
            )
        if any(plan.status is not RenamePlanStatus.READY for plan in ordered):
            return self._no_mutation_result(
                ordered,
                ExecutionStatus.REJECTED,
                '只有 READY 重命名计划允许执行。',
            )
        if self._journal is None or not _journal_available(self._journal):
            return self._no_mutation_result(
                ordered,
                ExecutionStatus.REJECTED,
                '事务日志不可用，已禁止文件系统重命名。',
            )
        assert self._journal is not None
        if os.name != 'nt' and isinstance(self._filesystem, LocalRenameFilesystem):
            return self._no_mutation_result(
                ordered, ExecutionStatus.REJECTED, '真实文件系统重命名仅支持 Windows'
            )
        try:
            unresolved = self._journal.find_unresolved_transaction()
        except Exception as exc:
            return self._no_mutation_result(
                ordered, ExecutionStatus.REJECTED, f'无法检查 journal health，已阻止重命名: {exc}'
            )
        if unresolved is not None:
            return self._no_mutation_result(
                ordered, ExecutionStatus.REJECTED, _unresolved_message(unresolved)
            )
        root = _absolute_path(root_path)
        issues = _preflight(root, ordered, case_insensitive=self._case_insensitive)
        if issues:
            logger.warning(
                'Rename batch preflight rejected: root=%s operations=%d issues=%s',
                root,
                len(ordered),
                issues,
            )
            return RenameExecutionResult(
                status=TransactionStatus.FAILED,
                transaction=None,
                operations=tuple(
                    _uncommitted_operation(
                        plan,
                        index,
                        ExecutionStatus.PRECONDITION_FAILED,
                        issues.get(index, '整批 preflight 未通过。'),
                    )
                    for index, plan in enumerate(ordered, start=1)
                ),
                error='执行前验证失败：整批未执行，请重新扫描并确认。',
            )

        try:
            transaction = self._journal.create_transaction(
                root,
                tuple((plan.source_path, _require_target(plan)) for plan in ordered),
            )
        except Exception:
            logger.exception('Could not create journal before rename mutation')
            return RenameExecutionResult(
                status=TransactionStatus.FAILED,
                transaction=None,
                operations=tuple(
                    _uncommitted_operation(
                        plan,
                        index,
                        ExecutionStatus.REJECTED,
                        '事务日志写入失败，未执行任何文件操作。',
                    )
                    for index, plan in enumerate(ordered, start=1)
                ),
                error='无法建立事务日志，未执行任何文件操作。',
            )

        logger.info(
            'Starting rename transaction id=%s root=%s operation_count=%d',
            transaction.transaction_id,
            root,
            len(ordered),
        )
        current_operations = list(transaction.operations)
        for index, operation in enumerate(transaction.operations):
            if progress_callback is not None:
                progress_callback(
                    index + 1,
                    len(transaction.operations),
                    operation.source_path,
                    operation.target_path,
                )
            try:
                issue = _last_mile_issue(root, operation, case_insensitive=self._case_insensitive)
                if issue is not None:
                    raise _PreconditionError(issue)
                self._filesystem.rename(operation.source_path, operation.target_path)
            except Exception as exc:
                error = _filesystem_error(exc, operation.source_path, operation.target_path)
                logger.exception(
                    'Rename failed transaction=%s sequence=%d source=%s target=%s',
                    transaction.transaction_id,
                    operation.sequence,
                    operation.source_path,
                    operation.target_path,
                )
                current_operations[index] = replace(
                    operation,
                    status=(
                        ExecutionStatus.PRECONDITION_FAILED
                        if isinstance(exc, _PreconditionError)
                        else ExecutionStatus.FAILED
                    ),
                    error=error,
                )
                status = (
                    TransactionStatus.PARTIAL
                    if any(item.status is ExecutionStatus.SUCCESS for item in current_operations)
                    else TransactionStatus.FAILED
                )
                try:
                    self._journal.record_execution_failure(
                        transaction.transaction_id,
                        operation.sequence,
                        error,
                        status,
                        _now(),
                    )
                    self._notify_mutation_history_changed()
                except Exception as journal_exc:
                    return self._recovery_result(
                        transaction,
                        current_operations,
                        operation.sequence,
                        f'{error}；同时无法写入失败 journal：{journal_exc}',
                    )
                snapshot = self._read_transaction_or_local(
                    transaction.transaction_id,
                    transaction,
                    current_operations,
                )
                logger.info(
                    'Rename transaction=%s stopped status=%s',
                    transaction.transaction_id,
                    status,
                )
                return RenameExecutionResult(
                    status=status,
                    transaction=snapshot,
                    operations=snapshot.operations,
                    error='重命名已停止，后续操作未执行。',
                )

            current_operations[index] = replace(
                operation,
                status=ExecutionStatus.SUCCESS,
                executed_at=_now(),
            )
            try:
                self._journal.mark_operation_success(
                    transaction.transaction_id,
                    operation.sequence,
                    current_operations[index].executed_at or _now(),
                )
            except Exception as exc:
                recovery_error = (
                    '文件系统重命名已经成功，但 success journal 更新失败；'
                    f'事务需要恢复：{exc}'
                )
                return self._recovery_result(
                    transaction,
                    current_operations,
                    operation.sequence,
                    recovery_error,
                )
            logger.info(
                'Rename succeeded transaction=%s sequence=%d source=%s target=%s',
                transaction.transaction_id,
                operation.sequence,
                operation.source_path,
                operation.target_path,
            )

        try:
            self._journal.mark_transaction_completed(transaction.transaction_id, _now())
            self._notify_mutation_history_changed()
        except Exception as exc:
            return self._recovery_result(
                transaction,
                current_operations,
                None,
                f'所有文件操作已尝试完成，但 transaction completion journal 更新失败：{exc}',
            )
        snapshot = self._read_transaction_or_local(
            transaction.transaction_id,
            transaction,
            current_operations,
        )
        logger.info('Rename transaction completed id=%s', transaction.transaction_id)
        return RenameExecutionResult(
            status=TransactionStatus.COMPLETED,
            transaction=snapshot,
            operations=snapshot.operations,
        )

    def _plan_sort_key(self, plan: RenamePlan) -> tuple[str, str]:
        return (
            _path_key(_absolute_path(plan.source_path), self._case_insensitive),
            plan.current_name,
        )

    def _no_mutation_result(
        self,
        plans: Sequence[RenamePlan],
        status: ExecutionStatus,
        error: str,
    ) -> RenameExecutionResult:
        return RenameExecutionResult(
            status=TransactionStatus.FAILED,
            transaction=None,
            operations=tuple(
                _uncommitted_operation(plan, index, status, error)
                for index, plan in enumerate(plans, start=1)
            ),
            error=error,
        )

    def _read_transaction_or_local(
        self,
        transaction_id: str,
        original: RenameTransaction,
        operations: Sequence[RenameOperation],
    ) -> RenameTransaction:
        assert self._journal is not None
        try:
            return self._journal.get_transaction(transaction_id)
        except Exception:
            logger.exception('Could not read transaction snapshot %s', transaction_id)
            return replace(original, operations=tuple(operations))

    def _recovery_result(
        self,
        transaction: RenameTransaction,
        operations: Sequence[RenameOperation],
        sequence: int | None,
        error: str,
    ) -> RenameExecutionResult:
        assert self._journal is not None
        logger.critical(
            'Rename transaction requires recovery id=%s root=%s sequence=%s',
            transaction.transaction_id,
            transaction.root,
            sequence,
            exc_info=True,
        )
        try:
            self._journal.mark_recovery_required(
                transaction.transaction_id,
                sequence,
                error,
                _now(),
            )
            self._notify_mutation_history_changed()
        except Exception:
            logger.critical(
                'Could not persist recovery-required state transaction=%s',
                transaction.transaction_id,
                exc_info=True,
            )
        snapshot = self._read_transaction_or_local(
            transaction.transaction_id,
            replace(transaction, status=TransactionStatus.RECOVERY_REQUIRED),
            operations,
        )
        return RenameExecutionResult(
            status=TransactionStatus.RECOVERY_REQUIRED,
            transaction=snapshot,
            operations=snapshot.operations,
            error=error,
        )

    def _notify_mutation_history_changed(self) -> None:
        notify_mutation_history_changed(
            self._mutation_history_changed,
            logger=logger,
        )


def _preflight(
    root: Path,
    plans: Sequence[RenamePlan],
    *,
    case_insensitive: bool,
) -> dict[int, str]:
    issues: dict[int, list[str]] = {}

    def add(index: int, message: str) -> None:
        issues.setdefault(index, []).append(message)

    if _link_like(root) or not root.exists() or not root.is_dir():
        for index in range(1, len(plans) + 1):
            add(index, 'Organizer root 不存在、不是目录或是 link-like entry。')
        return {index: '；'.join(messages) for index, messages in issues.items()}

    source_keys: dict[str, list[int]] = {}
    target_keys: dict[str, list[int]] = {}
    for index, plan in enumerate(plans, start=1):
        if plan.status is not RenamePlanStatus.READY:
            add(index, '只有 READY 计划允许执行。')
        if plan.target_path is None:
            add(index, '计划缺少安全目标路径。')
            continue
        source = _absolute_path(plan.source_path)
        target = _absolute_path(plan.target_path)
        source_keys.setdefault(_path_key(source, case_insensitive), []).append(index)
        target_keys.setdefault(_path_key(target, case_insensitive), []).append(index)

        if _path_key(source.parent, case_insensitive) != _path_key(root, case_insensitive):
            add(index, 'source 不是 Organizer root 的直接子目录。')
        if _path_key(target.parent, case_insensitive) != _path_key(root, case_insensitive):
            add(index, 'target 不是 Organizer root 下的直接子目录。')
        if _path_key(source, case_insensitive) == _path_key(target, case_insensitive):
            add(index, '相同路径或仅大小写不同的目录名不执行重命名。')
        if _link_like(source):
            add(index, 'source 是 symlink/junction，已拒绝。')
        elif not _lexists(source):
            add(index, 'source 在执行前已不存在。')
        elif not source.is_dir():
            add(index, 'source 不再是目录。')
        if _entry_exists(root, target, case_insensitive):
            add(index, 'target 已存在，禁止覆盖。')

    for indexes in source_keys.values():
        if len(indexes) > 1:
            for index in indexes:
                add(index, 'batch 中包含重复 source。')
    for indexes in target_keys.values():
        if len(indexes) > 1:
            for index in indexes:
                add(index, 'batch 中包含重复 target。')
    for target_key, target_indexes in target_keys.items():
        source_indexes = source_keys.get(target_key, [])
        for target_index in target_indexes:
            for source_index in source_indexes:
                if source_index != target_index:
                    add(target_index, '检测到 source/target dependency chain 或 cycle。')
                    add(source_index, '检测到 source/target dependency chain 或 cycle。')

    return {index: '；'.join(messages) for index, messages in issues.items()}


def _uncommitted_operation(
    plan: RenamePlan,
    sequence: int,
    status: ExecutionStatus,
    error: str,
) -> RenameOperation:
    target = plan.target_path or plan.source_path
    return RenameOperation(
        transaction_id='',
        sequence=sequence,
        source_path=_absolute_path(plan.source_path),
        target_path=_absolute_path(target),
        status=status,
        error=error,
    )


def _require_target(plan: RenamePlan) -> Path:
    if plan.target_path is None:
        raise ValueError('READY plan has no target path')
    return _absolute_path(plan.target_path)


def _journal_available(journal: RenameJournal) -> bool:
    try:
        return journal.available
    except Exception:
        logger.exception('Could not inspect journal availability')
        return False


def _entry_exists(root: Path, path: Path, case_insensitive: bool) -> bool:
    if _lexists(path):
        return True
    try:
        return any(
            _path_key(child, case_insensitive) == _path_key(path, case_insensitive)
            for child in root.iterdir()
        )
    except OSError:
        return True


def _link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, 'is_junction', None)
        return bool(is_junction is not None and is_junction())
    except OSError:
        return True


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _absolute_path(path: Path | str) -> Path:
    return Path(path).expanduser().absolute()


def _path_key(path: Path, case_insensitive: bool) -> str:
    # Windows may present the same directory as an 8.3 short path or a long
    # path.  Resolve only for comparison; filesystem checks and mutations keep
    # the original absolute path so symlink/junction validation is preserved.
    value = ntpath.normpath(str(path.resolve(strict=False)))
    return value.casefold() if case_insensitive else value


def _filesystem_error(exc: Exception, source: Path, target: Path) -> str:
    if isinstance(exc, _PreconditionError):
        return str(exc)
    if isinstance(exc, FileExistsError):
        return f'target 已存在，未覆盖：{target}'
    if isinstance(exc, FileNotFoundError):
        return f'source 不存在：{source}'
    if isinstance(exc, PermissionError):
        return f'没有权限重命名：{source}'
    if isinstance(exc, OSError):
        return f'文件系统错误：{exc}'
    return f'重命名异常：{exc}'


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


class _PreconditionError(RuntimeError):
    pass


def _last_mile_issue(
    root: Path, operation: RenameOperation, *, case_insensitive: bool
) -> str | None:
    source, target = operation.source_path, operation.target_path
    if _path_key(source.parent, case_insensitive) != _path_key(root, case_insensitive):
        return 'source parent changed outside expected root'
    if _path_key(target.parent, case_insensitive) != _path_key(root, case_insensitive):
        return 'target parent changed outside expected root'
    if source == target or _path_key(source, case_insensitive) == _path_key(
        target, case_insensitive
    ):
        return 'source and target are identical or case-only'
    if _link_like(source) or not _lexists(source) or not source.is_dir():
        return 'source precondition changed before mutation'
    if _entry_exists(root, target, case_insensitive):
        return 'target appeared before mutation; refusing overwrite'
    if target.name in ('.', '..') or any(sep in target.name for sep in ('\\', '/')):
        return 'target is not a single safe filename component'
    return None


def _unresolved_message(transaction: RenameTransaction) -> str:
    return (f'检测到未解决的重命名事务: {transaction.transaction_id} '
            f'root={transaction.root} status={transaction.status.value}；已阻止新的文件系统修改')
