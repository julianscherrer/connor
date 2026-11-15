"""Settings: which AI model, how creative, internet on/off and system behaviour."""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from .. import system, voice
from ..llm import RECOMMENDED_MODEL, OllamaClient, OllamaError
from ..paths import data_dir, models_dir
from ..prompts import MODES
from ..settings import Settings

log = logging.getLogger(__name__)

THINKING = [("auto", "Automatisch"), ("off", "Aus (schneller)"), ("on", "An (gründlicher, langsamer)")]
PROVIDERS = [("auto", "Automatisch (DuckDuckGo, sonst Bing/Wikipedia)"), ("ddg", "Nur DuckDuckGo"),
             ("bing", "Nur Bing"), ("searxng", "SearXNG (selbst gehostet)")]
KEEP_ALIVE = [("5m", "5 Minuten"), ("30m", "30 Minuten (Empfehlung)"), ("2h", "2 Stunden"), ("12h", "12 Stunden")]
VOICE_DEVICES = [("auto", "Automatisch (Grafikkarte, falls möglich)"), ("cpu", "Nur Prozessor (CPU)"),
                 ("cuda", "Grafikkarte (NVIDIA)")]


def _select(combo: QComboBox, options: list[tuple[str, str]], value: str) -> None:
    for key, label in options:
        combo.addItem(label, key)
    keys = [key for key, _label in options]
    combo.setCurrentIndex(keys.index(value) if value in keys else 0)


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("small")
    label.setWordWrap(True)
    return label


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Einstellungen")
        self.resize(640, 720)

        content = QWidget()
        column = QVBoxLayout(content)
        column.setSpacing(6)

        # --- model
        model_box = QGroupBox("🧠  KI-Modell (Ollama, lokal)")
        model_form = QFormLayout(model_box)
        model_form.setSpacing(8)
        url_row = QHBoxLayout()
        self.url = QLineEdit(settings.ollama_url)
        url_row.addWidget(self.url, 1)
        test_button = QPushButton("Verbindung testen")
        test_button.clicked.connect(self.test_connection)
        url_row.addWidget(test_button)
        model_form.addRow("Ollama-Adresse", url_row)
        model_row = QHBoxLayout()
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.lineEdit().setPlaceholderText(f"automatisch (z. B. {RECOMMENDED_MODEL})")
        if settings.model:
            self.model.addItem(settings.model)
        self.model.setCurrentText(settings.model)
        model_row.addWidget(self.model, 1)
        refresh = QPushButton("↻")
        refresh.setToolTip("Installierte Modelle laden")
        refresh.clicked.connect(self.test_connection)
        model_row.addWidget(refresh)
        model_form.addRow("Modell", model_row)
        self.connection_label = _hint("")
        self.connection_label.hide()
        model_form.addRow("", self.connection_label)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setValue(settings.temperature)
        model_form.addRow("Kreativität ✍️", self.temperature)
        self.chat_temperature = QDoubleSpinBox()
        self.chat_temperature.setRange(0.0, 2.0)
        self.chat_temperature.setSingleStep(0.1)
        self.chat_temperature.setValue(settings.chat_temperature)
        model_form.addRow("Kreativität 💬", self.chat_temperature)
        model_form.addRow("", _hint("✍️ Schreibpartner / 💬 Allrounder: 0.3 = sachlich · 0.8 = kreativ · 1.2+ = wild"))
        self.default_mode = QComboBox()
        _select(self.default_mode, list(MODES.items()), settings.default_mode)
        model_form.addRow("Modus für neue Chats", self.default_mode)
        self.context = QSpinBox()
        self.context.setRange(2048, 262144)
        self.context.setSingleStep(2048)
        self.context.setValue(settings.context_size)
        model_form.addRow("Gedächtnis pro Chat", self.context)
        model_form.addRow("", _hint("Kontextgrösse in Tokens. Mehr = Connor überblickt längere Chats und Texte, "
                                    "braucht aber mehr Grafikspeicher. 8192 ist ein guter Start."))
        self.thinking = QComboBox()
        for key, label in THINKING:
            self.thinking.addItem(label, key)
        keys = [key for key, _label in THINKING]
        self.thinking.setCurrentIndex(keys.index(settings.thinking) if settings.thinking in keys else 0)
        model_form.addRow("Denkmodus", self.thinking)
        model_form.addRow("", _hint("Manche Modelle (z. B. qwen3) können vor der Antwort nachdenken. "
                                    "Gründlicher, aber deutlich langsamer."))
        self.keep_alive = QComboBox()
        _select(self.keep_alive, KEEP_ALIVE, settings.keep_alive)
        model_form.addRow("Modell bereithalten", self.keep_alive)
        model_form.addRow("", _hint("So lange bleibt das Modell nach der letzten Nachricht geladen. Länger = "
                                    "schnellere Antworten, belegt aber Grafikspeicher."))
        self.use_tools = QCheckBox("Connor darf selbst speichern, erinnern und suchen (Werkzeuge)")
        self.use_tools.setChecked(settings.use_tools)
        model_form.addRow("", self.use_tools)
        column.addWidget(model_box)

        # --- about you
        you_box = QGroupBox("🙋  Über dich")
        you_form = QFormLayout(you_box)
        self.user_name = QLineEdit(settings.user_name)
        self.user_name.setPlaceholderText("Wie soll Connor dich nennen?")
        you_form.addRow("Dein Name", self.user_name)
        self.extra = QPlainTextEdit(settings.extra_instructions)
        self.extra.setPlaceholderText("z. B. «Ich schreibe vor allem Fantasy» oder «Antworte gern auf Schweizerdeutsch»")
        self.extra.setFixedHeight(90)
        you_form.addRow("Extra-Wünsche", self.extra)
        column.addWidget(you_box)

        # --- internet
        web_box = QGroupBox("🌐  Internet")
        web_form = QFormLayout(web_box)
        self.web_enabled = QCheckBox("Websuche erlauben")
        self.web_enabled.setChecked(settings.web_enabled)
        web_form.addRow("", self.web_enabled)
        self.web_confirm = QCheckBox("Vor jeder Webanfrage nachfragen")
        self.web_confirm.setChecked(settings.web_confirm)
        web_form.addRow("", self.web_confirm)
        self.read_pages = QCheckBox("Die besten Treffer gleich lesen (genauere Antworten)")
        self.read_pages.setChecked(settings.web_read_pages)
        web_form.addRow("", self.read_pages)
        self.provider = QComboBox()
        _select(self.provider, PROVIDERS, settings.search_provider)
        web_form.addRow("Suchmaschine", self.provider)
        self.searxng = QLineEdit(settings.searxng_url)
        web_form.addRow("SearXNG-Adresse", self.searxng)
        web_form.addRow("", _hint("Bei einer Websuche gehen nur die Suchbegriffe ins Internet – "
                                  "deine Chats, Geschichten und Erinnerungen nie."))
        column.addWidget(web_box)

        # --- voice
        voice_box = QGroupBox("🎤  Spracherkennung (lokal)")
        voice_form = QFormLayout(voice_box)
        self.voice_model = QComboBox()
        _select(self.voice_model, voice.VOICE_MODELS, settings.voice_model)
        voice_form.addRow("Modell", self.voice_model)
        self.voice_language = QComboBox()
        _select(self.voice_language, voice.VOICE_LANGUAGES, settings.voice_language)
        voice_form.addRow("Sprache", self.voice_language)
        self.voice_device = QComboBox()
        _select(self.voice_device, VOICE_DEVICES, settings.voice_device)
        voice_form.addRow("Rechnet auf", self.voice_device)
        self.voice_autosend = QCheckBox("Im Chat nach dem Diktieren gleich senden")
        self.voice_autosend.setChecked(settings.voice_autosend)
        voice_form.addRow("", self.voice_autosend)
        missing = voice.missing_packages()
        state = ("✅ Installiert. " if not missing else "❌ " + voice.INSTALL_HINT + " ")
        voice_form.addRow("", _hint(state + "Deine Stimme bleibt auf dem PC – nur das Sprachmodell wird beim "
                                    f"ersten Diktieren einmalig heruntergeladen (nach {models_dir()})."))
        column.addWidget(voice_box)

        # --- system
        system_box = QGroupBox("🖥️  System")
        system_form = QFormLayout(system_box)
        self.tray = QCheckBox("Beim Schliessen im Hintergrund weiterlaufen (für Erinnerungen)")
        self.tray.setChecked(settings.minimize_to_tray)
        system_form.addRow("", self.tray)
        self.autostart = QCheckBox("Connor mit Windows starten (minimiert)")
        self.autostart.setVisible(system.autostart_supported())
        self.autostart.setChecked(system.autostart_enabled())
        system_form.addRow("", self.autostart)
        folder_row = QHBoxLayout()
        folder_label = QLabel(str(data_dir()))
        folder_label.setObjectName("small")
        folder_label.setWordWrap(True)
        folder_label.setToolTip(str(data_dir()))
        folder_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        folder_row.addWidget(folder_label, 1)
        open_button = QPushButton("Ordner öffnen")
        open_button.clicked.connect(lambda: system.open_folder(data_dir()))
        folder_row.addWidget(open_button)
        system_form.addRow("Deine Daten", folder_row)
        system_form.addRow("", _hint("Hier liegen Chats, Gedächtnis, Geschichten und Erinnerungen (connor.db). "
                                     "Für ein Backup einfach diesen Ordner kopieren."))
        column.addWidget(system_box)
        column.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Speichern")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)

    def test_connection(self) -> None:
        self.connection_label.show()
        client = OllamaClient(self.url.text())
        try:
            models = client.list_models(timeout=3)
        except OllamaError as exc:
            self.connection_label.setText(f"❌ {exc}")
            return
        current = self.model.currentText()
        self.model.clear()
        self.model.addItems(models)
        self.model.setCurrentText(current)
        if models:
            self.connection_label.setText(f"✅ Verbunden – {len(models)} Modell(e) installiert")
        else:
            self.connection_label.setText(f"⚠️ Verbunden, aber noch kein Modell installiert. "
                                          f"Im Terminal: ollama pull {RECOMMENDED_MODEL}")

    def apply(self) -> None:
        s = self.settings
        s.ollama_url = self.url.text().strip() or "http://localhost:11434"
        s.model = self.model.currentText().strip()
        s.temperature = round(self.temperature.value(), 2)
        s.chat_temperature = round(self.chat_temperature.value(), 2)
        s.default_mode = self.default_mode.currentData()
        s.context_size = self.context.value()
        s.thinking = self.thinking.currentData()
        s.keep_alive = self.keep_alive.currentData()
        s.use_tools = self.use_tools.isChecked()
        s.user_name = self.user_name.text().strip()
        s.extra_instructions = self.extra.toPlainText().strip()
        s.web_enabled = self.web_enabled.isChecked()
        s.web_confirm = self.web_confirm.isChecked()
        s.web_read_pages = self.read_pages.isChecked()
        s.search_provider = self.provider.currentData()
        s.searxng_url = self.searxng.text().strip()
        s.voice_model = self.voice_model.currentData()
        s.voice_language = self.voice_language.currentData()
        s.voice_device = self.voice_device.currentData()
        s.voice_autosend = self.voice_autosend.isChecked()
        s.minimize_to_tray = self.tray.isChecked()
        if system.autostart_supported() and self.autostart.isChecked() != system.autostart_enabled():
            try:
                system.set_autostart(self.autostart.isChecked())
            except OSError as exc:
                QMessageBox.warning(self, "Autostart", f"Autostart konnte nicht geändert werden: {exc}")
