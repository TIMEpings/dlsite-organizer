"""Journal-driven, preflighted undo for the latest rename transaction."""

from __future__ import annotations

import logging
import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from dlsite_organizer.domain.rename_execution import (
    ExecutionStatus,
    RenameOperation,
    RenameTransaction,
    TransactionStatus,
    UndoResult,
    UndoStatus,
    UndoTransactionState,
)
from dlsite_organizer.persistence.rename_journal import RenameJournal
from dlsite_organizer.services.mutation_gate import MutationGate, shared_mutation_gate
from dlsite_organizer.services.mutation_history import (
    MutationHistoryChanged,
    notify_mutation_history_changed,
)
from dlsite_organizer.services.rename_executor import (
    ExecutionProgressCallback,
    LocalRenameFilesystem,
    RenameFilesystem,
    _absolute_path,
    _entry_exists,
    _link_like,
    _path_key,
)

logger = logging.getLogger(__name__)


class UndoService:
    """Undo successful journal operations in reverse execution order."""

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
        self._filesystem = filesystem
        self._case_insensitive = case_insensitive
        self._mutation_history_changed = mutation_history_changed
        self._mutation_gate = mutation_gate or shared_mutation_gate()

    @property
    def mutation_gate(self) -> MutationGate:
        """Return the gate protecting this service's complete undo transaction."""
        return self._mutation_gate

    def latest_transaction(self) -> RenameTransaction | None:
        """Return the most recent transaction with at least one undoable op."""
        if self._journal is None or not self._journal.available:
            return None
        assert self._journal is not None
        return self._journal.latest_undoable()

    def unresolved_transaction(self) -> RenameTransaction | None:
        if self._journal is None or not _journal_available(self._journal):
            return None
        return self._journal.find_unresolved_transaction()

    def undo(
        self,
        transaction_id: str | None = None,
        *,
        confirmed: bool = False,
        progress_callback: ExecutionProgressCallback | None = None,
    ) -> UndoResult:
        """Serialize and undo one complete journal transaction."""
        with self._mutation_gate.acquire():
            return self._undo(
                transaction_id,
                confirmed=confirmed,
                progress_callback=progress_callback,
            )

    def _undo(
        self,
        transaction_id: str | None = None,
        *,
        confirmed: bool = False,
        progress_callback: ExecutionProgressCallback | None = None,
    ) -> UndoResult:
        """Undo journaled successful operations after an all-operation preflight."""
        if self._journal is None or not _journal_available(self._journal):
            return UndoResult(
                status=TransactionStatus.FAILED,
                transaction=None,
                error='事务日志不可用，已禁止撤销文件操作。',
            )
        assert self._journal is not None
        try:
            unresolved = self._journal.find_unresolved_transaction()
        except Exception as exc:
            return UndoResult(
                status=TransactionStatus.FAILED,
                transaction=None,
                error=f'journal health check failed: {exc}',
            )
        if unresolved is not None:
            return UndoResult(
                status=TransactionStatus.RECOVERY_REQUIRED,
                transaction=unresolved,
                operations=unresolved.operations,
                error='unresolved transaction blocks undo',
            )
        if os.name != 'nt' and isinstance(self._filesystem, LocalRenameFilesystem):
            return UndoResult(
                status=TransactionStatus.FAILED,
                transaction=None,
                error='真实文件系统重命名仅支持 Windows',
            )

        try:
            transaction = (
                self._journal.latest_undoable()
                if transaction_id is None
                else self._journal.get_transaction(transaction_id)
            )
        except Exception as exc:
            logger.exception('Could not load transaction for undo')
            return UndoResult(
                status=TransactionStatus.FAILED,
                transaction=None,
                error=f'无法读取待撤销事务：{exc}',
            )
        if transaction is None:
            return UndoResult(
                status=TransactionStatus.FAILED,
                transaction=None,
                error='没有可撤销的重命名事务。',
            )

        pending = [
            operation
            for operation in transaction.operations
            if operation.status is ExecutionStatus.SUCCESS
            and operation.undo_status is not UndoStatus.SUCCESS
        ]
        if transaction.status is TransactionStatus.UNDONE or not pending:
            return UndoResult(
                status=TransactionStatus.FAILED,
                transaction=transaction,
                operations=transaction.operations,
                error='该重命名事务已经撤销，或没有剩余可撤销操作。',
            )
        if transaction.unresolved:
            return UndoResult(
                status=TransactionStatus.RECOVERY_REQUIRED,
                transaction=transaction,
                operations=transaction.operations,
                error='事务处于需要恢复状态，不能直接执行撤销。',
            )
        if transaction.status not in {
            TransactionStatus.COMPLETED,
            TransactionStatus.PARTIAL,
            TransactionStatus.UNDO_PARTIAL,
        }:
            return UndoResult(
                status=TransactionStatus.FAILED,
                transaction=transaction,
                operations=transaction.operations,
                error='该事务状态不允许撤销。',
            )
        if not confirmed:
            return UndoResult(
                status=TransactionStatus.FAILED,
                transaction=transaction,
                operations=transaction.operations,
                error='未收到明确确认，未执行任何撤销文件操作。',
            )

        root = _absolute_path(transaction.root)
        ordered = tuple(sorted(pending, key=lambda item: item.sequence, reverse=True))
        preflight = _undo_preflight(
            root,
            ordered,
            case_insensitive=self._case_insensitive,
        )
        issues = preflight.issues
        if issues:
            for operation in ordered:
                error = issues.get(operation.sequence, '撤销整批 preflight 未通过。')
                try:
                    self._journal.mark_undo_conflict(
                        transaction.transaction_id,
                        operation.sequence,
                        error,
                    )
                    self._notify_mutation_history_changed()
                except Exception:
                    logger.exception(
                        'Could not persist undo conflict transaction=%s sequence=%d',
                        transaction.transaction_id,
                        operation.sequence,
                    )
            snapshot = _read_or_local(self._journal, transaction, transaction.operations)
            return UndoResult(
                status=TransactionStatus.FAILED,
                transaction=snapshot,
                operations=snapshot.operations,
                error='撤销前验证失败：整批未执行，请处理冲突后重试。',
            )

        when = _now()
        try:
            self._journal.begin_undo(transaction.transaction_id, when)
        except Exception as exc:
            return UndoResult(
                status=TransactionStatus.FAILED,
                transaction=transaction,
                operations=transaction.operations,
                error=f'无法写入撤销意图，未执行任何撤销文件操作：{exc}',
            )
        transaction = replace(
            transaction,
            undo_state=UndoTransactionState.IN_PROGRESS,
            undo_started_at=when,
            undo_completed_at=None,
            recovery_stage='undo',
            recovery_error=None,
            recovery_sequence=None,
        )
        self._notify_mutation_history_changed()

        filesystem = self._filesystem
        if filesystem is None:
            filesystem = LocalRenameFilesystem()
        current_operations = list(transaction.operations)
        by_sequence = {
            operation.sequence: index
            for index, operation in enumerate(current_operations)
        }
        for count, operation in enumerate(ordered, start=1):
            if progress_callback is not None:
                progress_callback(
                    count,
                    len(ordered),
                    operation.target_path,
                    operation.source_path,
                )
            try:
                _undo_operation_identity(
                    root,
                    operation,
                    case_insensitive=self._case_insensitive,
                    root_identity=preflight.root_identity,
                    current_identity=preflight.current_identities[operation.sequence],
                )
                filesystem.rename(operation.target_path, operation.source_path)
            except Exception as exc:
                error = _undo_filesystem_error(exc, operation.target_path, operation.source_path)
                logger.exception(
                    'Undo failed transaction=%s sequence=%d source=%s target=%s',
                    transaction.transaction_id,
                    operation.sequence,
                    operation.target_path,
                    operation.source_path,
                )
                index = by_sequence[operation.sequence]
                current_operations[index] = replace(
                    operation,
                    undo_status=UndoStatus.FAILED,
                    undo_error=error,
                )
                try:
                    self._journal.record_undo_failure(
                        transaction.transaction_id,
                        operation.sequence,
                        error,
                        _now(),
                    )
                    self._notify_mutation_history_changed()
                except Exception as journal_exc:
                    return self._recovery_result(
                        transaction,
                        current_operations,
                        operation.sequence,
                        f'{error}；同时无法写入 undo journal：{journal_exc}',
                    )
                snapshot = _read_or_local(
                    self._journal,
                    transaction,
                    current_operations,
                )
                return UndoResult(
                    status=TransactionStatus.UNDO_PARTIAL,
                    transaction=snapshot,
                    operations=snapshot.operations,
                    error='撤销已停止，后续撤销操作未执行。',
                )

            when = _now()
            index = by_sequence[operation.sequence]
            current_operations[index] = replace(
                operation,
                undo_status=UndoStatus.SUCCESS,
                undone_at=when,
            )
            try:
                self._journal.mark_undo_success(
                    transaction.transaction_id,
                    operation.sequence,
                    when,
                )
            except Exception as exc:
                return self._recovery_result(
                    transaction,
                    current_operations,
                    operation.sequence,
                    f'撤销文件操作已经成功，但 undo journal 更新失败；事务需要恢复：{exc}',
                )
            logger.info(
                'Undo succeeded transaction=%s sequence=%d source=%s target=%s',
                transaction.transaction_id,
                operation.sequence,
                operation.target_path,
                operation.source_path,
            )

        try:
            self._journal.mark_transaction_undone(transaction.transaction_id, _now())
            self._notify_mutation_history_changed()
        except Exception as exc:
            return self._recovery_result(
                transaction,
                current_operations,
                None,
                f'撤销文件操作已完成，但 transaction journal 更新失败：{exc}',
            )
        snapshot = _read_or_local(self._journal, transaction, current_operations)
        logger.info(
            'Undo transaction=%s result=%s',
            transaction.transaction_id,
            TransactionStatus.UNDONE,
        )
        return UndoResult(
            status=TransactionStatus.UNDONE,
            transaction=snapshot,
            operations=snapshot.operations,
        )

    def _recovery_result(
        self,
        transaction: RenameTransaction,
        operations: Sequence[RenameOperation],
        sequence: int | None,
        error: str,
    ) -> UndoResult:
        assert self._journal is not None
        logger.critical(
            'Undo transaction requires recovery id=%s root=%s sequence=%s',
            transaction.transaction_id,
            transaction.root,
            sequence,
        )
        try:
            self._journal.mark_undo_recovery_required(
                transaction.transaction_id,
                sequence,
                error,
                _now(),
            )
            self._notify_mutation_history_changed()
        except Exception:
            logger.critical(
                'Could not persist undo recovery state transaction=%s',
                transaction.transaction_id,
                exc_info=True,
            )
        snapshot = _read_or_local(
            self._journal,
            replace(transaction, undo_state=UndoTransactionState.RECOVERY_REQUIRED),
            operations,
        )
        return UndoResult(
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


_DirectoryIdentity = tuple[int, int, int | None]


class _UndoPreconditionError(RuntimeError):
    """A known eligibility failure, before any mutation of this operation."""


@dataclass(frozen=True)
class _UndoPreflight:
    issues: dict[int, str]
    root_identity: _DirectoryIdentity | None
    current_identities: dict[int, _DirectoryIdentity]


def _directory_identity(path: Path, role: str) -> _DirectoryIdentity:
    """Inspect the entry without following links; retain only stable metadata."""
    if _link_like(path):
        raise _UndoPreconditionError(f'{role} 是 symlink/junction 或无法安全检查，已拒绝。')
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise _UndoPreconditionError(f'{role} 已不存在：{path}') from exc
    if stat.S_ISLNK(metadata.st_mode) or (
        getattr(metadata, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise _UndoPreconditionError(f'{role} 是 link-like/reparse entry，已拒绝。')
    if not stat.S_ISDIR(metadata.st_mode):
        raise _UndoPreconditionError(f'{role} 不再是目录：{path}')
    if not metadata.st_ino:
        raise _UndoPreconditionError(f'{role} 无法取得目录身份，已拒绝：{path}')
    # Path.lstat (unlike cached DirEntry.stat on Windows) exposes the file index.
    # Birth time, when available, reduces file-index reuse ambiguity. Do not use
    # mtime/ctime: normal child mutations may change them during a multi-op undo.
    return metadata.st_dev, metadata.st_ino, getattr(metadata, 'st_birthtime_ns', None)


def _undo_operation_identity(
    root: Path,
    operation: RenameOperation,
    *,
    case_insensitive: bool,
    root_identity: _DirectoryIdentity | None,
    current_identity: _DirectoryIdentity | None = None,
) -> _DirectoryIdentity:
    """Recheck one reverse operation under the retained mutation gate.

    These pathname observations are not atomic with the subsequent rename.
    """
    observed_root = _directory_identity(root, 'transaction root')
    if root_identity is not None and observed_root != root_identity:
        raise _UndoPreconditionError('transaction root 目录身份已改变，已拒绝。')
    current = _absolute_path(operation.target_path)
    original = _absolute_path(operation.source_path)
    root_key = _path_key(root, case_insensitive)
    if _path_key(current.parent, case_insensitive) != root_key:
        raise _UndoPreconditionError('undo current path 不在 transaction root 的直接子目录中。')
    if _path_key(original.parent, case_insensitive) != root_key:
        raise _UndoPreconditionError('undo original path 不在 transaction root 的直接子目录中。')
    observed_current = _directory_identity(current, '重命名后的路径')
    if current_identity is not None and observed_current != current_identity:
        raise _UndoPreconditionError('重命名后的目录身份已改变，已拒绝。')
    if _entry_exists(root, original, case_insensitive):
        raise _UndoPreconditionError('原始目录位置已有对象，禁止覆盖。')
    if current == original or _path_key(current, case_insensitive) == _path_key(
        original, case_insensitive,
    ):
        raise _UndoPreconditionError('undo source/target 是相同或 case-only 路径。')
    return observed_current


def _undo_preflight(
    root: Path,
    operations: Sequence[RenameOperation],
    *,
    case_insensitive: bool,
) -> _UndoPreflight:
    issues: dict[int, list[str]] = {}
    current_identities: dict[int, _DirectoryIdentity] = {}

    def add(sequence: int, message: str) -> None:
        issues.setdefault(sequence, []).append(message)

    try:
        root_identity = _directory_identity(root, 'transaction root')
    except (OSError, _UndoPreconditionError) as exc:
        return _UndoPreflight(
            {operation.sequence: str(exc) for operation in operations}, None, {},
        )

    source_keys: dict[str, list[int]] = {}
    target_keys: dict[str, list[int]] = {}
    for operation in operations:
        current = _absolute_path(operation.target_path)
        original = _absolute_path(operation.source_path)
        try:
            current_identities[operation.sequence] = _undo_operation_identity(
                root, operation, case_insensitive=case_insensitive, root_identity=root_identity,
            )
            source_key = _path_key(original, case_insensitive)
            target_key = _path_key(current, case_insensitive)
        except (OSError, _UndoPreconditionError) as exc:
            add(operation.sequence, str(exc))
            continue
        source_keys.setdefault(source_key, []).append(operation.sequence)
        target_keys.setdefault(target_key, []).append(operation.sequence)

    for values in source_keys.values():
        if len(values) > 1:
            for sequence in values:
                add(sequence, 'undo batch 中包含重复 original path。')
    for values in target_keys.values():
        if len(values) > 1:
            for sequence in values:
                add(sequence, 'undo batch 中包含重复 current path。')
    for target_key, target_sequences in target_keys.items():
        for source_sequence in source_keys.get(target_key, []):
            if source_sequence not in target_sequences:
                add(source_sequence, 'undo 检测到 source/target dependency chain。')
                for sequence in target_sequences:
                    add(sequence, 'undo 检测到 source/target dependency chain。')
    return _UndoPreflight(
        {sequence: '；'.join(messages) for sequence, messages in issues.items()},
        root_identity,
        current_identities,
    )


def _read_or_local(
    journal: RenameJournal,
    original: RenameTransaction,
    operations: Sequence[RenameOperation],
) -> RenameTransaction:
    try:
        return journal.get_transaction(original.transaction_id)
    except Exception:
        logger.exception(
            'Could not read transaction snapshot after undo transaction=%s',
            original.transaction_id,
        )
        return replace(original, operations=tuple(operations))


def _journal_available(journal: RenameJournal) -> bool:
    try:
        return journal.available
    except Exception:
        logger.exception('Could not inspect journal availability for undo')
        return False


def _undo_filesystem_error(exc: Exception, current: Path, original: Path) -> str:
    if isinstance(exc, _UndoPreconditionError):
        return str(exc)
    if isinstance(exc, FileExistsError):
        return f'原始目标已存在，未覆盖：{original}'
    if isinstance(exc, FileNotFoundError):
        return f'重命名后的目录不存在：{current}'
    if isinstance(exc, PermissionError):
        return f'没有权限撤销：{current}'
    if isinstance(exc, OSError):
        return f'文件系统错误：{exc}'
    return f'撤销异常：{exc}'


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)
