"""System-locale mapping for the provider's supported metadata locales."""

from __future__ import annotations

from PySide6.QtCore import QLocale

SUPPORTED_METADATA_LOCALES = ("ja_jp", "en_us", "zh_cn", "zh_tw", "ko_kr")
_TRADITIONAL_CHINESE_TERRITORIES = frozenset({"hk", "mo", "tw"})


def system_locale() -> QLocale:
    """Return Qt's packaged-app system locale as the single detection seam."""
    return QLocale.system()


def metadata_locale_for(system: QLocale | str) -> str:
    """Map a Qt locale to one of the provider's verified canonical locales.

    Chinese locales are classified from Qt's script first, then from the
    territory as a defensive fallback for synthetic or older locale data.
    Unsupported languages intentionally use the existing Japanese fallback.
    """
    locale = QLocale(system) if isinstance(system, str) else system
    language = locale.language()
    script = locale.script()

    if language == QLocale.Language.Japanese:
        return "ja_jp"
    if language == QLocale.Language.English:
        return "en_us"
    if language == QLocale.Language.Korean:
        return "ko_kr"
    if language == QLocale.Language.Chinese:
        if script == QLocale.Script.TraditionalHanScript:
            return "zh_tw"
        if script == QLocale.Script.SimplifiedHanScript:
            return "zh_cn"
        territory = locale.name().rsplit("_", 1)[-1].casefold()
        if territory in _TRADITIONAL_CHINESE_TERRITORIES:
            return "zh_tw"
        return "zh_cn"
    return "ja_jp"


def default_metadata_locale() -> str:
    """Return the locale used for fresh settings and missing config fields."""
    return metadata_locale_for(system_locale())
