"""Characters that can be registered for an area's NPC picker."""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget)


class NpcCatalogueDialog(QDialog):
    def __init__(self, host, destination, preferred_data=None):
        super().__init__(host)
        self.host = host
        self.imported_model = None
        self.imported_destination = None
        self.preferred_data = preferred_data
        self.setWindowTitle('NPC models / Import NPC')
        self.resize(900, 620)
        names = host._area_names()
        areas = [(chunk, names.get(chunk, f'AREA_{chunk:02X}'))
                 for chunk in range(4, 26) if host.overlay_for_area(chunk)]
        self.destination = QComboBox()
        self.source = QComboBox()
        for chunk, name in areas:
            self.destination.addItem(name, chunk)
            self.source.addItem(name, chunk)
        for combo in (self.destination, self.source):
            index = combo.findData(destination)
            if index >= 0:
                combo.setCurrentIndex(index)
        form = QFormLayout()
        form.addRow('NPCs available in', self.destination)
        source_row = QHBoxLayout()
        source_row.addWidget(self.source, 1)
        self.scan_button = QPushButton('Find characters')
        self.scan_button.clicked.connect(self.scan)
        source_row.addWidget(self.scan_button)
        form.addRow('Characters from', source_row)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search characters…')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter)
        self.available = QListWidget()
        self.available.currentItemChanged.connect(self.selection_changed)
        self.details = QLabel('Find characters in an area, then register or import one.')
        self.details.setWordWrap(True)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel('Complete characters in the source area'))
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.available, 1)
        left_layout.addWidget(self.details)
        self.registered = QListWidget()
        self.registered.currentItemChanged.connect(self.registered_changed)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel('Available in New NPC / Change NPC model'))
        right_layout.addWidget(self.registered, 1)
        self.use_button = QPushButton('Use selected model')
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self.use_selected)
        right_layout.addWidget(self.use_button)
        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([550, 300])
        self.name = QLineEdit()
        self.name.setPlaceholderText('Name in the NPC picker')
        self.state_path = QLineEdit()
        self.state_path.setPlaceholderText('Only needed when the character needs additional textures')
        self.state_path.setToolTip('A savestate taken while standing in the destination area.')
        state_row = QHBoxLayout()
        state_row.addWidget(self.state_path, 1)
        browse = QPushButton('Browse…')
        browse.clicked.connect(self.browse_state)
        state_row.addWidget(browse)
        fields = QFormLayout()
        fields.addRow('NPC model name', self.name)
        fields.addRow('Destination savestate', state_row)
        self.import_button = QPushButton('Register for this area')
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.import_selected)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        intro = QLabel('Register a complete character to make its model and animations selectable. '
                       'Choose another source area to bring a character into this area.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(splitter, 1)
        layout.addLayout(fields)
        layout.addWidget(self.import_button)
        layout.addWidget(self.status)
        layout.addWidget(buttons)
        self.destination.currentIndexChanged.connect(self.destination_changed)
        self.source.currentIndexChanged.connect(self.source_changed)
        self.destination_changed()
        QTimer.singleShot(0, self.scan)

    def destination_changed(self):
        self.registered.clear()
        destination = self.destination.currentData()
        if destination is not None:
            try:
                for model in self.host.npc_model_choices(destination):
                    item = QListWidgetItem(model.name, self.registered)
                    item.setData(Qt.ItemDataRole.UserRole, model)
                    item.setToolTip(model.describe())
            except (OSError, ValueError) as error:
                self.status.setText(str(error))
        if self.registered.count():
            self.registered.setCurrentRow(0)
        self.import_button.setText('Register for this area' if destination == self.source.currentData()
                                   else 'Import into this area')

    def source_changed(self):
        self.available.clear()
        self.import_button.setEnabled(False)
        self.import_button.setText('Register for this area' if self.destination.currentData() == self.source.currentData()
                                   else 'Import into this area')
        self.details.setText('Click Find characters to read this source area.')

    def scan(self):
        chunk = self.source.currentData()
        if chunk is None:
            return
        try:
            self.scan_button.setEnabled(False)
            recipes, rejected = self.host.npc_catalogue_recipes(chunk, self)
            self.available.clear()
            preferred = None
            for recipe in recipes:
                item = QListWidgetItem(recipe.name, self.available)
                item.setData(Qt.ItemDataRole.UserRole, recipe)
                item.setToolTip(recipe.describe())
                if (self.preferred_data is not None
                        and recipe.model_data.rstrip(b'\0') == self.preferred_data.rstrip(b'\0')):
                    preferred = self.available.count() - 1
            self.filter(self.search.text())
            if preferred is not None:
                self.available.setCurrentRow(preferred)
            elif self.available.count():
                self.available.setCurrentRow(0)
            text = f'{len(recipes)} complete character models found.'
            if rejected:
                text += f' {len(rejected)} native actor configurations could not be imported.'
                self.status.setToolTip('\n'.join(f'Scene {scene}: {reason}' for scene, _handler, reason in rejected[:25]))
            self.status.setText(text)
        except Exception as error:
            QMessageBox.warning(self, 'Character search', str(error))
        finally:
            self.scan_button.setEnabled(True)

    def filter(self, text):
        first = None
        for row in range(self.available.count()):
            item = self.available.item(row)
            hidden = text.strip().casefold() not in item.text().casefold()
            item.setHidden(hidden)
            if not hidden and first is None:
                first = row
        if first is not None and (self.available.currentItem() is None
                                  or self.available.currentItem().isHidden()):
            self.available.setCurrentRow(first)

    def selection_changed(self):
        item = self.available.currentItem()
        recipe = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.import_button.setEnabled(recipe is not None and not item.isHidden())
        if recipe is None:
            return
        self.name.setText(recipe.name)
        self.details.setText(f'{recipe.parts} model parts; {recipe.sequences.clip_count} animations. '
                             'The model, skeleton, animations and required textures are kept together.')

    def registered_changed(self):
        self.use_button.setEnabled(self.registered.currentItem() is not None)

    def use_selected(self):
        item = self.registered.currentItem()
        if item is not None:
            self.imported_model = item.data(Qt.ItemDataRole.UserRole)
            self.imported_destination = self.destination.currentData()
            self.accept()

    def browse_state(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, 'Savestate from the destination area', self.state_path.text(),
            'Emulator savestates (*.sav *.state *.sstate *.000 *.001 *.002);;All files (*)')
        if path:
            self.state_path.setText(path)

    def import_selected(self):
        item = self.available.currentItem()
        recipe = item.data(Qt.ItemDataRole.UserRole) if item else None
        if recipe is None:
            return
        destination = self.destination.currentData()
        try:
            self.import_button.setEnabled(False)
            model = self.host.import_npc_recipe(
                recipe, destination, name=self.name.text().strip() or recipe.name,
                state_path=self.state_path.text().strip() or None, parent=self)
            if model is None:
                return
            self.imported_model = model
            self.imported_destination = destination
            self.destination_changed()
            for row in range(self.registered.count()):
                candidate = self.registered.item(row).data(Qt.ItemDataRole.UserRole)
                if candidate.layout == model.layout:
                    self.registered.setCurrentRow(row)
                    break
            self.status.setText(f'{model.name} is now available in New NPC and Change NPC model. '
                                'Save Project, then Build Disc to use it in game.')
        except Exception as error:
            QMessageBox.warning(self, 'NPC model import', str(error))
        finally:
            self.import_button.setEnabled(self.available.currentItem() is not None)
