from pathlib import Path
from typing import cast

import pytest
from PySide6.QtWidgets import QApplication, QTableWidgetItem

from dlsite_organizer.domain.work import Work
from dlsite_organizer.services.lookup import LookupResult
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage


class FakeLookupService:
    def lookup(self, raw_workno: str) -> LookupResult:
        work = Work(workno=raw_workno, title="Preview Title", maker_name="Circle")
        return LookupResult(work=work, formatted_name=f"[Circle][{raw_workno}] Preview Title")


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def test_organizer_page_initializes_and_renders_preview(qapp: QApplication, tmp_path: Path) -> None:
    source = tmp_path / "old RJ01609020"
    source.mkdir()
    before = sorted(path.name for path in tmp_path.iterdir())
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    page.set_root_path(tmp_path)

    preview = OrganizerService(FakeLookupService()).preview(tmp_path)
    page.set_preview(preview)

    assert page.root_input.text() == str(tmp_path)
    assert page.table.rowCount() == 1
    status_item = cast(QTableWidgetItem, page.table.item(0, 0))
    current_item = cast(QTableWidgetItem, page.table.item(0, 1))
    code_item = cast(QTableWidgetItem, page.table.item(0, 2))
    proposed_item = cast(QTableWidgetItem, page.table.item(0, 5))
    assert status_item.text() == "READY"
    assert current_item.text() == "old RJ01609020"
    assert code_item.text() == "RJ01609020"
    assert proposed_item.text() == "[Circle][RJ01609020] Preview Title"
    assert "Ready 1" in page.summary_label.text()
    assert page.status_label.text() == "预览生成完成；未修改本地文件。"
    assert sorted(path.name for path in tmp_path.iterdir()) == before
    page.close()
