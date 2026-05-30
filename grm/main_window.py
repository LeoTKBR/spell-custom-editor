"""Janela principal: monta a UI e orquestra GraphicsCore via Worker."""

from __future__ import annotations

import copy
import json

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QPixmap
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
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextEdit,
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

        self._build_ui()
        self._set_loaded_actions_state(False)

        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._on_anim_tick)
        self._anim_timer.start(33)

    # ===================================================================
    # Construcao da UI
    # ===================================================================
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

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
        self.log_view.setFixedHeight(150)
        lb.addWidget(self.log_view)
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

        view_menu = menubar.addMenu("Exibir")
        self.act_dark = QAction("Tema escuro", self, checkable=True, checked=True)
        self.act_dark.triggered.connect(lambda: apply_theme(QApplication.instance(), self.act_dark.isChecked()))
        view_menu.addAction(self.act_dark)

        tb = self.addToolBar("Acoes")
        tb.addAction(self.act_compile)
        tb.addAction(self.act_backup)

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
        left.setMaximumWidth(360)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        form = QFormLayout()
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
        rl.addWidget(QLabel("description"))
        self.spell_description = QTextEdit()
        self.spell_description.setFixedHeight(80)
        rl.addWidget(self.spell_description)
        rl.addWidget(QLabel("Campos extras (JSON opcional)"))
        self.spell_extra = QTextEdit()
        rl.addWidget(self.spell_extra, 1)
        srow = QHBoxLayout()
        self.btn_spell_save = QPushButton("Salvar Registro")
        self.btn_spell_save.clicked.connect(self.save_spell_record)
        self.btn_spells_save_all = QPushButton("Salvar Arquivo")
        self.btn_spells_save_all.clicked.connect(self.on_save_spells_file)
        srow.addWidget(self.btn_spell_save)
        srow.addWidget(self.btn_spells_save_all)
        srow.addStretch(1)
        rl.addLayout(srow)
        split.addWidget(right)
        split.setStretchFactor(1, 1)

        wrap = QWidget()
        QVBoxLayout(wrap).addWidget(split)
        return wrap

    # ---- aba de previews ------------------------------------------------
    def _build_previews_tab(self) -> QWidget:
        split = QSplitter(Qt.Orientation.Horizontal)

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
        left.setMaximumWidth(360)
        split.addWidget(left)

        right = QTabWidget()
        right.addTab(self._build_preview_structural(), "Estrutural")
        right.addTab(self._build_preview_grid_tab(), "Grid FX/Missiles")
        split.addWidget(right)
        split.setStretchFactor(1, 1)

        wrap = QWidget()
        QVBoxLayout(wrap).addWidget(split)
        return wrap

    def _build_preview_structural(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        form = QFormLayout()
        self.preview_fields = {"spellid": QLineEdit(), "name": QLineEdit(), "range": QLineEdit()}
        form.addRow("spellid", self.preview_fields["spellid"])
        form.addRow("name", self.preview_fields["name"])
        form.addRow("range", self.preview_fields["range"])
        lay.addLayout(form)

        editors = QHBoxLayout()
        ts_box = QGroupBox("Timestamps")
        tsl = QVBoxLayout(ts_box)
        self.timestamps_list = QListWidget()
        self.timestamps_list.currentRowChanged.connect(self.on_select_timestamp)
        tsl.addWidget(self.timestamps_list)
        tsb = QHBoxLayout()
        b1 = QPushButton("Adicionar")
        b1.clicked.connect(self.add_timestamp)
        b2 = QPushButton("Remover")
        b2.clicked.connect(self.remove_timestamp)
        tsb.addWidget(b1)
        tsb.addWidget(b2)
        tsl.addLayout(tsb)
        editors.addWidget(ts_box, 1)

        act_box = QGroupBox("Actions do Timestamp")
        al = QVBoxLayout(act_box)
        self.actions_list = QListWidget()
        self.actions_list.currentRowChanged.connect(self.on_select_action)
        al.addWidget(self.actions_list)
        af = QHBoxLayout()
        self.action_type = QComboBox()
        self.action_type.addItems(["fieldEffect", "missile", "objecttype", "target"])
        self.action_id = QLineEdit()
        self.action_id.setFixedWidth(70)
        self.action_x = QLineEdit("0")
        self.action_x.setFixedWidth(45)
        self.action_y = QLineEdit("0")
        self.action_y.setFixedWidth(45)
        af.addWidget(QLabel("action"))
        af.addWidget(self.action_type)
        af.addWidget(QLabel("id"))
        af.addWidget(self.action_id)
        af.addWidget(QLabel("x"))
        af.addWidget(self.action_x)
        af.addWidget(QLabel("y"))
        af.addWidget(self.action_y)
        al.addLayout(af)
        ab = QHBoxLayout()
        for label, slot in (("Adicionar", self.add_action), ("Atualizar", self.update_action), ("Remover", self.remove_action)):
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            ab.addWidget(btn)
        al.addLayout(ab)
        editors.addWidget(act_box, 1)
        lay.addLayout(editors, 1)

        init_box = QGroupBox("InitActions")
        il = QVBoxLayout(init_box)
        self.init_actions_list = QListWidget()
        self.init_actions_list.currentRowChanged.connect(self.on_select_init_action)
        self.init_actions_list.setFixedHeight(90)
        il.addWidget(self.init_actions_list)
        iform = QHBoxLayout()
        self.init_action_type = QComboBox()
        self.init_action_type.addItems(["target", "fieldEffect"])
        self.init_action_x = QLineEdit("0")
        self.init_action_x.setFixedWidth(45)
        self.init_action_y = QLineEdit("0")
        self.init_action_y.setFixedWidth(45)
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
        lay.addWidget(init_box)

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
        lay = QHBoxLayout(tab)

        side = QVBoxLayout()
        self.render_objecttype = QCheckBox("Renderizar objecttype (experimental)")
        self.render_objecttype.setChecked(True)
        self.render_objecttype.stateChanged.connect(lambda *_: self.update_preview_grid(force=True))
        side.addWidget(self.render_objecttype)

        fx_box = QGroupBox("Effect")
        fl = QHBoxLayout(fx_box)
        self.effect_spin = QSpinBox()
        self.effect_spin.setRange(1, 1)
        fl.addWidget(self.effect_spin)
        bfx = QPushButton("Aplicar")
        bfx.clicked.connect(self.apply_effect_id)
        fl.addWidget(bfx)
        side.addWidget(fx_box)

        ms_box = QGroupBox("Missile")
        ml = QHBoxLayout(ms_box)
        self.missile_spin = QSpinBox()
        self.missile_spin.setRange(1, 1)
        ml.addWidget(self.missile_spin)
        bms = QPushButton("Aplicar")
        bms.clicked.connect(self.apply_missile_id)
        ml.addWidget(bms)
        side.addWidget(ms_box)
        side.addStretch(1)
        side_w = QWidget()
        side_w.setLayout(side)
        side_w.setMaximumWidth(240)
        lay.addWidget(side_w)

        grid_box = QGroupBox("Preview Visual 16x14 (32px)")
        gl = QVBoxLayout(grid_box)
        self.preview_grid = PreviewGrid()
        self.preview_grid.cellClicked.connect(self.on_grid_click)
        gl.addWidget(self.preview_grid, 1)
        lay.addWidget(grid_box, 1)
        return tab

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
        me = max(1, self.core.max_effect_id())
        mm = max(1, self.core.max_missile_id())
        self.effect_spin.setRange(1, me)
        self.missile_spin.setRange(1, mm)

    def apply_effect_id(self) -> None:
        self.action_type.setCurrentText("fieldEffect")
        self.action_id.setText(str(self.effect_spin.value()))
        self.update_preview_grid(force=True)

    def apply_missile_id(self) -> None:
        self.action_type.setCurrentText("missile")
        self.action_id.setText(str(self.missile_spin.value()))
        self.update_preview_grid(force=True)

    def on_grid_click(self, gx: int, gy: int) -> None:
        ox, oy = self.preview_grid.origin
        self.preview_grid.selected_cell = (gx, gy)
        self.action_x.setText(str(gx - ox))
        self.action_y.setText(str(gy - oy))
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
        self.update_preview_grid()

    def update_preview_grid(self, force: bool = False) -> None:
        cols, rows = PreviewGrid.COLS, PreviewGrid.ROWS
        ox, oy = self.preview_grid.origin
        target = self._preview_target_offset()
        selected_cell = self.preview_grid.selected_cell

        plan: list[tuple[int, int, object]] = []  # (gx, gy, pil_img_or_None)
        ts = self.get_selected_timestamp()
        if ts is not None:
            for action in ts.get("actions", []):
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
                        cat = self.core.effect_by_id(int(action.get("effectID", 0)))
                        if cat is not None:
                            pw = max(1, int(cat.get("pattern_width", 1)))
                            ph = max(1, int(cat.get("pattern_height", 1)))
                            sprite_img = self.core.sprite_for_catalog_entry(cat, self._anim_tick, ((gx % pw) + pw) % pw, ((gy % ph) + ph) % ph)
                    elif atype == "missile":
                        cat = self.core.missile_by_id(int(action.get("missileID", 0)))
                        mpx, mpy = self.core.missile_pattern_for_offset(ax, ay)
                        sprite_img = self.core.sprite_for_catalog_entry(cat, self._anim_tick, mpx, mpy)
                        phase = (self._anim_tick % 8) / 7.0
                        draw_gx = int(round(ox + (gx - ox) * phase))
                        draw_gy = int(round(oy + (gy - oy) * phase))
                    elif atype == "objecttype" and self.render_objecttype.isChecked():
                        cat = self.core.object_by_id(int(action.get("objecttypeID", 0)))
                        if cat is not None:
                            pw = max(1, int(cat.get("pattern_width", 1)))
                            ph = max(1, int(cat.get("pattern_height", 1)))
                            sprite_img = self.core.sprite_for_catalog_entry(cat, self._anim_tick, ((gx % pw) + pw) % pw, ((gy % ph) + ph) % ph)
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
