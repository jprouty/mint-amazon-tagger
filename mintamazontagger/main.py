#!/usr/bin/env python3

# This tool takes an Amazon Data Export and annotates Mint
# transactions based on the actual items in each order. It can handle charges
# that are split into multiple shipments/charges and can itemized each
# transaction for maximal control over categorization.

import argparse
import atexit
import datetime
from functools import partial
import logging
import os
from signal import signal, SIGINT
import sys
import time
from urllib.parse import urlencode
import webbrowser

from PyQt6.QtCore import (
    Q_ARG, QDate, QEventLoop, Qt, QMetaObject, QObject, QTimer, QThread,
    QUrl, pyqtSlot, pyqtSignal)
from PyQt6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCalendarWidget,
    QCheckBox, QComboBox, QDialog, QErrorMessage, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QProgressBar,
    QPushButton, QTableView, QWidget, QVBoxLayout)
from outdated import check_outdated

from mintamazontagger import amazon
from mintamazontagger import tagger
from mintamazontagger import VERSION
from mintamazontagger.args import (
    define_gui_args, get_name_to_help_dict, TAGGER_BASE_PATH)
from mintamazontagger.qt import (
    MintUpdatesTableModel, AmazonUnmatchedTableDialog, AmazonStatsDialog,
    TaggerStatsDialog)
from mintamazontagger.mintclient import MintClient
from mintamazontagger.my_progress import QtProgress
from mintamazontagger.webdriver import get_webdriver

logger = logging.getLogger(__name__)

NEVER_SAVE_MSG = 'Email & password are *never* saved.'


