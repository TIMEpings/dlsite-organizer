import json
from datetime import UTC, datetime
from pathlib import Path

from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.metadata_store import MetadataStore
from dlsite_organizer.providers.dlsite.sources import TranslationInfoSource


def test_translation_observation_payload_preserves_all_normalized_flags_and_references(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "metadata.sqlite3")
    database.initialize()
    store = MetadataStore(database)
    work = Work(workno="RJ01636949", title="翻译 Parent", maker_id="RG60289")
    translation = TranslationInfoSource.model_validate(
        {
            "is_translation_agree": True,
            "is_volunteer": False,
            "is_original": False,
            "is_parent": True,
            "is_child": False,
            "is_translation_bonus_child": False,
            "original_workno": "RJ01609020",
            "parent_workno": None,
            "child_worknos": ["RJ01637033", "RJ01636950", "RJ01663275"],
            "lang": "中文简体 🌏",
        }
    )

    store.append_observation(
        work,
        translation,
        source="DLSITE_PRODUCT_INFO_AJAX",
        observed_at=datetime(2026, 8, 30, 10, 0, tzinfo=UTC),
        regist_datetime=datetime(2026, 7, 7, 12, 34, 56, tzinfo=UTC),
    )

    observation = store.list_observations("RJ01636949")[0]
    assert observation.translation_json is not None
    restored = TranslationInfoSource.model_validate(json.loads(observation.translation_json))
    assert restored.is_original is False
    assert restored.is_parent is True
    assert restored.is_child is False
    assert restored.original_workno == "RJ01609020"
    assert restored.parent_workno is None
    assert restored.child_worknos == ["RJ01637033", "RJ01636950", "RJ01663275"]
    assert restored.lang == "中文简体 🌏"
    assert restored.is_translation_bonus_child is False
    assert observation.source == "DLSITE_PRODUCT_INFO_AJAX"
    assert observation.regist_datetime == datetime(2026, 7, 7, 12, 34, 56, tzinfo=UTC)
    database.dispose()
