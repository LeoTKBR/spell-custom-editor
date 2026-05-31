"""Janela principal: monta a UI e orquestra GraphicsCore via Worker."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QSettings, QUrl, QElapsedTimer, QPoint, QSize
from PySide6.QtGui import QAction, QFont, QPixmap, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStyle,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .core import GraphicsCore
from .helpers import pil_to_qpixmap, safe_text_value
from .theme import apply_theme
from .widgets.icon_sheet import IconSheetWidget
from .widgets.preview_grid import PreviewGrid
from .worker import Worker


class MainWindow(QMainWindow):
    log_signal = Signal(str)
    progress_signal = Signal(str, str, float)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Graphics Resources Manager (Qt)")
        self.resize(1320, 880)
        self.settings = QSettings("LeoTK", "SpellCustomEditor")

        self.core = GraphicsCore()
        self.core.log = self.log_signal.emit
        self.core.progress = self.progress_signal.emit
        self.log_signal.connect(self._append_log)
        self.progress_signal.connect(self._update_progress)

        self._workers: list[Worker] = []

        # Estado de UI
        self.spells_filtered_indices: list[int] = []
        self.previews_filtered_keys: list[str] = []
        self.selected_spell_id: int | None = None
        self.selected_preview_key: str | None = None
        self.selected_icon_index = -1
        self._anim_tick = 0
        self._render_sig = None
        self._pixmap_cache: dict[int, QPixmap] = {}
        self._preview_undo_stack: list[tuple[str, dict]] = []
        self._preview_redo_stack: list[tuple[str, dict]] = []
        self._grid_gesture_snapshot_taken = False
        self._grid_tool_mode = "Adicionar"
        self._asset_picker_entries: list[dict] = []
        self._asset_icon_last_tick = -1

        self._build_ui()
        self._load_ui_settings()
        self._set_loaded_actions_state(False)

        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._on_anim_tick)
        self._anim_elapsed = QElapsedTimer()
        self._anim_elapsed.start()
        self._anim_timer.start(33)

    # ===================================================================
    # Construcao da UI
    # ===================================================================
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        client_box = QGroupBox("1) Cliente")
        cb = QVBoxLayout(client_box)
        row = QHBoxLayout()
        row.addWidget(QLabel("Pasta do cliente:"))
        self.client_dir_edit = QLineEdit()
        row.addWidget(self.client_dir_edit, 1)
        btn_sel = QPushButton("Selecionar")
        btn_sel.clicked.connect(self.select_client_dir)
        row.addWidget(btn_sel)
        self.btn_load = QPushButton("Carregar Cliente")
        self.btn_load.clicked.connect(self.on_load_client)
        row.addWidget(self.btn_load)
        cb.addLayout(row)
        prow = QHBoxLayout()
        self.step_label = QLabel("Etapa: -")
        prow.addWidget(self.step_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        prow.addWidget(self.progress_bar, 1)
        self.status_label = QLabel("Aguardando selecao da pasta do cliente.")
        self.status_label.setWordWrap(True)
        prow.addWidget(self.status_label)
        cb.addLayout(prow)
        root.addWidget(client_box)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_icon_tab(), "Icon Editor")
        self.tabs.addTab(self._build_spells_tab(), "Spells Editor")
        self.tabs.addTab(self._build_previews_tab(), "Spells Preview Editor")
        root.addWidget(self.tabs, 1)

        log_box = QGroupBox("Log")
        lb = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setMinimumHeight(120)
        lb.addWidget(self.log_view)
        self.log_box = log_box
        self.log_box.setVisible(False)
        root.addWidget(log_box)

        self._build_menu_and_toolbar()

    def _build_menu_and_toolbar(self) -> None:
        menubar = self.menuBar()
        build_menu = menubar.addMenu("Build")
        self.act_compile = QAction("Compilar e Instalar", self)
        self.act_compile.triggered.connect(self.on_compile_install)
        build_menu.addAction(self.act_compile)
        self.act_backup = QAction("Backup Manual", self)
        self.act_backup.triggered.connect(self.on_manual_backup)
        build_menu.addAction(self.act_backup)

        options_menu = menubar.addMenu("Opções")
        self.act_toggle_log = QAction("Mostrar Log", self, checkable=True, checked=False)
        self.act_toggle_log.toggled.connect(self._on_toggle_log)
        options_menu.addAction(self.act_toggle_log)

        discord_menu = menubar.addMenu("Discord")
        act_discord_canary = QAction("Canary", self)
        act_discord_canary.triggered.connect(lambda: self._open_url("https://discord.gg/gvTj5sh9Mp"))
        discord_menu.addAction(act_discord_canary)
        act_discord_tk = QAction("TK Dev Core", self)
        act_discord_tk.triggered.connect(lambda: self._open_url("https://discord.gg/rj97H4JD3k"))
        discord_menu.addAction(act_discord_tk)

        credits_menu = menubar.addMenu("Créditos")
        act_show_credits = QAction("Ver Créditos", self)
        act_show_credits.triggered.connect(self._show_credits)
        credits_menu.addAction(act_show_credits)

        tb = self.addToolBar("Acoes")
        tb.addAction(self.act_compile)
        tb.addAction(self.act_backup)

    def _load_ui_settings(self) -> None:
        last_client_dir = str(self.settings.value("client/last_dir", "", str))
        if last_client_dir:
            self.client_dir_edit.setText(last_client_dir)
        apply_theme(QApplication.instance(), True)

    def _save_ui_settings(self) -> None:
        self.settings.setValue("client/last_dir", self.client_dir_edit.text().strip())
        self.settings.sync()

    def _open_url(self, url: str) -> None:
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(self, "Atencao", f"Nao foi possivel abrir o link:\n{url}")

    def _show_credits(self) -> None:
        QMessageBox.information(
            self,
            "Créditos",
            "Créditos:\n\n"
            "LeoTK\n"
            "Beats\n"
            "RedSTwix\n"
            "Pedrin",
        )

    def _on_toggle_log(self, visible: bool) -> None:
        self.log_box.setVisible(visible)

    # ---- aba de icones --------------------------------------------------
    def _build_icon_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)

        importbox = QGroupBox("Importar / Substituir")
        ib = QHBoxLayout(importbox)
        ib.addWidget(QLabel("PNG:"))
        self.icon_path_edit = QLineEdit()
        ib.addWidget(self.icon_path_edit, 1)
        btn_pick = QPushButton("Selecionar PNG")
        btn_pick.clicked.connect(self.select_icon_png)
        ib.addWidget(btn_pick)
        ib.addWidget(QLabel("Index (opcional):"))
        self.icon_index_edit = QLineEdit()
        self.icon_index_edit.setFixedWidth(70)
        ib.addWidget(self.icon_index_edit)
        self.btn_add_icon = QPushButton("Adicionar/Substituir")
        self.btn_add_icon.clicked.connect(self.on_add_icon)
        ib.addWidget(self.btn_add_icon)
        self.btn_remove_icon = QPushButton("Remover")
        self.btn_remove_icon.clicked.connect(self.on_remove_icon)
        ib.addWidget(self.btn_remove_icon)
        lay.addWidget(importbox)

        toprow = QHBoxLayout()
        self.icon_selected_label = QLabel("Indice selecionado: -")
        toprow.addWidget(self.icon_selected_label)
        toprow.addStretch(1)
        self.btn_icon_create = QPushButton("Criar indice custom")
        self.btn_icon_create.clicked.connect(self.on_create_custom_icon)
        toprow.addWidget(self.btn_icon_create)
        self.btn_icon_import_sel = QPushButton("Importar PNG no indice")
        self.btn_icon_import_sel.clicked.connect(self.on_import_icon_selected)
        toprow.addWidget(self.btn_icon_import_sel)
        self.btn_icon_clear = QPushButton("Limpar indice")
        self.btn_icon_clear.clicked.connect(self.on_clear_icon_selected)
        toprow.addWidget(self.btn_icon_clear)
        lay.addLayout(toprow)

        self.icon_sheet = IconSheetWidget()
        self.icon_sheet.selected.connect(self.on_icon_selected)
        self.icon_sheet.reorder.connect(self.on_icon_reorder)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.icon_sheet)
        lay.addWidget(scroll, 1)

        prev = QHBoxLayout()
        prev.addWidget(QLabel("Preview 32x32:"))
        self.icon_preview_32 = QLabel()
        self.icon_preview_32.setFixedSize(34, 34)
        self.icon_preview_32.setFrameShape(QFrame.Shape.Box)
        prev.addWidget(self.icon_preview_32)
        prev.addSpacing(20)
        prev.addWidget(QLabel("Preview 20x20:"))
        self.icon_preview_20 = QLabel()
        self.icon_preview_20.setFixedSize(22, 22)
        self.icon_preview_20.setFrameShape(QFrame.Shape.Box)
        prev.addWidget(self.icon_preview_20)
        prev.addStretch(1)
        lay.addLayout(prev)
        return tab

    # ---- aba de spells --------------------------------------------------
    def _build_spells_tab(self) -> QWidget:
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("Buscar por id/nome/formula"))
        self.spells_search = QLineEdit()
        self.spells_search.textChanged.connect(self.refresh_spells_list)
        ll.addWidget(self.spells_search)
        self.spells_list = QListWidget()
        self.spells_list.currentRowChanged.connect(self.on_select_spell)
        ll.addWidget(self.spells_list, 1)
        brow = QHBoxLayout()
        self.btn_spell_new = QPushButton("Novo")
        self.btn_spell_new.clicked.connect(self.new_spell)
        self.btn_spell_dup = QPushButton("Duplicar")
        self.btn_spell_dup.clicked.connect(self.duplicate_spell)
        self.btn_spell_del = QPushButton("Deletar")
        self.btn_spell_del.clicked.connect(self.delete_spell)
        for b in (self.btn_spell_new, self.btn_spell_dup, self.btn_spell_del):
            brow.addWidget(b)
        ll.addLayout(brow)
        left.setMinimumWidth(260)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.spell_fields = {
            "spellid": QLineEdit(),
            "name": QLineEdit(),
            "formulaWithoutParams": QLineEdit(),
            "iconIndex": QLineEdit(),
            "minimumCasterLevel": QLineEdit(),
            "goldPrice": QLineEdit(),
            "allowedVocations": QLineEdit(),
        }
        form.addRow("spellid", self.spell_fields["spellid"])
        form.addRow("name", self.spell_fields["name"])
        form.addRow("formula", self.spell_fields["formulaWithoutParams"])
        form.addRow("iconIndex", self.spell_fields["iconIndex"])
        form.addRow("minimumCasterLevel", self.spell_fields["minimumCasterLevel"])
        form.addRow("goldPrice", self.spell_fields["goldPrice"])
        form.addRow("allowedVocations (,)", self.spell_fields["allowedVocations"])
        rl.addLayout(form)
        crow = QHBoxLayout()
        self.spell_premium = QCheckBox("premium")
        self.spell_aggressive = QCheckBox("aggressive")
        self.spell_isrune = QCheckBox("isRune")
        for c in (self.spell_premium, self.spell_aggressive, self.spell_isrune):
            crow.addWidget(c)
        crow.addStretch(1)
        rl.addLayout(crow)
        text_split = QSplitter(Qt.Orientation.Horizontal)
        text_split.setChildrenCollapsible(False)

        description_box = QGroupBox("description")
        dl = QVBoxLayout(description_box)
        self.spell_description = QTextEdit()
        self.spell_description.setMinimumHeight(220)
        dl.addWidget(self.spell_description)
        text_split.addWidget(description_box)

        extra_box = QGroupBox("Campos extras (JSON opcional)")
        el = QVBoxLayout(extra_box)
        self.spell_extra = QTextEdit()
        self.spell_extra.setMinimumHeight(220)
        el.addWidget(self.spell_extra)
        text_split.addWidget(extra_box)

        text_split.setStretchFactor(0, 1)
        text_split.setStretchFactor(1, 1)
        text_split.setSizes([520, 520])
        rl.addWidget(text_split, 1)
        srow = QHBoxLayout()
        self.btn_spell_save = QPushButton("Salvar Registro")
        self.btn_spell_save.clicked.connect(self.save_spell_record)
        self.btn_spells_save_all = QPushButton("Salvar Arquivo")
        self.btn_spells_save_all.clicked.connect(self.on_save_spells_file)
        srow.addWidget(self.btn_spell_save)
        srow.addWidget(self.btn_spells_save_all)
        srow.addStretch(1)
        rl.addLayout(srow)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setWidget(right)
        split.addWidget(right_scroll)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([360, 900])

        wrap = QWidget()
        QVBoxLayout(wrap).addWidget(split)
        return wrap

    # ---- aba de previews ------------------------------------------------
    def _build_previews_tab(self) -> QWidget:
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("Buscar por id/nome"))
        self.previews_search = QLineEdit()
        self.previews_search.textChanged.connect(self.refresh_previews_list)
        ll.addWidget(self.previews_search)
        self.previews_list = QListWidget()
        self.previews_list.currentRowChanged.connect(self.on_select_preview)
        ll.addWidget(self.previews_list, 1)
        brow = QHBoxLayout()
        self.btn_preview_new = QPushButton("Novo")
        self.btn_preview_new.clicked.connect(self.new_preview)
        self.btn_preview_dup = QPushButton("Duplicar")
        self.btn_preview_dup.clicked.connect(self.duplicate_preview)
        self.btn_preview_del = QPushButton("Deletar")
        self.btn_preview_del.clicked.connect(self.delete_preview)
        for b in (self.btn_preview_new, self.btn_preview_dup, self.btn_preview_del):
            brow.addWidget(b)
        ll.addLayout(brow)
        left.setMinimumWidth(260)
        split.addWidget(left)

        right = QTabWidget()
        right.addTab(self._wrap_scroll_widget(self._build_preview_structural()), "Estrutural")
        right.addTab(self._wrap_scroll_widget(self._build_preview_grid_tab()), "Grid FX/Missiles")
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([360, 900])

        wrap = QWidget()
        QVBoxLayout(wrap).addWidget(split)
        return wrap

    def _wrap_scroll_widget(self, content: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll

    def _build_preview_structural(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.preview_fields = {"spellid": QLineEdit(), "name": QLineEdit(), "range": QLineEdit()}
        form.addRow("spellid", self.preview_fields["spellid"])
        form.addRow("name", self.preview_fields["name"])
        form.addRow("range", self.preview_fields["range"])
        lay.addLayout(form)

        editors = QSplitter(Qt.Orientation.Horizontal)
        editors.setChildrenCollapsible(False)
        ts_box = QGroupBox("Timestamps")
        tsl = QVBoxLayout(ts_box)
        self.timestamps_list = QListWidget()
        self.timestamps_list.currentRowChanged.connect(self.on_select_timestamp)
        self.timestamps_list.setMinimumHeight(120)
        tsl.addWidget(self.timestamps_list)
        tsb = QHBoxLayout()
        b1 = QPushButton("Adicionar")
        b1.clicked.connect(self.add_timestamp)
        b2 = QPushButton("Remover")
        b2.clicked.connect(self.remove_timestamp)
        tsb.addWidget(b1)
        tsb.addWidget(b2)
        tsl.addLayout(tsb)
        editors.addWidget(ts_box)

        act_box = QGroupBox("Actions do Timestamp")
        al = QVBoxLayout(act_box)
        self.actions_list = QListWidget()
        self.actions_list.currentRowChanged.connect(self.on_select_action)
        self.actions_list.setMinimumHeight(120)
        al.addWidget(self.actions_list)
        af = QHBoxLayout()
        self.action_type = QComboBox()
        self.action_type.addItems(["fieldEffect", "missile", "objecttype", "target"])
        self.action_id = QLineEdit()
        self.action_id.setMinimumWidth(90)
        self.action_x = QLineEdit("0")
        self.action_x.setMinimumWidth(56)
        self.action_y = QLineEdit("0")
        self.action_y.setMinimumWidth(56)
        af.addWidget(QLabel("action"))
        af.addWidget(self.action_type)
        af.addWidget(QLabel("id"))
        af.addWidget(self.action_id)
        af.addWidget(QLabel("x"))
        af.addWidget(self.action_x)
        af.addWidget(QLabel("y"))
        af.addWidget(self.action_y)
        af.addStretch(1)
        al.addLayout(af)
        ab = QHBoxLayout()
        for label, slot in (("Adicionar", self.add_action), ("Atualizar", self.update_action), ("Remover", self.remove_action)):
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            ab.addWidget(btn)
        ab.addStretch(1)
        al.addLayout(ab)
        editors.addWidget(act_box)

        init_box = QGroupBox("InitActions")
        il = QVBoxLayout(init_box)
        self.init_actions_list = QListWidget()
        self.init_actions_list.currentRowChanged.connect(self.on_select_init_action)
        self.init_actions_list.setMinimumHeight(120)
        il.addWidget(self.init_actions_list)
        iform = QHBoxLayout()
        self.init_action_type = QComboBox()
        self.init_action_type.addItems(["target", "fieldEffect"])
        self.init_action_x = QLineEdit("0")
        self.init_action_x.setMinimumWidth(56)
        self.init_action_y = QLineEdit("0")
        self.init_action_y.setMinimumWidth(56)
        iform.addWidget(QLabel("action"))
        iform.addWidget(self.init_action_type)
        iform.addWidget(QLabel("x"))
        iform.addWidget(self.init_action_x)
        iform.addWidget(QLabel("y"))
        iform.addWidget(self.init_action_y)
        iform.addStretch(1)
        il.addLayout(iform)
        ib = QHBoxLayout()
        for label, slot in (("Adicionar", self.add_init_action), ("Atualizar", self.update_init_action), ("Remover", self.remove_init_action)):
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            ib.addWidget(btn)
        il.addLayout(ib)
        editors.addWidget(init_box)
        editors.setStretchFactor(0, 1)
        editors.setStretchFactor(1, 1)
        editors.setStretchFactor(2, 1)
        editors.setSizes([340, 460, 340])
        lay.addWidget(editors, 1)

        srow = QHBoxLayout()
        self.btn_preview_save = QPushButton("Salvar Registro")
        self.btn_preview_save.clicked.connect(self.save_preview_record)
        self.btn_previews_save_all = QPushButton("Salvar Arquivo")
        self.btn_previews_save_all.clicked.connect(self.on_save_previews_file)
        srow.addWidget(self.btn_preview_save)
        srow.addWidget(self.btn_previews_save_all)
        srow.addStretch(1)
        lay.addLayout(srow)
        return tab

    def _build_preview_grid_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)

        grid_box = QGroupBox("Preview Visual 30x14 (responsivo)")
        gl = QVBoxLayout(grid_box)
        row = QHBoxLayout()
        left_panel = QVBoxLayout()
        left_panel.addWidget(QLabel("Tipo de lista"))
        self.asset_kind_combo = QComboBox()
        self.asset_kind_combo.addItems(["Effect", "Missile", "Object"])
        self.asset_kind_combo.currentIndexChanged.connect(self._refresh_asset_picker_list)
        left_panel.addWidget(self.asset_kind_combo)
        self.asset_search = QLineEdit()
        self.asset_search.setPlaceholderText("Buscar por id/nome...")
        self.asset_search.textChanged.connect(self._refresh_asset_picker_list)
        left_panel.addWidget(self.asset_search)
        self.asset_list = QListWidget()
        self.asset_list.currentRowChanged.connect(self._on_asset_picker_selected)
        self.asset_list.setMinimumWidth(280)
        self.asset_list.setMaximumWidth(360)
        left_panel.addWidget(self.asset_list, 1)
        self.btn_update_field_json = QPushButton("Atualizar JSON Fields")
        self.btn_update_field_json.clicked.connect(self.on_update_field_objects_json)
        left_panel.addWidget(self.btn_update_field_json)
        left_info = QLabel("Clique esquerdo: adiciona no grid\nCtrl + clique esquerdo: remove")
        left_info.setWordWrap(True)
        left_panel.addWidget(left_info)
        left_w = QWidget()
        left_w.setLayout(left_panel)
        row.addWidget(left_w, 0)

        right_panel = QVBoxLayout()
        tools = QHBoxLayout()
        self.btn_mode_target = self._make_grid_tool_button("Definir Target", "SP_CommandLink", "Definir Target")
        self.btn_mode_target.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        tools.addWidget(self.btn_mode_target)
        self.btn_mode_add = self._make_grid_tool_button("Adicionar", "SP_DialogYesButton", "Adicionar")
        self.btn_mode_add.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        tools.addWidget(self.btn_mode_add)
        self.btn_grid_undo = QToolButton()
        self.btn_grid_undo.setText("Desfazer")
        self.btn_grid_undo.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        self.btn_grid_undo.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.btn_grid_undo.clicked.connect(self._undo_preview_edit)
        tools.addWidget(self.btn_grid_undo)
        self.btn_grid_redo = QToolButton()
        self.btn_grid_redo.setText("Refazer")
        self.btn_grid_redo.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowForward))
        self.btn_grid_redo.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.btn_grid_redo.clicked.connect(self._redo_preview_edit)
        tools.addWidget(self.btn_grid_redo)
        tools.addSpacing(14)
        self.render_objecttype = QCheckBox("Renderizar objecttype")
        self.render_objecttype.setChecked(True)
        self.render_objecttype.stateChanged.connect(lambda *_: self.update_preview_grid(force=True))
        tools.addWidget(self.render_objecttype)
        tools.addStretch(1)
        right_panel.addLayout(tools)
        self.grid_mode_label = QLabel("Modo atual: Adicionar | Clique: adiciona | Ctrl+Clique: remove")
        right_panel.addWidget(self.grid_mode_label)
        self.preview_grid = PreviewGrid()
        self.preview_grid.cellClicked.connect(self.on_grid_click)
        self.preview_grid.cellDragged.connect(self.on_grid_drag)
        self.preview_grid.cellRightClicked.connect(self.on_grid_right_click)
        right_panel.addWidget(self.preview_grid, 1)
        right_w = QWidget()
        right_w.setLayout(right_panel)
        row.addWidget(right_w, 1)
        gl.addLayout(row, 1)
        lay.addWidget(grid_box, 1)
        self._set_grid_mode("Adicionar")
        return tab

    def _standard_icon_by_name(self, icon_name: str):
        enum_value = getattr(QStyle.StandardPixmap, icon_name, None)
        if enum_value is None:
            enum_value = QStyle.StandardPixmap.SP_FileIcon
        return self.style().standardIcon(enum_value)

    def _make_grid_tool_button(self, text: str, icon_name: str, mode: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setIcon(self._standard_icon_by_name(icon_name))
        btn.setCheckable(True)
        btn.clicked.connect(lambda *_: self._set_grid_mode(mode))
        return btn

    def _set_grid_mode(self, mode: str) -> None:
        self._grid_tool_mode = mode
        mapping = {
            "Adicionar": getattr(self, "btn_mode_add", None),
            "Definir Target": getattr(self, "btn_mode_target", None),
        }
        for k, b in mapping.items():
            if b is not None:
                b.setChecked(k == mode)
        if hasattr(self, "grid_mode_label"):
            self.grid_mode_label.setText(f"Modo atual: {mode} | Clique: adiciona | Ctrl+Clique: remove")

    # ===================================================================
    # Infra: log/progress/worker/estado
    # ===================================================================
    def _append_log(self, msg: str) -> None:
        self.log_view.appendPlainText(msg)

    def _update_progress(self, step: str, detail: str, value: float) -> None:
        self.step_label.setText(f"Etapa: {step}")
        self.status_label.setText(detail)
        self.progress_bar.setValue(int(value))

    def run_bg(self, fn, on_done=None) -> None:
        worker = Worker(fn)
        self._workers.append(worker)

        def cleanup():
            if worker in self._workers:
                self._workers.remove(worker)

        def done():
            cleanup()
            if on_done is not None:
                on_done()

        def err(msg):
            cleanup()
            self._set_loaded_actions_state(bool(self.core.spells_data) or self.core.icon_sheet_32 is not None)
            self.btn_load.setEnabled(True)
            QMessageBox.critical(self, "Erro", msg)
            self.status_label.setText(f"Falhou: {msg}")

        worker.ok.connect(done)
        worker.failed.connect(err)
        worker.start()

    def _set_loaded_actions_state(self, enabled: bool) -> None:
        for w in (
            self.btn_add_icon, self.btn_remove_icon, self.btn_icon_create,
            self.btn_icon_import_sel, self.btn_icon_clear,
            self.btn_spell_new, self.btn_spell_dup, self.btn_spell_del,
            self.btn_spell_save, self.btn_spells_save_all,
            self.btn_preview_new, self.btn_preview_dup, self.btn_preview_del,
            self.btn_preview_save, self.btn_previews_save_all,
            self.act_compile, self.act_backup,
        ):
            w.setEnabled(enabled)

    # ===================================================================
    # Cliente
    # ===================================================================
    def select_client_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Selecione a pasta do cliente")
        if folder:
            self.client_dir_edit.setText(folder)
            self._save_ui_settings()
            self._append_log(f"Pasta selecionada: {folder}")

    def on_load_client(self) -> None:
        folder = self.client_dir_edit.text().strip()
        if not folder:
            QMessageBox.warning(self, "Atencao", "Selecione a pasta do cliente antes de carregar.")
            return
        self.core.client_dir = folder
        self._set_loaded_actions_state(False)
        self.btn_load.setEnabled(False)

        def done():
            self.refresh_spells_list()
            self.refresh_previews_list()
            self.refresh_icon_tab()
            self._refresh_effect_missile_spin()
            self._set_loaded_actions_state(True)
            self.btn_load.setEnabled(True)
            self.update_preview_grid(force=True)

        self.run_bg(self.core.load_client, done)

    # ===================================================================
    # Icones
    # ===================================================================
    def select_icon_png(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Selecione o PNG", "", "PNG (*.png)")
        if path:
            self.icon_path_edit.setText(path)
            self._append_log(f"Icone selecionado: {path}")

    def _icon_index_or_none(self) -> int | None:
        raw = self.icon_index_edit.text().strip()
        if not raw:
            return None
        if not raw.lstrip("-").isdigit():
            raise RuntimeError("icon index precisa ser numero inteiro.")
        idx = int(raw)
        if idx < 0:
            raise RuntimeError("icon index nao pode ser negativo.")
        return idx

    def refresh_icon_tab(self) -> None:
        self.icon_sheet.set_sheet(self.core.icon_sheet_32)
        self.icon_sheet.selected_index = self.selected_icon_index
        self._update_icon_previews()

    def on_icon_selected(self, idx: int) -> None:
        self.selected_icon_index = idx
        self.icon_index_edit.setText(str(idx))
        self.icon_selected_label.setText(f"Indice selecionado: {idx}")
        self._update_icon_previews()

    def _update_icon_previews(self) -> None:
        idx = self.selected_icon_index
        for label, size in ((self.icon_preview_32, 32), (self.icon_preview_20, 20)):
            crop = self.core.icon_crop(idx, size) if idx >= 0 else None
            label.setPixmap(pil_to_qpixmap(crop) if crop is not None else QPixmap())

    def on_add_icon(self) -> None:
        icon_file = self.icon_path_edit.text().strip()
        if not icon_file:
            QMessageBox.warning(self, "Atencao", "Selecione um arquivo PNG.")
            return
        try:
            index = self._icon_index_or_none()
        except RuntimeError as exc:
            QMessageBox.critical(self, "Erro", str(exc))
            return

        def work():
            self._last_icon_idx = self.core.add_or_replace_icon(icon_file, index)

        def done():
            self.refresh_icon_tab()
            self._append_log(f"Icone aplicado no index {getattr(self, '_last_icon_idx', '?')}.")

        self.run_bg(work, done)

    def on_remove_icon(self) -> None:
        try:
            idx = self._icon_index_or_none()
        except RuntimeError as exc:
            QMessageBox.critical(self, "Erro", str(exc))
            return
        if idx is None:
            QMessageBox.warning(self, "Atencao", "Informe icon index para remover.")
            return
        self.run_bg(lambda: self.core.remove_icon(idx), self.refresh_icon_tab)

    def on_create_custom_icon(self) -> None:
        target, ok = QInputDialog.getInt(self, "Novo indice custom", "Informe o indice a criar:", 0, 0)
        if not ok:
            return

        def done():
            self.selected_icon_index = target
            self.icon_index_edit.setText(str(target))
            self.refresh_icon_tab()

        self.run_bg(lambda: self.core.ensure_icon_index_capacity(target), done)

    def on_import_icon_selected(self) -> None:
        if self.selected_icon_index < 0:
            QMessageBox.warning(self, "Atencao", "Selecione um indice no gerenciador visual.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Selecione o PNG", "", "PNG (*.png)")
        if not path:
            return
        idx = self.selected_icon_index
        self.run_bg(lambda: self.core.add_or_replace_icon(path, idx), self.refresh_icon_tab)

    def on_clear_icon_selected(self) -> None:
        if self.selected_icon_index < 0:
            QMessageBox.warning(self, "Atencao", "Selecione um indice no gerenciador visual.")
            return
        idx = self.selected_icon_index
        self.run_bg(lambda: self.core.remove_icon(idx), self.refresh_icon_tab)

    def on_icon_reorder(self, source: int, target: int) -> None:
        def done():
            self.selected_icon_index = target
            self.icon_index_edit.setText(str(target))
            self.refresh_icon_tab()

        self.run_bg(lambda: self.core.move_icon_index(source, target), done)

    # ===================================================================
    # Spells
    # ===================================================================
    def refresh_spells_list(self) -> None:
        self.spells_list.blockSignals(True)
        self.spells_list.clear()
        self.core.spells_data.sort(key=lambda x: int(x.get("spellid", 0)))
        self.spells_filtered_indices = []
        term = self.spells_search.text().strip().lower()
        for idx, item in enumerate(self.core.spells_data):
            sid_text = str(item.get("spellid", ""))
            name = safe_text_value(item.get("name", "(sem nome)"))
            formula = safe_text_value(item.get("formulaWithoutParams", ""))
            if term and term not in sid_text.lower() and term not in name.lower() and term not in formula.lower():
                continue
            self.spells_filtered_indices.append(idx)
            self.spells_list.addItem(f"{item.get('spellid', '?')} - {item.get('name', '(sem nome)')}")
        self.spells_list.blockSignals(False)

    def on_select_spell(self, ui_idx: int) -> None:
        if ui_idx < 0 or ui_idx >= len(self.spells_filtered_indices):
            return
        item = self.core.spells_data[self.spells_filtered_indices[ui_idx]]
        self.selected_spell_id = int(item["spellid"])
        self.spell_fields["spellid"].setText(str(item.get("spellid", "")))
        self.spell_fields["name"].setText(safe_text_value(item.get("name", "")))
        self.spell_fields["formulaWithoutParams"].setText(safe_text_value(item.get("formulaWithoutParams", "")))
        self.spell_fields["iconIndex"].setText(str(item.get("iconIndex", "")))
        self.spell_fields["minimumCasterLevel"].setText(str(item.get("minimumCasterLevel", "")))
        self.spell_fields["goldPrice"].setText(str(item.get("goldPrice", "")))
        self.spell_fields["allowedVocations"].setText(", ".join(safe_text_value(v) for v in item.get("allowedVocations", [])))
        self.spell_premium.setChecked(bool(item.get("premium", False)))
        self.spell_aggressive.setChecked(bool(item.get("aggressive", False)))
        self.spell_isrune.setChecked(bool(item.get("isRune", False)))
        self.spell_description.setPlainText(safe_text_value(item.get("description", "")))
        extras = {k: v for k, v in item.items() if k not in (
            "spellid", "name", "formulaWithoutParams", "iconIndex", "minimumCasterLevel",
            "goldPrice", "premium", "aggressive", "isRune", "allowedVocations", "description")}
        self.spell_extra.setPlainText(json.dumps(extras, ensure_ascii=False, indent=4))

    def new_spell(self) -> None:
        sid, ok = QInputDialog.getInt(self, "Novo spell", "spellid:", 1, 1)
        if not ok:
            return
        if any(int(x.get("spellid", -1)) == sid for x in self.core.spells_data):
            QMessageBox.critical(self, "Erro", f"spellid {sid} ja existe em spells.")
            return
        self.core.spells_data.append({"spellid": sid, "name": f"New Spell {sid}", "allowedVocations": []})
        self.refresh_spells_list()
        self._append_log(f"Novo registro spell criado: {sid}")

    def duplicate_spell(self) -> None:
        if self.selected_spell_id is None:
            QMessageBox.warning(self, "Atencao", "Selecione um registro de spell para duplicar.")
            return
        source = next((x for x in self.core.spells_data if int(x.get("spellid", -1)) == self.selected_spell_id), None)
        if source is None:
            return
        sid, ok = QInputDialog.getInt(self, "Duplicar spell", "Novo spellid:", 1, 1)
        if not ok:
            return
        if any(int(x.get("spellid", -1)) == sid for x in self.core.spells_data):
            QMessageBox.critical(self, "Erro", f"spellid {sid} ja existe em spells.")
            return
        clone = copy.deepcopy(source)
        clone["spellid"] = sid
        clone["name"] = f"{safe_text_value(clone.get('name', 'Spell'))} (Copy)"
        self.core.spells_data.append(clone)
        self.refresh_spells_list()
        self._append_log(f"Registro spell duplicado para {sid}")

    def delete_spell(self) -> None:
        if self.selected_spell_id is None:
            QMessageBox.warning(self, "Atencao", "Selecione um registro de spell para deletar.")
            return
        sid = self.selected_spell_id
        self.core.spells_data = [x for x in self.core.spells_data if int(x.get("spellid", -1)) != sid]
        self.selected_spell_id = None
        self.spell_description.clear()
        self.spell_extra.clear()
        self.refresh_spells_list()
        self._append_log(f"Registro spell removido: {sid}")

    def save_spell_record(self) -> None:
        if self.selected_spell_id is None:
            QMessageBox.warning(self, "Atencao", "Selecione um registro de spell para salvar.")
            return
        try:
            sid = int(self.spell_fields["spellid"].text().strip())
            icon = int(self.spell_fields["iconIndex"].text().strip() or 0)
            level = int(self.spell_fields["minimumCasterLevel"].text().strip() or 0)
            price = int(self.spell_fields["goldPrice"].text().strip() or 0)
        except ValueError:
            QMessageBox.critical(self, "Erro", "Campos numericos de spell invalidos.")
            return
        if sid <= 0:
            QMessageBox.critical(self, "Erro", "spellid precisa ser maior que zero.")
            return
        try:
            extras = json.loads(self.spell_extra.toPlainText().strip() or "{}")
        except json.JSONDecodeError as exc:
            QMessageBox.critical(self, "Erro", f"JSON invalido em campos extras: {exc.msg}")
            return
        if not isinstance(extras, dict):
            QMessageBox.critical(self, "Erro", "Campos extras precisam ser um objeto JSON.")
            return
        vocations = [x.strip() for x in self.spell_fields["allowedVocations"].text().split(",") if x.strip()]
        parsed = dict(extras)
        parsed.update({
            "spellid": sid,
            "name": self.spell_fields["name"].text().strip(),
            "formulaWithoutParams": self.spell_fields["formulaWithoutParams"].text().strip(),
            "iconIndex": icon,
            "minimumCasterLevel": level,
            "goldPrice": price,
            "premium": self.spell_premium.isChecked(),
            "aggressive": self.spell_aggressive.isChecked(),
            "isRune": self.spell_isrune.isChecked(),
            "allowedVocations": vocations,
            "description": self.spell_description.toPlainText().strip(),
        })
        for i, item in enumerate(self.core.spells_data):
            if int(item.get("spellid", -1)) == self.selected_spell_id:
                self.core.spells_data[i] = parsed
                break
        self.selected_spell_id = sid
        self.refresh_spells_list()
        self._append_log(f"Registro spell atualizado: {sid}")

    def on_save_spells_file(self) -> None:
        try:
            self.core.save_spells_file()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Erro", str(exc))

    # ===================================================================
    # Previews
    # ===================================================================
    def refresh_previews_list(self) -> None:
        self.previews_list.blockSignals(True)
        self.previews_list.clear()
        self.previews_filtered_keys = []
        term = self.previews_search.text().strip().lower()
        for key in sorted(self.core.previews_data.keys(), key=lambda x: int(x)):
            name = safe_text_value(self.core.previews_data[key].get("name", "(sem nome)"))
            if term and term not in key.lower() and term not in name.lower():
                continue
            self.previews_filtered_keys.append(key)
            self.previews_list.addItem(f"{key} - {self.core.previews_data[key].get('name', '(sem nome)')}")
        self.previews_list.blockSignals(False)

    def on_select_preview(self, ui_idx: int) -> None:
        if ui_idx < 0 or ui_idx >= len(self.previews_filtered_keys):
            return
        key = self.previews_filtered_keys[ui_idx]
        self.selected_preview_key = key
        rec = self.core.previews_data[key]
        self.preview_fields["spellid"].setText(str(rec.get("spellid", "")))
        self.preview_fields["name"].setText(rec.get("name", ""))
        self.preview_fields["range"].setText(str(rec.get("range", 0)))
        self.refresh_timestamps_list()
        self.refresh_init_actions_list()
        self.update_preview_grid(force=True)

    def new_preview(self) -> None:
        sid, ok = QInputDialog.getInt(self, "Novo preview", "spellid:", 1, 1)
        if not ok:
            return
        key = str(sid)
        if key in self.core.previews_data:
            QMessageBox.critical(self, "Erro", f"spellid {sid} ja existe em previews.")
            return
        self.core.previews_data[key] = {"spellid": sid, "range": 0, "name": f"New Preview {sid}", "timestamps": [], "initActions": []}
        self.refresh_previews_list()
        self._append_log(f"Novo registro preview criado: {sid}")

    def duplicate_preview(self) -> None:
        if self.selected_preview_key is None:
            QMessageBox.warning(self, "Atencao", "Selecione um registro de preview para duplicar.")
            return
        source = self.core.previews_data.get(self.selected_preview_key)
        if source is None:
            return
        sid, ok = QInputDialog.getInt(self, "Duplicar preview", "Novo spellid:", 1, 1)
        if not ok:
            return
        key = str(sid)
        if key in self.core.previews_data:
            QMessageBox.critical(self, "Erro", f"spellid {sid} ja existe em previews.")
            return
        clone = copy.deepcopy(source)
        clone["spellid"] = sid
        clone["name"] = f"{safe_text_value(clone.get('name', 'Preview'))} (Copy)"
        self.core.previews_data[key] = clone
        self.refresh_previews_list()
        self._append_log(f"Registro preview duplicado para {sid}")

    def delete_preview(self) -> None:
        if self.selected_preview_key is None:
            QMessageBox.warning(self, "Atencao", "Selecione um registro de preview para deletar.")
            return
        key = self.selected_preview_key
        self.core.previews_data.pop(key, None)
        self.selected_preview_key = None
        self.timestamps_list.clear()
        self.actions_list.clear()
        self.init_actions_list.clear()
        self.refresh_previews_list()
        self.update_preview_grid(force=True)
        self._append_log(f"Registro preview removido: {key}")

    def save_preview_record(self) -> None:
        if self.selected_preview_key is None:
            QMessageBox.warning(self, "Atencao", "Selecione um registro de preview para salvar.")
            return
        record = self.core.previews_data[self.selected_preview_key]
        try:
            sid = int(self.preview_fields["spellid"].text().strip())
            rng = int(self.preview_fields["range"].text().strip() or 0)
        except ValueError:
            QMessageBox.critical(self, "Erro", "Campos numericos de preview invalidos.")
            return
        parsed = dict(record)
        parsed["spellid"] = sid
        parsed["name"] = self.preview_fields["name"].text().strip()
        parsed["range"] = rng
        new_key = str(sid)
        if new_key != self.selected_preview_key and new_key in self.core.previews_data:
            QMessageBox.critical(self, "Erro", f"Ja existe outro preview com spellid {new_key}.")
            return
        old_key = self.selected_preview_key
        if new_key != old_key:
            self.core.previews_data.pop(old_key, None)
        self.core.previews_data[new_key] = parsed
        self.selected_preview_key = new_key
        self.refresh_previews_list()
        self._append_log(f"Registro preview atualizado: {new_key}")

    def on_save_previews_file(self) -> None:
        try:
            self.core.save_previews_file()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Erro", str(exc))

    # ---- timestamps / actions ------------------------------------------
    def _current_preview(self) -> dict | None:
        if self.selected_preview_key is None:
            return None
        return self.core.previews_data.get(self.selected_preview_key)

    def refresh_timestamps_list(self) -> None:
        self.timestamps_list.blockSignals(True)
        self.timestamps_list.clear()
        rec = self._current_preview()
        if rec is not None:
            for i, ts in enumerate(rec.get("timestamps", [])):
                self.timestamps_list.addItem(f"#{i} t={ts.get('timestamp', '?')} ({len(ts.get('actions', []))} acoes)")
        self.timestamps_list.blockSignals(False)
        if self.timestamps_list.count() > 0:
            self.timestamps_list.setCurrentRow(0)
        else:
            self.refresh_actions_list()

    def get_selected_timestamp(self) -> dict | None:
        rec = self._current_preview()
        if rec is None:
            return None
        timestamps = rec.get("timestamps", [])
        idx = self.timestamps_list.currentRow()
        if idx < 0 or idx >= len(timestamps):
            return None
        return timestamps[idx]

    def add_timestamp(self) -> None:
        rec = self._current_preview()
        if rec is None:
            QMessageBox.warning(self, "Atencao", "Selecione um preview.")
            return
        t, ok = QInputDialog.getInt(self, "Timestamp", "Valor do timestamp (ms):", 0, 0)
        if not ok:
            return
        rec.setdefault("timestamps", []).append({"timestamp": t, "actions": []})
        self.refresh_timestamps_list()
        self.timestamps_list.setCurrentRow(len(rec["timestamps"]) - 1)
        self.update_preview_grid(force=True)

    def remove_timestamp(self) -> None:
        rec = self._current_preview()
        if rec is None:
            return
        idx = self.timestamps_list.currentRow()
        timestamps = rec.get("timestamps", [])
        if idx < 0 or idx >= len(timestamps):
            QMessageBox.warning(self, "Atencao", "Selecione um timestamp para remover.")
            return
        timestamps.pop(idx)
        self.refresh_timestamps_list()
        self.refresh_actions_list()
        self.update_preview_grid(force=True)

    def on_select_timestamp(self, _row: int) -> None:
        self.refresh_actions_list()
        self.update_preview_grid(force=True)

    def refresh_actions_list(self) -> None:
        self.actions_list.blockSignals(True)
        self.actions_list.clear()
        ts = self.get_selected_timestamp()
        if ts is not None:
            for i, action in enumerate(ts.get("actions", [])):
                aid = action.get("effectID", action.get("missileID", action.get("objecttypeID", "")))
                self.actions_list.addItem(f"#{i} {action.get('action', '?')} id={aid} ({action.get('x', 0)},{action.get('y', 0)})")
        self.actions_list.blockSignals(False)

    def _build_action_from_form(self) -> dict:
        action_name = self.action_type.currentText().strip()
        id_raw = self.action_id.text().strip()
        data = {"action": action_name, "x": int(self.action_x.text().strip() or 0), "y": int(self.action_y.text().strip() or 0)}
        if action_name == "fieldEffect" and id_raw:
            data["effectID"] = int(id_raw)
        elif action_name == "missile" and id_raw:
            data["missileID"] = int(id_raw)
        elif action_name == "objecttype" and id_raw:
            data["objecttypeID"] = int(id_raw)
        return data

    def add_action(self) -> None:
        ts = self.get_selected_timestamp()
        if ts is None:
            QMessageBox.warning(self, "Atencao", "Selecione um timestamp antes de adicionar acao.")
            return
        ts.setdefault("actions", []).append(self._build_action_from_form())
        self.refresh_actions_list()
        self.update_preview_grid(force=True)

    def update_action(self) -> None:
        ts = self.get_selected_timestamp()
        if ts is None:
            return
        sel = self.actions_list.currentRow()
        if sel < 0:
            QMessageBox.warning(self, "Atencao", "Selecione uma acao para atualizar.")
            return
        ts["actions"][sel] = self._build_action_from_form()
        self.refresh_actions_list()
        self.update_preview_grid(force=True)

    def remove_action(self) -> None:
        ts = self.get_selected_timestamp()
        if ts is None:
            return
        sel = self.actions_list.currentRow()
        if sel < 0:
            QMessageBox.warning(self, "Atencao", "Selecione uma acao para remover.")
            return
        ts["actions"].pop(sel)
        self.refresh_actions_list()
        self.update_preview_grid(force=True)

    def on_select_action(self, sel: int) -> None:
        ts = self.get_selected_timestamp()
        if ts is None or sel < 0 or sel >= len(ts.get("actions", [])):
            return
        action = ts["actions"][sel]
        self.action_type.setCurrentText(action.get("action", "fieldEffect"))
        aid = action.get("effectID", action.get("missileID", action.get("objecttypeID", "")))
        self.action_id.setText(str(aid))
        self.action_x.setText(str(action.get("x", 0)))
        self.action_y.setText(str(action.get("y", 0)))

    # ---- init actions ---------------------------------------------------
    def refresh_init_actions_list(self) -> None:
        self.init_actions_list.blockSignals(True)
        self.init_actions_list.clear()
        rec = self._current_preview()
        if rec is not None:
            for i, action in enumerate(rec.get("initActions", [])):
                self.init_actions_list.addItem(f"#{i} {action.get('action', '?')} ({action.get('x', 0)},{action.get('y', 0)})")
        self.init_actions_list.blockSignals(False)

    def _build_init_action_from_form(self) -> dict:
        return {
            "action": self.init_action_type.currentText().strip(),
            "x": int(self.init_action_x.text().strip() or 0),
            "y": int(self.init_action_y.text().strip() or 0),
        }

    def add_init_action(self) -> None:
        rec = self._current_preview()
        if rec is None:
            QMessageBox.warning(self, "Atencao", "Selecione um preview antes de adicionar initAction.")
            return
        rec.setdefault("initActions", []).append(self._build_init_action_from_form())
        self.refresh_init_actions_list()
        self.update_preview_grid(force=True)

    def update_init_action(self) -> None:
        rec = self._current_preview()
        if rec is None:
            return
        sel = self.init_actions_list.currentRow()
        if sel < 0:
            QMessageBox.warning(self, "Atencao", "Selecione uma initAction para atualizar.")
            return
        rec["initActions"][sel] = self._build_init_action_from_form()
        self.refresh_init_actions_list()
        self.update_preview_grid(force=True)

    def remove_init_action(self) -> None:
        rec = self._current_preview()
        if rec is None:
            return
        sel = self.init_actions_list.currentRow()
        if sel < 0:
            QMessageBox.warning(self, "Atencao", "Selecione uma initAction para remover.")
            return
        rec["initActions"].pop(sel)
        self.refresh_init_actions_list()
        self.update_preview_grid(force=True)

    def on_select_init_action(self, sel: int) -> None:
        rec = self._current_preview()
        if rec is None or sel < 0 or sel >= len(rec.get("initActions", [])):
            return
        action = rec["initActions"][sel]
        self.init_action_type.setCurrentText(action.get("action", "target"))
        self.init_action_x.setText(str(action.get("x", 0)))
        self.init_action_y.setText(str(action.get("y", 0)))

    # ===================================================================
    # Grid de preview
    # ===================================================================
    def _refresh_effect_missile_spin(self) -> None:
        self.core.load_field_objects_json()
        self._refresh_asset_picker_entries()
        self._refresh_asset_picker_list()

    def on_update_field_objects_json(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Selecione o diretorio que contem o items.xml")
        if not folder:
            return
        items_xml = Path(folder) / "items.xml"
        if not items_xml.exists():
            QMessageBox.warning(self, "Atencao", f"Nao encontrei items.xml em:\n{folder}")
            return

        def work():
            self._last_field_count = self.core.generate_field_objects_json_from_items_xml(items_xml)

        def done():
            self.core.load_field_objects_json()
            self._refresh_asset_picker_entries()
            self._refresh_asset_picker_list()
            self.update_preview_grid(force=True)
            QMessageBox.information(
                self,
                "JSON Fields",
                f"JSON atualizado com {getattr(self, '_last_field_count', 0)} objetos field.",
            )

        self.run_bg(work, done)

    def _refresh_asset_picker_entries(self) -> None:
        self._asset_picker_entries = []
        for e in self.core.effects_catalog:
            self._asset_picker_entries.append({"kind": "Effect", "id": int(e.get("id", 0)), "name": safe_text_value(e.get("name", ""))})
        for m in self.core.missiles_catalog:
            self._asset_picker_entries.append({"kind": "Missile", "id": int(m.get("id", 0)), "name": safe_text_value(m.get("name", ""))})
        for obj in self.core.object_entries():
            self._asset_picker_entries.append({"kind": "Object", "id": int(obj.get("id", 0)), "name": safe_text_value(obj.get("name", ""))})

    def _refresh_asset_picker_list(self) -> None:
        if not hasattr(self, "asset_list"):
            return
        kind = self.asset_kind_combo.currentText().strip() if hasattr(self, "asset_kind_combo") else "Effect"
        term = self.asset_search.text().strip().lower() if hasattr(self, "asset_search") else ""
        self.asset_list.blockSignals(True)
        self.asset_list.clear()
        self.asset_list.setIconSize(QSize(28, 28))
        for entry in self._asset_picker_entries:
            if entry["kind"] != kind:
                continue
            if entry["kind"] == "Effect":
                label = f"{entry['id']}"
            elif entry["kind"] == "Missile":
                label = f"{entry['id']}"
            else:
                label = f"{entry['id']} - {entry['name'] or '(sem nome)'}"
            if term and term not in label.lower():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            pix = self._asset_list_preview_pixmap(entry, 0)
            if pix is not None:
                item.setIcon(QIcon(pix))
            self.asset_list.addItem(item)
        self.asset_list.blockSignals(False)
        if self.asset_list.count() > 0:
            self.asset_list.setCurrentRow(0)
        self._update_asset_picker_icons(force=True)

    def _asset_list_preview_pixmap(self, entry: dict, elapsed_ms: int) -> QPixmap | None:
        kind = safe_text_value(entry.get("kind", ""))
        sid = int(entry.get("id", 0) or 0)
        pil_img = None
        if kind == "Effect":
            cat = self.core.effect_by_id(sid)
            pil_img = self.core.sprite_for_catalog_entry(cat, elapsed_ms, 0, 0)
        elif kind == "Missile":
            cat = self.core.missile_by_id(sid)
            pil_img = self.core.sprite_for_catalog_entry(cat, 0, 2, 1)
        elif kind == "Object":
            cat = self.core.object_by_id(sid)
            pil_img = self.core.sprite_for_catalog_entry(cat, elapsed_ms, 0, 0)
        if pil_img is None:
            return None
        return pil_to_qpixmap(pil_img)

    def _update_asset_picker_icons(self, force: bool = False) -> None:
        if not hasattr(self, "asset_list") or self.asset_list.count() == 0:
            return
        tick = self._anim_tick // 3
        if not force and tick == self._asset_icon_last_tick:
            return
        self._asset_icon_last_tick = tick
        elapsed_ms = int(self._anim_elapsed.elapsed()) if self._anim_elapsed.isValid() else (self._anim_tick * 33)
        for i in range(self.asset_list.count()):
            item = self.asset_list.item(i)
            if item is None:
                continue
            entry = item.data(Qt.ItemDataRole.UserRole) or {}
            pix = self._asset_list_preview_pixmap(entry, elapsed_ms)
            if pix is not None:
                item.setIcon(QIcon(pix))

    def _on_asset_picker_selected(self, row: int) -> None:
        if row < 0:
            return
        item = self.asset_list.item(row)
        if item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole) or {}
        kind = safe_text_value(entry.get("kind", ""))
        sid = entry.get("id", 0)
        if kind == "Effect":
            self.action_type.setCurrentText("fieldEffect")
            self.action_id.setText(str(int(sid)))
        elif kind == "Missile":
            self.action_type.setCurrentText("missile")
            self.action_id.setText(str(int(sid)))
        elif kind == "Object":
            self.action_type.setCurrentText("objecttype")
            self.action_id.setText(str(int(sid)))

    def on_grid_click(self, gx: int, gy: int, ctrl: bool) -> None:
        self._grid_gesture_snapshot_taken = False
        self._on_grid_edit(gx, gy, dragged=False, ctrl=ctrl)

    def on_grid_drag(self, gx: int, gy: int, ctrl: bool) -> None:
        self._on_grid_edit(gx, gy, dragged=True, ctrl=ctrl)

    def on_grid_right_click(self, gx: int, gy: int, global_pos: QPoint) -> None:
        menu = QMenu(self)
        act_select_here = menu.addAction("Selecionar ação aqui")
        act_add_effect_here = menu.addAction("Adicionar Effect aqui")
        act_add_missile_here = menu.addAction("Adicionar Missile aqui")
        act_add_object_here = menu.addAction("Adicionar Object aqui")
        act_add_current_here = menu.addAction("Adicionar ação atual aqui")
        act_remove_here = menu.addAction("Remover ação aqui")
        act_target_here = menu.addAction("Definir Target aqui")
        menu.addSeparator()
        act_mode_add = menu.addAction("Trocar modo para Adicionar")
        act_mode_target = menu.addAction("Trocar modo para Definir Target")
        menu.addSeparator()
        act_undo = menu.addAction("Desfazer")
        act_redo = menu.addAction("Refazer")
        selected = menu.exec(global_pos)
        if selected is None:
            return
        if selected == act_undo:
            self._undo_preview_edit()
            return
        if selected == act_redo:
            self._redo_preview_edit()
            return
        if selected == act_select_here:
            self._select_action_at_grid(gx, gy)
            return
        if selected == act_add_effect_here:
            self.asset_kind_combo.setCurrentText("Effect")
            self._refresh_asset_picker_list()
            self._set_grid_mode("Adicionar")
            self._grid_gesture_snapshot_taken = False
            self._on_grid_edit(gx, gy, dragged=False, ctrl=False)
            return
        if selected == act_add_missile_here:
            self.asset_kind_combo.setCurrentText("Missile")
            self._refresh_asset_picker_list()
            self._set_grid_mode("Adicionar")
            self._grid_gesture_snapshot_taken = False
            self._on_grid_edit(gx, gy, dragged=False, ctrl=False)
            return
        if selected == act_add_object_here:
            self.asset_kind_combo.setCurrentText("Object")
            self._refresh_asset_picker_list()
            self._set_grid_mode("Adicionar")
            self._grid_gesture_snapshot_taken = False
            self._on_grid_edit(gx, gy, dragged=False, ctrl=False)
            return
        if selected == act_add_current_here:
            self._set_grid_mode("Adicionar")
            self._grid_gesture_snapshot_taken = False
            self._on_grid_edit(gx, gy, dragged=False, ctrl=False)
            return
        if selected == act_remove_here:
            self._grid_gesture_snapshot_taken = False
            self._on_grid_edit(gx, gy, dragged=False, ctrl=True)
            return
        if selected == act_target_here:
            self._set_grid_mode("Definir Target")
            self._grid_gesture_snapshot_taken = False
            self._on_grid_edit(gx, gy, dragged=False, ctrl=False)
            return
        mode_map = {
            act_mode_add: "Adicionar",
            act_mode_target: "Definir Target",
        }
        mode = mode_map.get(selected)
        if mode is None:
            return
        self._set_grid_mode(mode)

    def _on_grid_edit(self, gx: int, gy: int, dragged: bool, ctrl: bool) -> None:
        ox, oy = self.preview_grid.origin
        ax = gx - ox
        ay = gy - oy
        self.preview_grid.selected_cell = (gx, gy)
        mode = self._grid_tool_mode
        ts = self.get_selected_timestamp()
        if ts is None:
            QMessageBox.warning(self, "Atencao", "Selecione um timestamp antes de editar no grid.")
            self.action_x.setText(str(ax))
            self.action_y.setText(str(ay))
            self.update_preview_grid(force=True)
            return

        # Clique esquerdo sempre seleciona.
        if not dragged:
            self._select_action_at_grid(gx, gy)

        if ctrl:
            self._push_preview_undo_if_needed()
            idx = self._find_action_index_at_offset(ts, ax, ay)
            if idx >= 0:
                ts.get("actions", []).pop(idx)
                self.refresh_actions_list()
            self.update_preview_grid(force=True)
            return

        if dragged and mode != "Definir Target":
            self._push_preview_undo_if_needed()
            sel = self.actions_list.currentRow()
            actions = ts.get("actions", [])
            if sel < 0 or sel >= len(actions):
                return
            actions[sel]["x"] = ax
            actions[sel]["y"] = ay
            self.action_x.setText(str(ax))
            self.action_y.setText(str(ay))
            self.refresh_actions_list()
            self.actions_list.setCurrentRow(sel)
            self.update_preview_grid(force=True)
            return

        if mode == "Adicionar":
            self._push_preview_undo_if_needed()
            self.action_x.setText(str(ax))
            self.action_y.setText(str(ay))
            ts.setdefault("actions", []).append(self._build_action_from_form())
            self.refresh_actions_list()
            self.actions_list.setCurrentRow(max(0, self.actions_list.count() - 1))
            self.update_preview_grid(force=True)
            return

        if mode == "Definir Target":
            self._push_preview_undo_if_needed()
            rec = self._current_preview()
            if rec is None:
                return
            target = None
            for action in rec.setdefault("initActions", []):
                if action.get("action") == "target":
                    target = action
                    break
            if target is None:
                rec["initActions"].append({"action": "target", "x": ax, "y": ay})
            else:
                target["x"] = ax
                target["y"] = ay
            self.refresh_init_actions_list()
            self.update_preview_grid(force=True)
            return

        self.action_x.setText(str(ax))
        self.action_y.setText(str(ay))
        self.update_preview_grid(force=True)

    def _select_action_at_grid(self, gx: int, gy: int) -> None:
        ox, oy = self.preview_grid.origin
        ax = gx - ox
        ay = gy - oy
        self.action_x.setText(str(ax))
        self.action_y.setText(str(ay))
        ts = self.get_selected_timestamp()
        if ts is None:
            self.update_preview_grid(force=True)
            return
        idx = self._find_action_index_at_offset(ts, ax, ay)
        if idx >= 0:
            self.actions_list.setCurrentRow(idx)
        self.update_preview_grid(force=True)

    def _find_action_index_at_offset(self, ts: dict, ax: int, ay: int) -> int:
        for i, action in enumerate(ts.get("actions", [])):
            try:
                if int(action.get("x", 0)) == ax and int(action.get("y", 0)) == ay:
                    return i
            except Exception:
                continue
        return -1

    def _find_matching_action_at_offset(self, ts: dict, ax: int, ay: int, ref: dict) -> int:
        for i, action in enumerate(ts.get("actions", [])):
            try:
                if int(action.get("x", 0)) != ax or int(action.get("y", 0)) != ay:
                    continue
                if safe_text_value(action.get("action", "")) != safe_text_value(ref.get("action", "")):
                    continue
                aid_a = action.get("effectID", action.get("missileID", action.get("objecttypeID", None)))
                aid_b = ref.get("effectID", ref.get("missileID", ref.get("objecttypeID", None)))
                if aid_a == aid_b:
                    return i
            except Exception:
                continue
        return -1

    def _push_preview_undo_if_needed(self) -> None:
        if self._grid_gesture_snapshot_taken:
            return
        rec = self._current_preview()
        if rec is None or self.selected_preview_key is None:
            return
        self._preview_undo_stack.append((self.selected_preview_key, copy.deepcopy(rec)))
        if len(self._preview_undo_stack) > 100:
            self._preview_undo_stack.pop(0)
        self._preview_redo_stack.clear()
        self._grid_gesture_snapshot_taken = True

    def _undo_preview_edit(self) -> None:
        if not self._preview_undo_stack or self.selected_preview_key is None:
            return
        rec = self._current_preview()
        if rec is not None:
            self._preview_redo_stack.append((self.selected_preview_key, copy.deepcopy(rec)))
            if len(self._preview_redo_stack) > 100:
                self._preview_redo_stack.pop(0)
        key, snapshot = self._preview_undo_stack.pop()
        self.core.previews_data[key] = copy.deepcopy(snapshot)
        if key == self.selected_preview_key:
            self.refresh_timestamps_list()
            self.refresh_init_actions_list()
            self.update_preview_grid(force=True)

    def _redo_preview_edit(self) -> None:
        if not self._preview_redo_stack or self.selected_preview_key is None:
            return
        rec = self._current_preview()
        if rec is not None:
            self._preview_undo_stack.append((self.selected_preview_key, copy.deepcopy(rec)))
            if len(self._preview_undo_stack) > 100:
                self._preview_undo_stack.pop(0)
        key, snapshot = self._preview_redo_stack.pop()
        self.core.previews_data[key] = copy.deepcopy(snapshot)
        if key == self.selected_preview_key:
            self.refresh_timestamps_list()
            self.refresh_init_actions_list()
            self.update_preview_grid(force=True)

    def _pixmap_for(self, pil_img):
        if pil_img is None:
            return None
        key = id(pil_img)
        pix = self._pixmap_cache.get(key)
        if pix is None:
            pix = pil_to_qpixmap(pil_img)
            self._pixmap_cache[key] = pix
        return pix

    def _preview_target_offset(self):
        rec = self._current_preview()
        if rec is None:
            return None
        for action in rec.get("initActions", []):
            if action.get("action") == "target":
                try:
                    return (int(action.get("x", 0)), int(action.get("y", 0)))
                except Exception:
                    return (0, 0)
        return None

    def _on_anim_tick(self) -> None:
        self._anim_tick += 1
        self._update_asset_picker_icons()
        self.update_preview_grid()

    def update_preview_grid(self, force: bool = False) -> None:
        cols, rows = PreviewGrid.COLS, PreviewGrid.ROWS
        ox, oy = self.preview_grid.origin
        target = self._preview_target_offset()
        selected_cell = self.preview_grid.selected_cell
        elapsed_ms = int(self._anim_elapsed.elapsed()) if self._anim_elapsed.isValid() else (self._anim_tick * 33)

        plan: list[tuple[int, int, object]] = []  # (gx, gy, pil_img_or_None)
        ts = self.get_selected_timestamp()
        if ts is not None:
            actions = list(ts.get("actions", []))
            missile_actions = [a for a in actions if a.get("action") == "missile"]
            effect_like_actions = [a for a in actions if a.get("action") in ("fieldEffect", "objecttype")]

            effect_windows = []
            for action in effect_like_actions:
                atype = action.get("action")
                cat = None
                if atype == "fieldEffect":
                    cat = self.core.effect_by_id(int(action.get("effectID", 0)))
                elif atype == "objecttype":
                    cat = self.core.object_by_id(int(action.get("objecttypeID", 0)))
                w = self.core.animation_total_duration_max_ms(cat)
                if w > 0:
                    effect_windows.append(w)
            effect_phase_ms = max(effect_windows) if effect_windows else 1000

            # ciclo de onda:
            # - com missile: missiles viajam -> effects aparecem no alvo -> espera efeito terminar -> repete
            # - sem missile: effects aparecem juntos -> espera terminar -> repete
            missile_travel_ms = 420
            if missile_actions:
                cycle_ms = missile_travel_ms + effect_phase_ms
                cycle_pos = elapsed_ms % cycle_ms
                missile_active = cycle_pos < missile_travel_ms
                effect_active = not missile_active
                missile_local_ms = cycle_pos
                effect_local_ms = max(0, cycle_pos - missile_travel_ms)
            else:
                cycle_ms = effect_phase_ms
                cycle_pos = elapsed_ms % cycle_ms
                missile_active = False
                effect_active = True
                missile_local_ms = 0
                effect_local_ms = cycle_pos

            for action in actions:
                local_ms = elapsed_ms
                try:
                    ax = int(action.get("x", 0))
                    ay = int(action.get("y", 0))
                except Exception:
                    continue
                gx = ox + ax
                gy = oy + ay
                if gx < 0 or gy < 0 or gx >= cols or gy >= rows:
                    continue
                sprite_img = None
                draw_gx, draw_gy = gx, gy
                try:
                    atype = action.get("action")
                    if atype == "fieldEffect":
                        if not effect_active:
                            continue
                        cat = self.core.effect_by_id(int(action.get("effectID", 0)))
                        if cat is not None:
                            pw = max(1, int(cat.get("pattern_width", 1)))
                            ph = max(1, int(cat.get("pattern_height", 1)))
                            sprite_img = self.core.sprite_for_catalog_entry(cat, effect_local_ms, ((gx % pw) + pw) % pw, ((gy % ph) + ph) % ph)
                    elif atype == "missile":
                        if not missile_active:
                            continue
                        cat = self.core.missile_by_id(int(action.get("missileID", 0)))
                        mpx, mpy = self.core.missile_pattern_for_offset(ax, ay)
                        sprite_img = self.core.sprite_for_catalog_entry(cat, missile_local_ms, mpx, mpy)
                        phase = min(1.0, max(0.0, missile_local_ms / missile_travel_ms))
                        draw_gx = int(round(ox + (gx - ox) * phase))
                        draw_gy = int(round(oy + (gy - oy) * phase))
                    elif atype == "objecttype" and self.render_objecttype.isChecked():
                        if not effect_active:
                            continue
                        cat = self.core.object_by_id(int(action.get("objecttypeID", 0)))
                        if cat is not None:
                            pw = max(1, int(cat.get("pattern_width", 1)))
                            ph = max(1, int(cat.get("pattern_height", 1)))
                            sprite_img = self.core.sprite_for_catalog_entry(cat, effect_local_ms, ((gx % pw) + pw) % pw, ((gy % ph) + ph) % ph)
                except Exception:
                    sprite_img = None
                plan.append((draw_gx, draw_gy, sprite_img))

        sig = (ox, oy, target, selected_cell, tuple((gx, gy, id(img) if img is not None else None) for gx, gy, img in plan))
        if not force and sig == self._render_sig:
            return
        self._render_sig = sig
        draws = [(gx, gy, self._pixmap_for(img)) for gx, gy, img in plan]
        self.preview_grid.set_plan((ox, oy), target, selected_cell, draws)

    # ===================================================================
    # Build
    # ===================================================================
    def on_compile_install(self) -> None:
        self.run_bg(self.core.compile_and_install, lambda: QMessageBox.information(self, "Build", "Compilacao + instalacao concluida."))

    def on_manual_backup(self) -> None:
        self.run_bg(self.core.manual_backup, lambda: QMessageBox.information(self, "Backup", "Backup manual concluido."))

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_ui_settings()
        super().closeEvent(event)