class TaggerGui:
    def __init__(self, args, arg_name_to_help, log_filename):
        self.args = args
        self.arg_name_to_help = arg_name_to_help
        self.log_filename = log_filename

    def create_gui(self):
        app = QApplication(sys.argv)

        timer = QTimer()
        timer.start(500)
        timer.timeout.connect(lambda: None)

        app.setStyle('Fusion')
        version_string = f'Mint Amazon Tagger v{VERSION}'
        app.setApplicationName(version_string)
        self.window = QMainWindow()
        self.window.setWindowTitle(version_string)

        self.quit_shortcuts = []
        for seq in ("Ctrl+Q", "Ctrl+C", "Ctrl+W", "ESC"):
            s = QShortcut(QKeySequence(seq), self.window)
            s.activated.connect(app.exit)
            self.quit_shortcuts.append(s)

        logger.info(f'Running version {VERSION}')
        try:
            is_outdated, latest_version = check_outdated(
                'mint-amazon-tagger', VERSION)
            if is_outdated:
                outdate_msg = QErrorMessage(self.window)
                outdate_msg.showMessage(
                    'A new version is available. Please update for the best '
                    'experience. '
                    'https://github.com/jprouty/mint-amazon-tagger')
                logger.warning(
                    'Running out of date software is bad. Latest is '
                    f'{latest_version}')
        except ValueError:
            logger.error(
                f'Version {VERSION} is newer than PyPY version')

        v_layout = QVBoxLayout()
        h_layout = QHBoxLayout()
        v_layout.addLayout(h_layout)

        amazon_group = QGroupBox('Amazon Order History')
        amazon_group.setMinimumWidth(300)
        amazon_layout = QVBoxLayout()

        amazon_mode_layout = self.create_amazon_import_layout()

        amazon_layout.addLayout(amazon_mode_layout)
        amazon_group.setLayout(amazon_layout)
        h_layout.addWidget(amazon_group)

        mint_group = QGroupBox('Mint Login && Options')
        mint_group.setMinimumWidth(350)
        mint_layout = QFormLayout()

        mint_layout.addRow(
            self.create_line_label('Email:', 'mint_email'),
            self.create_line_edit('mint_email', tool_tip=NEVER_SAVE_MSG))
        mint_layout.addRow(
            self.create_line_label('Password:', 'mint_password'),
            self.create_line_edit(
                'mint_password', tool_tip=NEVER_SAVE_MSG, password=True))
        mint_layout.addRow(
            self.create_line_label('MFA Code: ', 'mint_mfa_preferred_method'),
            self.create_combobox(
                'mint_mfa_preferred_method',
                ['sms', 'email'],
                lambda x: x.lower()))
        mint_layout.addRow(
            self.create_line_label('I will login myself',
                                   'mint_user_will_login'),
            self.create_checkbox('mint_user_will_login'))
        mint_layout.addRow(
            self.create_line_label('Sync first?', 'mint_wait_for_sync'),
            self.create_checkbox('mint_wait_for_sync'))

        mint_layout.addRow(
            self.create_line_label('Description Filter',
                                   'mint_input_description_filter'),
            self.create_line_edit('mint_input_description_filter'))
        mint_layout.addRow(
            self.create_line_label(
                'Include user description', 'mint_input_include_user_description'),
            self.create_checkbox('mint_input_include_user_description'))
        mint_layout.addRow(
            self.create_line_label(
                'Include inferred description', 'mint_input_include_inferred_description'),
            self.create_checkbox('mint_input_include_inferred_description'))
        mint_layout.addRow(
            self.create_line_label(
                'Input Categories Filter', 'mint_input_categories_filter'),
            self.create_line_edit('mint_input_categories_filter'))
        mint_group.setLayout(mint_layout)
        h_layout.addWidget(mint_group)

        tagger_group = QGroupBox('Tagger Options')
        tagger_layout = QHBoxLayout()
        tagger_left = QFormLayout()

        tagger_left.addRow(
            self.create_line_label('Verbose Itemize', 'verbose_itemize'),
            self.create_checkbox('verbose_itemize'))
        tagger_left.addRow(
            self.create_line_label('Do not Itemize', 'no_itemize'),
            self.create_checkbox('no_itemize'))
        tagger_left.addRow(
            self.create_line_label('Retag Changed', 'retag_changed'),
            self.create_checkbox('retag_changed'))

        tagger_right = QFormLayout()
        tagger_right.addRow(
            self.create_line_label(
                'Do not tag categories', 'no_tag_categories'),
            self.create_checkbox('no_tag_categories'))
        tagger_right.addRow(
            self.create_line_label(
                'Do not predict categories', 'do_not_predict_categories'),
            self.create_checkbox('do_not_predict_categories'))
        tagger_right.addRow(
            self.create_line_label(
                'Max days between payment/shipment', 'max_days_between_payment_and_shipping'),
            self.create_combobox(
                'max_days_between_payment_and_shipping',
                [3, 4, 5, 6, 7, 8, 9, 10],
                lambda x: int(x)))

        tagger_layout.addLayout(tagger_left)
        tagger_layout.addLayout(tagger_right)
        tagger_group.setLayout(tagger_layout)
        v_layout.addWidget(tagger_group)

        self.start_button = QPushButton('Start Tagging')
        self.start_button.setAutoDefault(True)
        self.start_button.clicked.connect(self.on_start_button_clicked)
        v_layout.addWidget(self.start_button)

        main_widget = QWidget()
        main_widget.setLayout(v_layout)
        self.window.setCentralWidget(main_widget)
        self.window.show()
        # return app.exec_()
        return app.exec()

    def create_amazon_import_layout(self):
        amazon_import_layout = QFormLayout()

        order_history_link = QLabel()
        order_history_link.setText(
            '''<a href="https://www.amazon.com/hz/privacy-central/data-requests/preview.html">
            Export Amazon Data</a><br>
            and select below when complete:''')
        order_history_link.setOpenExternalLinks(True)
        amazon_import_layout.addRow(order_history_link)

        amazon_import_layout.addRow(
            self.create_line_label('Data Export:', 'amazon_export'),
            self.create_file_edit(
                'amazon_export',
                'Select Amazon Data Export'
            ))
        return amazon_import_layout

    def on_quit(self):
        pass

    def on_tagger_dialog_closed(self):
        self.start_button.setEnabled(True)
        # Reset any csv file handles, as there might have been an error and
        # the user may try again (could already be consumed/closed).
        attr_name = 'amazon_export'
        files = getattr(self.args, attr_name)
        if files:
            files = [open(file.name, 'r', encoding='utf-8') for file in files]
            setattr(self.args, attr_name, files)

    def on_start_button_clicked(self):
        self.start_button.setEnabled(False)
        # If the fetch tab is selected for Amazon order history, clear out any
        # provided csv file paths, so the tagger actually fetches (versus using
        # the given paths).
        args = argparse.Namespace(**vars(self.args))
        # Input validation for Amazon Export Zip:
        if not getattr(self.args, 'amazon_export'):
            error_dialog = QErrorMessage(self.window)
            error_dialog.showMessage('Please provide valid Amazon Export Zip file')
            logger.error('User did not provide Amazon Export zip')
            self.on_tagger_dialog_closed()
            return

        # Input validation for mint login credentials:
        if not getattr(self.args, 'mint_user_will_login') and (not getattr(self.args, 'mint_email') or not getattr(self.args, 'mint_password')):
                error_dialog = QErrorMessage(self.window)
                error_dialog.showMessage('Mint: Please select "I will login myself" or provide an email and password.')
                self.on_tagger_dialog_closed()
                return

        self.tagger = TaggerDialog(
            args=args,
            parent=self.window,
            log_filename=self.log_filename)
        self.tagger.show()
        self.tagger.finished.connect(self.on_tagger_dialog_closed)

    def clear_layout(self, layout):
        if layout:
            while layout.count():
                child = layout.takeAt(0)
                if child.widget() is not None:
                    child.widget().deleteLater()
                elif child.layout() is not None:
                    self.clear_layout(child.layout())

    def create_checkbox(self, name, tool_tip=None, invert=False):
        x_box = QCheckBox()
        x_box.setTristate(False)
        x_box.setCheckState(
            Qt.CheckState.Checked if getattr(self.args, name) else Qt.CheckState.Unchecked)
        if not tool_tip and name in self.arg_name_to_help:
            tool_tip = 'When checked, ' + self.arg_name_to_help[name]
        if tool_tip:
            x_box.setToolTip(tool_tip)

        def on_changed(state):
            setattr(
                self.args, name,
                state != Qt.CheckState.Checked.value if invert else state == Qt.CheckState.Checked.value)
        x_box.stateChanged.connect(on_changed)
        return x_box

    def advance_focus(self):
        self.window.focusNextChild()

    def create_line_label(self, label, tool_tip_name=None, tool_tip=None):
        line_edit = QLabel(label)
        if not tool_tip and tool_tip_name:
            tool_tip = self.arg_name_to_help[tool_tip_name]
        if tool_tip:
            line_edit.setToolTip(tool_tip)
        return line_edit

    def create_line_edit(self, name, tool_tip=None, password=False):
        line_edit = QLineEdit(getattr(self.args, name))
        if not tool_tip:
            tool_tip = self.arg_name_to_help[name]
        if tool_tip:
            line_edit.setToolTip(tool_tip)
        if password:
            line_edit.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)

        def on_changed(state):
            setattr(self.args, name, state)

        def on_return():
            self.advance_focus()
        line_edit.textChanged.connect(on_changed)
        line_edit.returnPressed.connect(on_return)
        return line_edit

    def create_date_edit(
            self, name, popup_title, max_date=datetime.date.today(),
            tool_tip=None):
        date_edit = QPushButton(str(getattr(self.args, name)))
        date_edit.setAutoDefault(True)
        if not tool_tip:
            tool_tip = self.arg_name_to_help[name]
        if tool_tip:
            date_edit.setToolTip(tool_tip)

        def on_date_edit_clicked():
            dlg = QDialog(self.window)
            dlg.setWindowTitle(popup_title)
            layout = QVBoxLayout()
            cal = QCalendarWidget()
            cal.setMaximumDate(QDate(max_date))
            cal.setSelectedDate(QDate(getattr(self.args, name)))
            cal.selectionChanged.connect(lambda: dlg.accept())
            layout.addWidget(cal)
            okay = QPushButton('Select')
            okay.clicked.connect(lambda: dlg.accept())
            layout.addWidget(okay)
            dlg.setLayout(layout)
            dlg.exec()

            setattr(self.args, name, cal.selectedDate().toPyDate())
            date_edit.setText(str(getattr(self.args, name)))

        date_edit.clicked.connect(on_date_edit_clicked)
        return date_edit

    def create_file_edit(
            self, name, popup_title, filter='Zip files (*.zip)',
            tool_tip=None):
        label = 'Select a file'
        files = getattr(self.args, name)
        if files:
            if not isinstance(files, list):
                files = [files]
            
            label = ' AND '.join([os.path.split(file.name)[1] for file in files])
        file_button = QPushButton(label)

        if not tool_tip:
            tool_tip = self.arg_name_to_help[name]
        if tool_tip:
            file_button.setToolTip(tool_tip)

        def on_button():
            dlg = QFileDialog()
            selection = dlg.getOpenFileNames(
                self.window, popup_title, filter=filter)
            if selection[0]:
                prev_files = getattr(self.args, name)
                if prev_files:
                    for file in prev_files:
                        file.close()
                new_files = [open(file, 'r', encoding='utf-8') for file in selection[0]]
                setattr(self.args, name, new_files)
                label = ' AND '.join([os.path.split(file.name)[1] for file in new_files])
                file_button.setText(label)

        file_button.clicked.connect(on_button)
        return file_button

    def create_combobox(self, name, items, transform, tool_tip=None):
        combo = QComboBox()
        # combo.setFocusPolicy(Qt.StrongFocus)
        if not tool_tip:
            tool_tip = self.arg_name_to_help[name]
        if tool_tip:
            combo.setToolTip(tool_tip)
        combo.addItems([str(i) for i in items])

        default_value = getattr(self.args, name)
        combo.setCurrentIndex(items.index(default_value))

        def on_change(option):
            setattr(self.args, name, transform(option))
        combo.currentTextChanged.connect(on_change)
        return combo


