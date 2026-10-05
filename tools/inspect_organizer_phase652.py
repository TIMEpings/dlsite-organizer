"""Native offline append UAT: drag sandbox sources, remove B with Delete, re-add B."""

from __future__ import annotations

import json
import platform
from pathlib import Path
from uuid import uuid4

from inspect_organizer_phase61 import OfflineLookup
from PySide6.QtCore import QMimeData, Qt, QTimer, QUrl
from PySide6.QtWidgets import QApplication, QLabel, QListWidget, QVBoxLayout, QWidget

from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage
from dlsite_organizer.ui.theme import apply_light_theme


class DirectorySources(QListWidget):
    """Real native Qt drags carrying local directory URLs, like Explorer."""

    def mimeData(self, items):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(item.data(Qt.ItemDataRole.UserRole)) for item in items])
        return mime


def main():
    output = Path.cwd() / "research" / f"phase652-native-{uuid4().hex[:8]}"
    root = output / "library"
    root.mkdir(parents=True)
    paths = [root / name for name in ("A RJ000001", "B RJ000001", "Valid RJ000002")]
    for path in paths:
        path.mkdir()
    app = QApplication([])
    assert app.platformName() == "windows"
    apply_light_theme(app)
    database = Database(output / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    service = OrganizerService(OfflineLookup())
    page = OrganizerPage(service, execution_service=RenameExecutor(journal),
                         undo_service=UndoService(journal))
    window = QWidget()
    window.setWindowTitle("DLsite Organizer — Phase 6.5.2 offline sandbox")
    layout = QVBoxLayout(window)
    layout.addWidget(QLabel("Drag A, then B into preview. Select B / Delete. Drag B again. "
                            "Valid is an independent executable sandbox work."))
    sources = DirectorySources()
    sources.setDragEnabled(True)
    sources.setDefaultDropAction(Qt.DropAction.CopyAction)
    sources.setFixedHeight(90)
    for path in paths:
        sources.addItem(path.name)
        sources.item(sources.count() - 1).setData(Qt.ItemDataRole.UserRole, str(path))
    layout.addWidget(sources)
    layout.addWidget(page)
    window.resize(1200, 1000)
    window.show()
    evidence = {"platform": platform.platform(), "qt_platform": app.platformName(),
                "sandbox": str(output), "states": []}
    last = None

    def capture():
        nonlocal last
        if page.is_busy():
            return
        state = {"rows": [{"source": p.source_path.name, "status": str(p.status),
                           "checked": page.state.checked(i),
                           "label": page.preview_model.status_label(i)}
                          for i, p in enumerate(page.state.plans)],
                 "summary": page.summary_label.text(), "execute": page.execute_button.text(),
                 "current": page.state.current_key, "stale": page.state.stale,
                 "feedback": page.status_label.text(),
                 "execution": str(page.state.last_execution_result.status)
                 if page.state.last_execution_result else None}
        if state == last:
            return
        last = state.copy()
        screen = window.screen()
        evidence.update(dpr=window.devicePixelRatioF(), logical_dpi=screen.logicalDotsPerInch(),
                        windows_scaling_percent=window.devicePixelRatioF() * 100)
        number = len(evidence["states"])
        state["image"] = str(output / f"state-{number:02}.png")
        window.grab().save(state["image"])
        evidence["states"].append(state)
        (output / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2),
                                            encoding="utf-8")

    timer = QTimer(window)
    timer.timeout.connect(capture)
    timer.start(200)
    print(json.dumps({"sandbox": str(output), "platform": platform.platform()}, indent=2),
          flush=True)
    app.exec()
    database.dispose()


if __name__ == "__main__":
    main()
