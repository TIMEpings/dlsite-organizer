"""Graphical settings editor backed by the application settings schema."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from PySide6.QtCore import Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from dlsite_organizer.app.settings import (
    AppSettings,
    SettingsError,
    SettingsService,
    StartupMode,
    default_logs_path,
)
from dlsite_organizer.domain.work import AgeCategory, Work, WorkLanguage
from dlsite_organizer.services.explorer_integration import (
    ExplorerIntegrationError,
    ExplorerIntegrationService,
    ExplorerRegistration,
    ExplorerRegistrationState,
)
from dlsite_organizer.services.naming import NamingService, NamingTemplateError


class SettingsPage(QWidget):
    """Edit settings in memory, preview safely, and save with one action."""

    settings_saved = Signal(object)

    _LOCALES = (
        ("日本語", "ja_jp"),
        ("English", "en_us"),
        ("简体中文", "zh_cn"),
        ("繁體中文", "zh_tw"),
        ("한국어", "ko_kr"),
    )
    _VARIABLES = (
        ("RJ编号", "{rjcode}"),
        ("标题", "{title}"),
        ("社团", "{maker_name}"),
        ("社团ID", "{maker_id}"),
        ("系列", "{series}"),
        ("CV", "{cv}"),
        ("标签", "{tags}"),
        ("年龄", "{age}"),
        ("语言", "{language}"),
        ("发售日期", "{release_date}"),
    )

    def __init__(
        self,
        settings_service: SettingsService,
        parent: QWidget | None = None,
        *,
        explorer_integration_service: ExplorerIntegrationService | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings_service = settings_service
        self._explorer_integration_service = (
            explorer_integration_service or ExplorerIntegrationService()
        )
        settings_service.subscribe(self._on_service_settings_changed)
        self._build_ui()
        self._load_settings(settings_service.settings)
        self.refresh_explorer_registration()

    def _build_ui(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(36, 28, 36, 28)
        page_layout.setSpacing(12)

        heading = QLabel("设置")
        heading.setObjectName("pageTitle")
        description = QLabel("普通配置使用此页面；TOML 仍可作为持久化和高级手工入口。")
        description.setObjectName("pageDescription")
        description.setWordWrap(True)
        page_layout.addWidget(heading)
        page_layout.addWidget(description)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 4, 8, 4)
        content_layout.setSpacing(12)

        naming_box = QGroupBox("重命名")
        naming_layout = QVBoxLayout(naming_box)
        naming_form = QFormLayout()
        naming_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.template_input = QLineEdit()
        self.template_input.setObjectName("namingTemplateInput")
        self.template_input.setPlaceholderText("例如：[{maker_name}][{rjcode}] {title}")
        self.template_input.setToolTip(
            "只支持简单的 {变量名} 占位符；[ ... ] 空段会在内容缺失时清理。"
        )
        naming_form.addRow("命名模板", self.template_input)
        self.date_format_input = QLineEdit()
        self.date_format_input.setObjectName("dateFormatInput")
        self.date_format_input.setToolTip("使用安全的 Python strftime 格式，例如 %Y-%m-%d。")
        naming_form.addRow("日期格式", self.date_format_input)
        self.illegal_replacement_input = QLineEdit()
        self.illegal_replacement_input.setObjectName("illegalReplacementInput")
        self.illegal_replacement_input.setMaxLength(1)
        self.illegal_replacement_input.setToolTip(
            "Windows 非法字符会被替换为此字符；保留保留名和尾随点/空格保护。"
        )
        naming_form.addRow("非法字符替换符", self.illegal_replacement_input)
        self.hide_general_age = QCheckBox("不显示“全年龄”")
        self.hide_general_age.setToolTip("启用后，{age} 在 general/全年龄作品中为空。")
        naming_form.addRow("年龄标签", self.hide_general_age)
        naming_layout.addLayout(naming_form)

        variables_label = QLabel("可用变量（点击插入）")
        variables_label.setObjectName("sectionLabel")
        naming_layout.addWidget(variables_label)
        variables_layout = QGridLayout()
        variables_layout.setSpacing(6)
        self.template_variable_buttons: dict[str, QPushButton] = {}
        for index, (label, variable) in enumerate(self._VARIABLES):
            button = QPushButton(f"{label} {variable}")
            button.setObjectName("templateVariableButton")
            button.setToolTip(f"插入 {variable}")
            button.clicked.connect(
                lambda _checked=False, value=variable: self._insert_variable(value)
            )
            variables_layout.addWidget(button, index // 5, index % 5)
            self.template_variable_buttons[variable] = button
        naming_layout.addLayout(variables_layout)

        self.template_error_label = QLabel()
        self.template_error_label.setObjectName("settingsErrorLabel")
        self.template_error_label.setWordWrap(True)
        naming_layout.addWidget(self.template_error_label)
        self.preview_label = QLabel()
        self.preview_label.setObjectName("settingsPreviewLabel")
        self.preview_label.setWordWrap(True)
        naming_layout.addWidget(self.preview_label)
        content_layout.addWidget(naming_box)

        format_box = QGroupBox("CV 与标签")
        format_form = QFormLayout(format_box)
        format_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.cv_separator_input = QLineEdit()
        self.cv_separator_input.setObjectName("cvSeparatorInput")
        format_form.addRow("CV 分隔符", self.cv_separator_input)
        self.cv_prefix_input = QLineEdit()
        self.cv_prefix_input.setObjectName("cvPrefixInput")
        format_form.addRow("CV 前缀", self.cv_prefix_input)
        self.cv_suffix_input = QLineEdit()
        self.cv_suffix_input.setObjectName("cvSuffixInput")
        format_form.addRow("CV 后缀", self.cv_suffix_input)
        self.tag_separator_input = QLineEdit()
        self.tag_separator_input.setObjectName("tagSeparatorInput")
        format_form.addRow("标签分隔符", self.tag_separator_input)
        self.max_tags_input = QSpinBox()
        self.max_tags_input.setObjectName("maxTagsInput")
        self.max_tags_input.setRange(0, 100)
        self.max_tags_input.setSpecialValueText("不限")
        self.max_tags_input.setToolTip("0 表示不限制标签数量；顺序保持 DLsite 原始顺序。")
        format_form.addRow("最多标签数", self.max_tags_input)
        content_layout.addWidget(format_box)

        metadata_box = QGroupBox("元数据")
        metadata_form = QFormLayout(metadata_box)
        metadata_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.metadata_locale_input = QComboBox()
        self.metadata_locale_input.setObjectName("metadataLocaleInput")
        for label, code in self._LOCALES:
            self.metadata_locale_input.addItem(f"{label} ({code})", code)
        self.provider_section_input = QLineEdit()
        self.provider_section_input.setObjectName("providerSectionInput")
        self.provider_section_input.setToolTip("DLsite storefront section；默认 maniax。")
        self.base_url_input = QLineEdit()
        self.base_url_input.setObjectName("providerBaseUrlInput")
        self.base_url_input.setToolTip("DLsite base URL；不包含 section 路径。")
        self.cache_enabled = QCheckBox("启用 metadata cache")
        self.cache_enabled.setObjectName("cacheEnabledInput")
        self.cache_ttl_input = QDoubleSpinBox()
        self.cache_ttl_input.setObjectName("cacheTtlInput")
        self.cache_ttl_input.setRange(0.1, 8760.0)
        self.cache_ttl_input.setDecimals(1)
        self.cache_ttl_input.setSuffix(" 小时")
        self.cache_ttl_input.setToolTip(
            "已有 cache 不会按新 locale 静默重解释；下一次 live/force refresh 使用新 locale。"
        )
        self.allow_stale_input = QCheckBox("网络错误时允许使用旧 cache")
        self.timeout_input = QDoubleSpinBox()
        self.timeout_input.setObjectName("timeoutInput")
        self.timeout_input.setRange(3.0, 120.0)
        self.timeout_input.setDecimals(1)
        self.timeout_input.setSuffix(" 秒")
        self.timeout_input.setToolTip("网络请求超时，推荐范围 3–120 秒。")
        metadata_form.addRow("元数据语言", self.metadata_locale_input)
        metadata_form.addRow("DLsite section", self.provider_section_input)
        metadata_form.addRow("DLsite base URL", self.base_url_input)
        metadata_form.addRow("缓存", self.cache_enabled)
        metadata_form.addRow("缓存 TTL", self.cache_ttl_input)
        metadata_form.addRow("缓存回退", self.allow_stale_input)
        metadata_form.addRow("网络超时", self.timeout_input)
        content_layout.addWidget(metadata_box)

        runtime_box = QGroupBox("启动与模式")
        runtime_form = QFormLayout(runtime_box)
        self.startup_mode_input = QComboBox()
        self.startup_mode_input.setObjectName("startupModeInput")
        self.startup_mode_input.addItem("完整模式", StartupMode.FULL.value)
        self.startup_mode_input.addItem("轻量模式", StartupMode.LIGHTWEIGHT.value)
        self.startup_mode_input.setToolTip("只控制下次启动时显示的窗口；临时切换模式不会修改此设置。")
        runtime_form.addRow("启动模式", self.startup_mode_input)
        content_layout.addWidget(runtime_box)

        explorer_box = QGroupBox("资源管理器集成")
        explorer_layout = QVBoxLayout(explorer_box)
        explorer_form = QFormLayout()
        self.explorer_status_label = QLabel()
        self.explorer_status_label.setObjectName("explorerRegistrationStatus")
        self.explorer_status_label.setWordWrap(True)
        explorer_form.addRow("右键菜单", self.explorer_status_label)
        self.explorer_path_label = QLabel()
        self.explorer_path_label.setObjectName("explorerRegistrationPath")
        self.explorer_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.explorer_path_label.setWordWrap(True)
        explorer_form.addRow("路径", self.explorer_path_label)
        explorer_layout.addLayout(explorer_form)

        explorer_help = QLabel(
            "注册仅对当前 Windows 用户生效，不需要管理员权限。"
            "根据 Windows 版本和 Explorer 行为，该命令可能出现在“显示更多选项”菜单中。"
        )
        explorer_help.setObjectName("pageDescription")
        explorer_help.setWordWrap(True)
        explorer_layout.addWidget(explorer_help)

        explorer_actions = QHBoxLayout()
        self.register_explorer_button = QPushButton("注册 / 更新")
        self.register_explorer_button.setObjectName("registerExplorerButton")
        self.remove_explorer_button = QPushButton("移除")
        self.remove_explorer_button.setObjectName("removeExplorerButton")
        explorer_actions.addWidget(self.register_explorer_button)
        explorer_actions.addWidget(self.remove_explorer_button)
        explorer_actions.addStretch(1)
        explorer_layout.addLayout(explorer_actions)
        self.explorer_feedback_label = QLabel()
        self.explorer_feedback_label.setObjectName("explorerFeedbackLabel")
        self.explorer_feedback_label.setWordWrap(True)
        explorer_layout.addWidget(self.explorer_feedback_label)
        content_layout.addWidget(explorer_box)

        data_box = QGroupBox("数据与诊断")
        data_layout = QFormLayout(data_box)
        data_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        config_row, self.config_path_label, self.open_config_button = self._path_row(
            self._settings_service.config_path, "打开所在文件夹"
        )
        database_row, self.database_path_label, self.open_database_button = self._path_row(
            self._settings_service.settings.database_path, "打开所在文件夹"
        )
        logs_row, self.logs_path_label, self.open_logs_button = self._path_row(
            default_logs_path(), "打开日志文件夹"
        )
        data_layout.addRow("配置文件", config_row)
        data_layout.addRow("SQLite 数据库", database_row)
        data_layout.addRow("日志", logs_row)
        content_layout.addWidget(data_box)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        page_layout.addWidget(scroll, 1)

        action_row = QHBoxLayout()
        self.save_button = QPushButton("保存设置")
        self.save_button.setObjectName("primaryButton")
        self.save_button.setMinimumSize(112, 38)
        self.reset_button = QPushButton("恢复默认设置")
        self.reset_button.setMinimumSize(124, 38)
        self.settings_status_label = QLabel()
        self.settings_status_label.setObjectName("statusLabel")
        self.settings_status_label.setWordWrap(True)
        action_row.addWidget(self.save_button)
        action_row.addWidget(self.reset_button)
        action_row.addWidget(self.settings_status_label, 1)
        page_layout.addLayout(action_row)

        for widget in (
            self.template_input,
            self.date_format_input,
            self.illegal_replacement_input,
            self.cv_separator_input,
            self.cv_prefix_input,
            self.cv_suffix_input,
            self.tag_separator_input,
        ):
            widget.textChanged.connect(self._refresh_preview)
        self.max_tags_input.valueChanged.connect(self._refresh_preview)
        self.hide_general_age.stateChanged.connect(self._refresh_preview)
        self.save_button.clicked.connect(self.save_settings)
        self.reset_button.clicked.connect(self.reset_defaults)
        self.register_explorer_button.clicked.connect(self.register_explorer_integration)
        self.remove_explorer_button.clicked.connect(self.remove_explorer_integration)

    def _path_row(
        self, path: Path, button_text: str
    ) -> tuple[QWidget, QLabel, QPushButton]:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        path_label = QLabel(str(path))
        path_label.setObjectName("pathLabel")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_label.setWordWrap(True)
        button = QPushButton(button_text)
        button.setObjectName("openPathButton")
        button.clicked.connect(lambda _checked=False, value=path: self._open_path(value))
        row_layout.addWidget(path_label, 1)
        row_layout.addWidget(button)
        return row, path_label, button

    def _load_settings(self, settings: AppSettings) -> None:
        self.template_input.setText(settings.naming_template)
        self.date_format_input.setText(settings.date_format)
        self.illegal_replacement_input.setText(settings.illegal_char_replacement)
        self.hide_general_age.setChecked(settings.hide_general_age)
        self.cv_separator_input.setText(settings.cv_separator)
        self.cv_prefix_input.setText(settings.cv_prefix)
        self.cv_suffix_input.setText(settings.cv_suffix)
        self.tag_separator_input.setText(settings.tag_separator)
        self.max_tags_input.setValue(settings.max_tags)
        self.provider_section_input.setText(settings.provider.section)
        self.base_url_input.setText(settings.provider.base_url)
        self.metadata_locale_input.setCurrentIndex(
            max(0, self.metadata_locale_input.findData(settings.provider.metadata_locale))
        )
        self.cache_enabled.setChecked(settings.cache.enabled)
        self.cache_ttl_input.setValue(settings.cache.ttl_hours)
        self.allow_stale_input.setChecked(settings.cache.allow_stale_on_error)
        self.timeout_input.setValue(settings.provider.timeout_seconds)
        self.startup_mode_input.setCurrentIndex(
            max(0, self.startup_mode_input.findData(settings.startup_mode.value))
        )
        self.database_path_label.setText(str(settings.database_path))
        self._refresh_preview()

    def _on_service_settings_changed(self, settings: AppSettings) -> None:
        """Keep the editor synchronized when another caller saves settings."""
        self._load_settings(settings)

    def refresh_explorer_registration(self) -> ExplorerRegistration:
        """Refresh the UI from the real registry state, never from TOML."""
        registration = self._explorer_integration_service.inspect()
        self._render_explorer_registration(registration)
        return registration

    @Slot()
    def register_explorer_integration(self) -> None:
        try:
            registration = self._explorer_integration_service.register_current_executable()
        except ExplorerIntegrationError as exc:
            self.explorer_feedback_label.setProperty("state", "error")
            self.explorer_feedback_label.setText(exc.user_message)
            self._refresh_status_style(self.explorer_feedback_label)
            self.refresh_explorer_registration()
            return
        self._render_explorer_registration(registration)
        if registration.state is ExplorerRegistrationState.REGISTERED_CURRENT:
            self.explorer_feedback_label.setProperty("state", "success")
            self.explorer_feedback_label.setText("资源管理器右键菜单已注册到当前程序路径。")
        elif registration.state is ExplorerRegistrationState.UNSUPPORTED:
            self.explorer_feedback_label.setProperty("state", "error")
            self.explorer_feedback_label.setText("资源管理器右键菜单只能在打包版本中注册。")
        self._refresh_status_style(self.explorer_feedback_label)

    @Slot()
    def remove_explorer_integration(self) -> None:
        try:
            registration = self._explorer_integration_service.unregister()
        except ExplorerIntegrationError:
            self.explorer_feedback_label.setProperty("state", "error")
            self.explorer_feedback_label.setText("无法移除资源管理器右键菜单。")
            self._refresh_status_style(self.explorer_feedback_label)
            self.refresh_explorer_registration()
            return
        self._render_explorer_registration(registration)
        if registration.state is ExplorerRegistrationState.NOT_REGISTERED:
            self.explorer_feedback_label.setProperty("state", "success")
            self.explorer_feedback_label.setText("资源管理器右键菜单已移除。")
        elif registration.state is ExplorerRegistrationState.UNSUPPORTED:
            self.explorer_feedback_label.setProperty("state", "error")
            self.explorer_feedback_label.setText("资源管理器右键菜单只能在打包版本中移除。")
        self._refresh_status_style(self.explorer_feedback_label)

    def _render_explorer_registration(self, registration: ExplorerRegistration) -> None:
        labels = {
            ExplorerRegistrationState.NOT_REGISTERED: "未注册",
            ExplorerRegistrationState.REGISTERED_CURRENT: "已注册",
            ExplorerRegistrationState.REGISTERED_STALE: "路径已失效 / 需要更新",
            ExplorerRegistrationState.UNSUPPORTED: "不支持",
            ExplorerRegistrationState.ERROR: "读取失败",
        }
        self.explorer_status_label.setProperty(
            "state",
            "error"
            if registration.state in {
                ExplorerRegistrationState.REGISTERED_STALE,
                ExplorerRegistrationState.UNSUPPORTED,
                ExplorerRegistrationState.ERROR,
            }
            else "success"
            if registration.state is ExplorerRegistrationState.REGISTERED_CURRENT
            else "",
        )
        self.explorer_status_label.setText(f"状态：{labels[registration.state]}")

        path_lines: list[str] = []
        if registration.registered_executable is not None:
            path_lines.append(f"已注册路径：{registration.registered_executable}")
        if registration.current_executable is not None:
            path_lines.append(f"当前程序：{registration.current_executable}")
        if registration.state is ExplorerRegistrationState.UNSUPPORTED:
            path_lines.append("资源管理器右键菜单只能在打包版本中注册。")
        if registration.error:
            path_lines.append(registration.error)
        self.explorer_path_label.setText("\n".join(path_lines) or "—")
        supported = registration.state is not ExplorerRegistrationState.UNSUPPORTED
        self.register_explorer_button.setEnabled(supported)
        self.remove_explorer_button.setEnabled(supported)
        self._refresh_status_style(self.explorer_status_label)

    def _insert_variable(self, variable: str) -> None:
        self.template_input.insert(variable)
        self.template_input.setFocus()

    def _collect_values(self) -> dict[str, Any]:
        current = self._settings_service.settings.model_dump()
        current.update(
            {
                "naming_template": self.template_input.text(),
                "cv_separator": self.cv_separator_input.text(),
                "cv_prefix": self.cv_prefix_input.text(),
                "cv_suffix": self.cv_suffix_input.text(),
                "tag_separator": self.tag_separator_input.text(),
                "max_tags": self.max_tags_input.value(),
                "hide_general_age": self.hide_general_age.isChecked(),
                "date_format": self.date_format_input.text(),
                "illegal_char_replacement": self.illegal_replacement_input.text(),
                "startup_mode": self.startup_mode_input.currentData(),
            }
        )
        current["provider"] = {
            **current["provider"],
            "section": self.provider_section_input.text(),
            "base_url": self.base_url_input.text(),
            "timeout_seconds": self.timeout_input.value(),
            "metadata_locale": self.metadata_locale_input.currentData(),
        }
        current["cache"] = {
            **current["cache"],
            "enabled": self.cache_enabled.isChecked(),
            "ttl_hours": self.cache_ttl_input.value(),
            "allow_stale_on_error": self.allow_stale_input.isChecked(),
        }
        return current

    def _settings_from_widgets(self) -> AppSettings:
        return AppSettings.model_validate(self._collect_values())

    def _renderer_from_widgets(self) -> NamingService:
        settings = self._settings_from_widgets()
        return NamingService(
            settings.naming_template,
            cv_separator=settings.cv_separator,
            cv_prefix=settings.cv_prefix,
            cv_suffix=settings.cv_suffix,
            tag_separator=settings.tag_separator,
            max_tags=settings.max_tags,
            hide_general_age=settings.hide_general_age,
            date_format=settings.date_format,
            illegal_char_replacement=settings.illegal_char_replacement,
        )

    def _refresh_preview(self, *_args: object) -> None:
        try:
            renderer = self._renderer_from_widgets()
            sample = Work(
                workno="RJ01234567",
                title="Example Work",
                maker_id="RG00001",
                maker_name="Example Circle",
                series_name="Example Series",
                cvs=["Voice A", "Voice B"],
                tags=["ASMR", "Binaural"],
                age_category=AgeCategory.R18,
                language=WorkLanguage.JPN,
                release_date=date(2026, 8, 31),
            )
            self.template_error_label.clear()
            self.preview_label.setText(f"实时预览（内置示例）：{renderer.format(sample)}")
            self.save_button.setEnabled(True)
        except (NamingTemplateError, ValidationError, ValueError) as exc:
            message = _friendly_validation_error(exc)
            self.template_error_label.setText(f"模板无效：{message}")
            self.preview_label.setText("实时预览不可用。请修正后再保存。")
            self.save_button.setEnabled(False)

    @Slot()
    def save_settings(self) -> None:
        try:
            settings = self._settings_from_widgets()
            saved = self._settings_service.save(settings)
        except (ValidationError, SettingsError, ValueError, OSError) as exc:
            self.settings_status_label.setProperty("state", "error")
            self.settings_status_label.setText(f"保存失败：{_friendly_validation_error(exc)}")
            self._refresh_status_style()
            return
        self.settings_status_label.setProperty("state", "success")
        self.settings_status_label.setText("设置已保存；命名设置立即用于下一次预览或轻量模式操作。")
        self._refresh_status_style()
        self.settings_saved.emit(saved)

    @Slot()
    def reset_defaults(self) -> None:
        answer = QMessageBox.question(
            self,
            "恢复默认设置",
            "将默认值载入当前页面，尚不会写入 config.toml。确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._load_settings(self._settings_service.defaults())
        self.settings_status_label.setProperty("state", "success")
        self.settings_status_label.setText("默认值已载入当前页面；点击“保存设置”后才会写入。")
        self._refresh_status_style()

    @Slot()
    def _open_path(self, path: Path) -> None:
        target = path if path.is_dir() else path.parent
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            self.settings_status_label.setProperty("state", "error")
            self.settings_status_label.setText(f"无法打开路径：{target}")
            self._refresh_status_style()

    def _refresh_status_style(self, label: QLabel | None = None) -> None:
        target = label or self.settings_status_label
        target.style().unpolish(target)
        target.style().polish(target)


def _friendly_validation_error(error: Exception) -> str:
    if isinstance(error, NamingTemplateError):
        return str(error)
    if isinstance(error, ValidationError):
        first = error.errors()[0] if error.errors() else None
        if first and first.get("msg"):
            return str(first["msg"])
    return str(error) or "输入值无效。"