class TaggerDialog(QDialog):
    def __init__(self, args, log_filename, **kwargs):
        super(TaggerDialog, self).__init__(**kwargs)

        self.reviewing = False
        self.args = args
        self.log_filename = log_filename

        self.worker = TaggerWorker()
        self.thread = QThread()
        self.worker.moveToThread(self.thread)

        self.worker.on_error.connect(self.on_error)
        self.worker.on_review_ready.connect(self.on_review_ready)
        self.worker.on_stopped.connect(self.on_stopped)
        self.worker.on_progress.connect(self.on_progress)
        self.worker.on_updates_sent.connect(self.on_updates_sent)
        self.worker.on_mfa.connect(self.on_mfa)

        self.thread.started.connect(
            partial(self.worker.create_updates, args, self))
        self.thread.start()

        self.init_ui()

    def init_ui(self):
        self.setWindowTitle('Tagger is running...')
        self.setModal(True)
        self.v_layout = QVBoxLayout()
        self.setLayout(self.v_layout)

        self.label = QLabel()
        self.v_layout.addWidget(self.label)

        self.progress = 0
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.v_layout.addWidget(self.progress_bar)

        self.button_bar = QHBoxLayout()
        self.v_layout.addLayout(self.button_bar)

        self.cancel_button = QPushButton('Cancel')
        self.button_bar.addWidget(self.cancel_button)
        self.cancel_button.clicked.connect(self.on_cancel)

    def on_error(self, msg):
        logger.error(msg)
        self.label.setText(f'Error: {msg}')
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setStyleSheet(
            'QLabel { color: red; font-weight: bold; }')

        self.report_issue_button = QPushButton('Report Issue on Github')
        self.button_bar.addWidget(self.report_issue_button)
        self.report_issue_button.clicked.connect(self.on_report_issue)

        self.cancel_button.setText('Close')
        self.cancel_button.clicked.connect(self.on_stopped)

    def on_report_issue(self):
        logger.info('Report Issue Clicked')
        url_params = {
            'title': f'In-app Report for v{VERSION} on {sys.platform}',
            'body': (
                'Behavior observed: \n\n\n'
                'Expected behavior: \n\n\n'
                f'Please attach your log file: {self.log_filename}')
        }
        webbrowser.open(
            'https://github.com/jprouty/mint-amazon-tagger/issues/'
            f'new?{urlencode(url_params)}')
        self.on_stopped()

    def open_amazon_order_id(self, order_id):
        if order_id:
            QDesktopServices.openUrl(QUrl(
                amazon.get_invoice_url(order_id)))

    def on_activated(self, index):
        # Only handle clicks on the order_id cell.
        if index.column() != 5:
            return
        order_id = self.updates_table_model.data(index, Qt.ItemDataRole.DisplayRole)
        self.open_amazon_order_id(order_id)

    def on_double_click(self, index):
        if index.column() == 5:
            # Ignore double clicks on the order_id cell.
            return
        order_id_cell = self.updates_table_model.createIndex(index.row(), 5)
        order_id = self.updates_table_model.data(order_id_cell, Qt.ItemDataRole.DisplayRole)
        self.open_amazon_order_id(order_id)

    def on_review_ready(self, results):
        self.reviewing = True
        self.progress_bar.hide()

        self.label.setText('Select below which updates to send to Mint.')

        self.updates_table_model = MintUpdatesTableModel(results.updates)
        self.updates_table = QTableView()
        self.updates_table.doubleClicked.connect(self.on_double_click)
        self.updates_table.clicked.connect(self.on_activated)

        def resize():
            self.updates_table.resizeColumnsToContents()
            self.updates_table.resizeRowsToContents()
            min_width = sum(
                self.updates_table.columnWidth(i) for i in range(6))
            self.updates_table.setMinimumSize(min_width + 20, 600)

        self.updates_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.updates_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.updates_table.setModel(self.updates_table_model)
        self.updates_table.setSortingEnabled(True)
        resize()
        self.updates_table_model.layoutChanged.connect(resize)

        self.v_layout.insertWidget(2, self.updates_table)

        unmatched_button = QPushButton('View Unmatched Amazon charges')
        self.button_bar.addWidget(unmatched_button)
        unmatched_button.clicked.connect(
            partial(self.on_open_unmatched, results.unmatched_charges))

        amazon_stats_button = QPushButton('Amazon Stats')
        self.button_bar.addWidget(amazon_stats_button)
        amazon_stats_button.clicked.connect(
            partial(self.on_open_amazon_stats,
                    results.items,
                    results.charges,
                    []))

        tagger_stats_button = QPushButton('Tagger Stats')
        self.button_bar.addWidget(tagger_stats_button)
        tagger_stats_button.clicked.connect(
            partial(self.on_open_tagger_stats, results.stats))

        self.confirm_button = QPushButton('Send to Mint')
        self.button_bar.addWidget(self.confirm_button)
        self.confirm_button.clicked.connect(self.on_send)

        self.setGeometry(50, 50, self.width(), self.height())

    def on_updates_sent(self, num_sent):
        self.label.setText(
            f'All done! {num_sent} newly tagged Mint transactions')
        self.cancel_button.setText('Close')

    def on_open_unmatched(self, unmatched):
        self.unmatched_dialog = AmazonUnmatchedTableDialog(unmatched)
        self.unmatched_dialog.show()

    def on_open_amazon_stats(self, items, charges, refunds):
        self.amazon_stats_dialog = AmazonStatsDialog(items, charges, refunds)
        self.amazon_stats_dialog.show()

    def on_open_tagger_stats(self, stats):
        self.tagger_stats_dialog = TaggerStatsDialog(stats)
        self.tagger_stats_dialog.show()

    def on_send(self):
        self.progress_bar.show()
        updates = self.updates_table_model.get_selected_updates()

        self.confirm_button.hide()
        self.updates_table.hide()
        self.confirm_button.deleteLater()
        self.updates_table.deleteLater()
        self.adjustSize()

        QMetaObject.invokeMethod(
            self.worker, 'send_updates', Qt.ConnectionType.QueuedConnection,
            Q_ARG(list, updates),
            Q_ARG(object, self.args))

    def on_stopped(self):
        QMetaObject.invokeMethod(
            self.worker, 'close_webdriver', Qt.ConnectionType.QueuedConnection)
        self.close()

    def on_progress(self, msg, max, value):
        self.label.setText(msg)
        self.progress_bar.setRange(0, max)
        self.progress_bar.setValue(value)

    def on_cancel(self):
        if not self.reviewing:
            QMetaObject.invokeMethod(
                self.worker, 'stop', Qt.ConnectionType.QueuedConnection)
        else:
            self.on_stopped()

    def on_mfa(self):
        mfa_code, ok = QInputDialog().getText(
            self, 'Please enter your MFA/OTP Code.',
            'Code:')
        self.worker.mfa_code = mfa_code
        QMetaObject.invokeMethod(
            self.worker, 'mfa_code', Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, mfa_code))
        self.worker.on_mfa_done.emit()


class TaggerWorker(QObject):
    """This class is required to prevent locking up the main Qt thread."""
    on_error = pyqtSignal(str)
    on_review_ready = pyqtSignal(tagger.UpdatesResult)
    on_updates_sent = pyqtSignal(int)
    on_stopped = pyqtSignal()
    on_mfa = pyqtSignal()
    on_mfa_done = pyqtSignal()
    on_progress = pyqtSignal(str, int, int)
    stopping = False
    webdriver = None

    @ pyqtSlot()
    def stop(self):
        self.stopping = True

    @ pyqtSlot(str)
    def mfa_code(self, code):
        logger.info(code)
        self.mfa_code = code

    @ pyqtSlot(object)
    def create_updates(self, args, parent):
        try:
            self.do_create_updates(args, parent)
        except Exception as e:
            msg = f'Internal error while creating updates: {e}'
            self.on_error.emit(msg)
            logger.exception(msg)

    @ pyqtSlot(list, object)
    def send_updates(self, updates, args):
        try:
            self.do_send_updates(updates, args)
        except Exception as e:
            msg = f'Internal error while sending updates: {e}'
            self.on_error.emit(msg)
            logger.exception(msg)

    @ pyqtSlot()
    def close_webdriver(self):
        if self.webdriver:
            self.webdriver.close()
            self.webdriver = None

    def get_webdriver(self, args):
        if self.webdriver:
            logger.info('Using existing webdriver')
            return self.webdriver
        logger.info('Creating a new webdriver')
        self.webdriver = get_webdriver(args.headless, args.session_path)
        return self.webdriver

    def do_create_updates(self, args, parent):
        def on_mfa(prompt):
            logger.info('Asking for MFA/OTP')
            self.on_mfa.emit()
            loop = QEventLoop()
            self.on_mfa_done.connect(loop.quit)
            loop.exec_()
            logger.info(self.mfa_code)
            return self.mfa_code

        # Factory that handles indeterminite, determinite, and counter style.
        def progress_factory(msg, max=0):
            return QtProgress(msg, max, self.on_progress.emit)

        atexit.register(self.close_webdriver)

        bound_webdriver_factory = partial(self.get_webdriver, args)
        self.mint_client = MintClient(
            args,
            bound_webdriver_factory,
            mfa_input_callback=on_mfa)

        results = tagger.create_updates(
            args, self.mint_client,
            on_critical=self.on_error.emit,
            indeterminate_progress_factory=progress_factory,
            determinate_progress_factory=progress_factory,
            counter_progress_factory=progress_factory)

        if results.success and not self.stopping:
            self.on_review_ready.emit(results)

        if self.stopping:
            self.close_webdriver()

    def do_send_updates(self, updates, args):
        num_updates = self.mint_client.send_updates(
            updates,
            progress=QtProgress(
                'Sending updates to Mint',
                len(updates),
                self.on_progress.emit),
            ignore_category=args.no_tag_categories)
        self.close_webdriver()
        self.on_updates_sent.emit(num_updates)


def main():
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(logging.StreamHandler())
    # Disable noisy log spam from filelock from within tldextract.
    logging.getLogger("filelock").setLevel(logging.WARN)
    # For helping remote debugging, also log to file.
    # Developers should be vigilant to NOT log any PII, ever (including being
    # mindful of what exceptions might be thrown).
    log_directory = os.path.join(TAGGER_BASE_PATH, 'Tagger Logs')
    os.makedirs(log_directory, exist_ok=True)
    log_filename = os.path.join(
        log_directory, f'{time.strftime("%Y-%m-%d_%H-%M-%S")}.log')
    file_handler = logging.FileHandler(log_filename)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s: %(message)s'))
    file_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)

    parser = argparse.ArgumentParser(
        description='Tag Mint transactions based on itemized Amazon history.')
    define_gui_args(parser)
    args = parser.parse_args()

    def sigint_handler(signal, frame):
        logger.warning('Keyboard interrupt caught')
        QApplication.quit()
        sys.exit(0)

    signal(SIGINT, sigint_handler)
    sys.exit(TaggerGui(args, get_name_to_help_dict(
        parser), log_filename).create_gui())


if __name__ == '__main__':
    main()
