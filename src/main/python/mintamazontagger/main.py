#!/usr/bin/env python3

# This script fetches Amazon "Order History Reports" and annotates your Mint
# transactions based on actual items in each purchase. It can handle orders
# that are split into multiple shipments/charges, and can even itemized each
# transaction for maximal control over categorization.

import argparse
from collections import Counter
import datetime
from functools import partial
import logging
import os
import sys
from threading import Condition
import time

from PyQt5.QtCore import (
    Q_ARG, QDate, Qt, QMetaObject, QObject, QThread, QUrl, pyqtSlot,
    pyqtSignal)
from PyQt5.QtGui import QDesktopServices, QKeySequence
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QCalendarWidget,
    QCheckBox, QComboBox, QDialog, QErrorMessage, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QProgressBar,
    QPushButton, QShortcut, QTableView, QWidget, QVBoxLayout)
from outdated import check_outdated

from mintamazontagger import amazon
from mintamazontagger import mint
from mintamazontagger import tagger
from mintamazontagger import VERSION
from mintamazontagger.args import define_gui_args, get_name_to_help_dict
from mintamazontagger.qt import (
    MintUpdatesTableModel, AmazonUnmatchedTableDialog, AmazonStatsDialog,
    TaggerStatsDialog)
from mintamazontagger.mint import (
    get_trans_and_categories_from_pickle, dump_trans_and_categories)
from mintamazontagger.mintclient import MintClient
from mintamazontagger.orderhistory import fetch_order_history

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

NEVER_SAVE_MSG = 'Email & password are *never* saved.'


class TaggerGui:
    def __init__(self, args, arg_name_to_help):
        self.args = args
        self.arg_name_to_help = arg_name_to_help

    def create_gui(self):
        try:
            from fbs_runtime.application_context.PyQt5 import (
                ApplicationContext)
            appctxt = ApplicationContext()
            app = appctxt.app
        except ImportError:
            app = QApplication(sys.argv)
        app.setStyle('Fusion')
        self.window = QMainWindow()

        self.quit_shortcuts = []
        for seq in ("Ctrl+Q", "Ctrl+C", "Ctrl+W", "ESC"):
            s = QShortcut(QKeySequence(seq), self.window)
            s.activated.connect(app.exit)
            self.quit_shortcuts.append(s)

        is_outdated, latest_version = check_outdated(
            'mint-amazon-tagger', VERSION)
        if is_outdated:
            outdate_msg = QErrorMessage(self.window)
            outdate_msg.showMessage(
                'A new version is available. Please update for the best '
                'experience. https://github.com/jprouty/mint-amazon-tagger')

        v_layout = QVBoxLayout()
        h_layout = QHBoxLayout()
        v_layout.addLayout(h_layout)

        amazon_group = QGroupBox('Amazon Order History')
        amazon_group.setMinimumWidth(300)
        amazon_layout = QVBoxLayout()

        amazon_mode = QComboBox()
        amazon_mode.addItem('Fetch Reports')
        amazon_mode.addItem('Use Local Reports')
        amazon_mode.setFocusPolicy(Qt.StrongFocus)

        has_csv = any([
            self.args.orders_csv, self.args.items_csv, self.args.refunds_csv])
        self.amazon_mode_layout = (
            self.create_amazon_import_layout()
            if has_csv else self.create_amazon_fetch_layout())
        amazon_mode.setCurrentIndex(1 if has_csv else 0)

        def on_amazon_mode_changed(i):
            self.clear_layout(self.amazon_mode_layout)
            if i == 0:
                self.amazon_mode_layout = self.create_amazon_fetch_layout()
            elif i == 1:
                self.amazon_mode_layout = self.create_amazon_import_layout()
            amazon_layout.addLayout(self.amazon_mode_layout)
        amazon_mode.currentIndexChanged.connect(
            on_amazon_mode_changed)

        amazon_layout.addWidget(amazon_mode)
        amazon_layout.addLayout(self.amazon_mode_layout)
        amazon_group.setLayout(amazon_layout)
        h_layout.addWidget(amazon_group)

        mint_group = QGroupBox('Mint Login && Options')
        mint_group.setMinimumWidth(350)
        mint_layout = QFormLayout()

        mint_layout.addRow(
            'Email:',
            self.create_line_edit('mint_email', tool_tip=NEVER_SAVE_MSG))
        mint_layout.addRow(
            'Password:',
            self.create_line_edit(
                'mint_password', tool_tip=NEVER_SAVE_MSG, password=True))
        mint_layout.addRow(
            'MFA Code:',
            self.create_combobox(
                'mint_mfa_method',
                ['SMS', 'Email'],
                lambda x: x.lower()))
        mint_layout.addRow(
            'Sync first?',
            self.create_checkbox('mint_wait_for_sync'))

        mint_layout.addRow(
            'Merchant Filter',
            self.create_line_edit('mint_input_merchant_filter'))
        mint_layout.addRow(
            'Include MMerchant',
            self.create_checkbox('mint_input_include_mmerchant'))
        mint_layout.addRow(
            'Include Merchant',
            self.create_checkbox('mint_input_include_merchant'))
        mint_layout.addRow(
            'Input Categories Filter',
            self.create_line_edit('mint_input_categories_filter'))
        mint_group.setLayout(mint_layout)
        h_layout.addWidget(mint_group)

        tagger_group = QGroupBox('Tagger Options')
        tagger_layout = QHBoxLayout()
        tagger_left = QFormLayout()

        tagger_left.addRow(
            'Verbose Itemize',
            self.create_checkbox('verbose_itemize'))
        tagger_left.addRow(
            'Do not Itemize',
            self.create_checkbox('no_itemize'))
        tagger_left.addRow(
            'Retag Changed',
            self.create_checkbox('retag_changed'))

        tagger_right = QFormLayout()
        tagger_right.addRow(
            'Do not tag categories',
            self.create_checkbox('no_tag_categories'))
        tagger_right.addRow(
            'Do not predict categories',
            self.create_checkbox('do_not_predict_categories'))
        tagger_right.addRow(
            'Max days between payment/shipment',
            self.create_combobox(
                'max_days_between_payment_and_shipping',
                ['3', '4', '5', '6', '7', '8', '9', '10'],
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
        return app.exec_()

    def create_amazon_fetch_layout(self):
        amazon_fetch_layout = QFormLayout()
        amazon_fetch_layout.addRow(QLabel(
            'Fetches recent Amazon order history for you.'))
        amazon_fetch_layout.addRow(
            'Email:',
            self.create_line_edit('amazon_email', tool_tip=NEVER_SAVE_MSG))
        amazon_fetch_layout.addRow(
            'Password:',
            self.create_line_edit(
                'amazon_password', tool_tip=NEVER_SAVE_MSG, password=True))
        amazon_fetch_layout.addRow(
            'Start date:',
            self.create_date_edit(
                'order_history_start_date',
                'Select Amazon order history start date'))
        amazon_fetch_layout.addRow(
            'End date:',
            self.create_date_edit(
                'order_history_end_date',
                'Select Amazon order history end date'))
        return amazon_fetch_layout

    def create_amazon_import_layout(self):
        amazon_import_layout = QFormLayout()

        order_history_link = QLabel()
        order_history_link.setText(
            '''<a href="https://www.amazon.com/gp/b2b/reports">
            Download your Amazon reports</a><br>
            and select them below:''')
        order_history_link.setOpenExternalLinks(True)
        amazon_import_layout.addRow(order_history_link)

        amazon_import_layout.addRow(
            'Items CSV:',
            self.create_file_edit(
                'items_csv',
                'Select Amazon Items Report'
            ))
        amazon_import_layout.addRow(
            'Orders CSV:',
            self.create_file_edit(
                'orders_csv',
                'Select Amazon Orders Report'
            ))
        amazon_import_layout.addRow(
            'Refunds CSV:',
            self.create_file_edit(
                'refunds_csv',
                'Select Amazon Refunds Report'
            ))
        return amazon_import_layout

    def on_quit(self):
        pass

    def on_tagger_dialog_closed(self):
        self.start_button.setEnabled(True)
        # Reset any csv file handles, as there might have been an error and
        # they user may try again (could already be consumed/closed).
        for attr_name in ('orders_csv', 'items_csv', 'refunds_csv'):
            file = getattr(self.args, attr_name)
            if file:
                setattr(
                    self.args,
                    attr_name,
                    open(file.name, 'r', encoding='utf-8'))

    def on_start_button_clicked(self):
        self.start_button.setEnabled(False)
        self.tagger = TaggerDialog(
            args=self.args,
            parent=self.window)
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
            Qt.Checked if getattr(self.args, name) else Qt.Unchecked)
        if not tool_tip and name in self.arg_name_to_help:
            tool_tip = 'When checked, ' + self.arg_name_to_help[name]
        if tool_tip:
            x_box.setToolTip(tool_tip)

        def on_changed(state):
            setattr(
                self.args, name,
                state != Qt.Checked if invert else state == Qt.Checked)
        x_box.stateChanged.connect(on_changed)
        return x_box

    def advance_focus(self):
        self.window.focusNextChild()

    def create_line_edit(self, name, tool_tip=None, password=False):
        line_edit = QLineEdit(getattr(self.args, name))
        if not tool_tip:
            tool_tip = self.arg_name_to_help[name]
        if tool_tip:
            line_edit.setToolTip(tool_tip)
        if password:
            line_edit.setEchoMode(QLineEdit.PasswordEchoOnEdit)

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
            self, name, popup_title, filter='CSV files (*.csv)',
            tool_tip=None):
        file_button = QPushButton(
            'Select a file' if not getattr(self.args, name)
            else os.path.split(getattr(self.args, name).name)[1])

        if not tool_tip:
            tool_tip = self.arg_name_to_help[name]
        if tool_tip:
            file_button.setToolTip(tool_tip)

        def on_button():
            dlg = QFileDialog()
            selection = dlg.getOpenFileName(
                self.window, popup_title, '', filter)
            if selection[0]:
                prev_file = getattr(self.args, name)
                if prev_file:
                    prev_file.close()
                setattr(
                    self.args,
                    name,
                    open(selection[0], 'r', encoding='utf-8'))
                file_button.setText(os.path.split(selection[0])[1])

        file_button.clicked.connect(on_button)
        return file_button

    def create_combobox(self, name, items, transform, tool_tip=None):
        combo = QComboBox()
        combo.setFocusPolicy(Qt.StrongFocus)
        if not tool_tip:
            tool_tip = self.arg_name_to_help[name]
        if tool_tip:
            combo.setToolTip(tool_tip)
        combo.addItems(items)

        def on_change(option):
            setattr(self.args, name, transform(option))
        combo.currentTextChanged.connect(on_change)
        return combo


class TaggerDialog(QDialog):
    def __init__(self, args, **kwargs):
        super(TaggerDialog, self).__init__(**kwargs)

        self.reviewing = False
        self.args = args

        self.worker = TaggerWorker()
        self.thread = QThread()
        self.worker.moveToThread(self.thread)

        self.worker.on_error.connect(self.on_error)
        self.worker.on_review_ready.connect(self.on_review_ready)
        self.worker.on_stopped.connect(self.on_stopped)
        self.worker.on_progress.connect(self.on_progress)
        self.worker.on_updates_sent.connect(self.on_updates_sent)
        self.worker.on_mint_mfa.connect(self.on_mint_mfa)

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
        self.label.setText('Error: {}'.format(msg))
        self.label.setStyleSheet(
            'QLabel { color: red; font-weight: bold; }')
        self.cancel_button.setText('Close')
        self.cancel_button.clicked.connect(self.close)

    def open_amazon_order_id(self, order_id):
        if order_id:
            QDesktopServices.openUrl(QUrl(
                amazon.get_invoice_url(order_id)))

    def on_activated(self, index):
        # Only handle clicks on the order_id cell.
        if index.column() != 5:
            return
        order_id = self.updates_table_model.data(index, Qt.DisplayRole)
        self.open_amazon_order_id(order_id)

    def on_double_click(self, index):
        if index.column() == 5:
            # Ignore double clicks on the order_id cell.
            return
        order_id_cell = self.updates_table_model.createIndex(index.row(), 5)
        order_id = self.updates_table_model.data(order_id_cell, Qt.DisplayRole)
        self.open_amazon_order_id(order_id)

    def on_review_ready(
            self, updates, unmatched_orders, items, orders, refunds, stats):
        self.reviewing = True
        self.progress_bar.hide()

        self.label.setText('Select below which updates to send to Mint.')

        self.updates_table_model = MintUpdatesTableModel(updates)
        self.updates_table = QTableView()
        self.updates_table.doubleClicked.connect(self.on_double_click)
        self.updates_table.clicked.connect(self.on_activated)

        def resize():
            self.updates_table.resizeColumnsToContents()
            self.updates_table.resizeRowsToContents()
            min_width = sum(
                self.updates_table.columnWidth(i) for i in range(6))
            self.updates_table.setMinimumSize(min_width + 20, 600)

        self.updates_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.updates_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.updates_table.setModel(self.updates_table_model)
        self.updates_table.setSortingEnabled(True)
        resize()
        self.updates_table_model.layoutChanged.connect(resize)

        self.v_layout.insertWidget(2, self.updates_table)

        unmatched_button = QPushButton('View Unmatched Amazon orders')
        self.button_bar.addWidget(unmatched_button)
        unmatched_button.clicked.connect(
            partial(self.on_open_unmatched, unmatched_orders))

        amazon_stats_button = QPushButton('Amazon Stats')
        self.button_bar.addWidget(amazon_stats_button)
        amazon_stats_button.clicked.connect(
            partial(self.on_open_amazon_stats, items, orders, refunds))

        tagger_stats_button = QPushButton('Tagger Stats')
        self.button_bar.addWidget(tagger_stats_button)
        tagger_stats_button.clicked.connect(
            partial(self.on_open_tagger_stats, stats))

        self.confirm_button = QPushButton('Send to Mint')
        self.button_bar.addWidget(self.confirm_button)
        self.confirm_button.clicked.connect(self.on_send)

    def on_updates_sent(self, num_sent):
        self.label.setText(
            'All done! {} newly tagged Mint transactions'.format(num_sent))
        self.cancel_button.setText('Close')

    def on_open_unmatched(self, unmatched):
        self.unmatched_dialog = AmazonUnmatchedTableDialog(unmatched)
        self.unmatched_dialog.show()

    def on_open_amazon_stats(self, items, orders, refunds):
        self.amazon_stats_dialog = AmazonStatsDialog(items, orders, refunds)
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
            self.worker, 'send_updates', Qt.QueuedConnection,
            Q_ARG(list, updates),
            Q_ARG(object, self.args))

    def on_stopped(self):
        self.close()

    def on_progress(self, msg, max, value):
        self.label.setText(msg)
        self.progress_bar.setRange(0, max)
        self.progress_bar.setValue(value)

    def on_cancel(self):
        if not self.reviewing:
            QMetaObject.invokeMethod(
                self.worker, 'stop', Qt.QueuedConnection)
        else:
            self.close()

    def on_mint_mfa(self):
        mfa_code, ok = QInputDialog().getText(
            self, 'Please enter your Mint Code.',
            'Mint Code:')
        QMetaObject.invokeMethod(
            self.worker, 'mfa_code', Qt.QueuedConnection,
            Q_ARG(int, mfa_code))


class TaggerWorker(QObject):
    """This class is required to prevent locking up the main Qt thread."""
    on_error = pyqtSignal(str)
    on_review_ready = pyqtSignal(list, list, list, list, list, dict)
    on_updates_sent = pyqtSignal(int)
    on_stopped = pyqtSignal()
    on_mint_mfa = pyqtSignal()
    on_progress = pyqtSignal(str, int, int)
    stopping = False
    mfa_condition = Condition()

    @pyqtSlot()
    def stop(self):
        self.stopping = True

    @pyqtSlot(str)
    def mfa_code(self, code):
        print('Got code')
        print(code)
        self.mfa_code = code
        print('Waking thread')
        self.mfa_condition.notify()

    @pyqtSlot(object)
    def create_updates(self, args, parent):
        try:
            self.do_create_updates(args, parent)
        except Exception as e:
            msg = 'Internal error while creating updates: {}'.format(e)
            self.on_error.emit(msg)
            logger.exception(msg)

    @pyqtSlot(list, object)
    def send_updates(self, updates, args):
        try:
            self.do_send_updates(updates, args)
        except Exception as e:
            msg = 'Internal error while sending updates: {}'.format(e)
            self.on_error.emit(msg)
            logger.exception(msg)

    def do_create_updates(self, args, parent):
        items_csv = args.items_csv
        orders_csv = args.orders_csv
        refunds_csv = args.refunds_csv

        start_date = None

        if not items_csv or not orders_csv:
            start_date = args.order_history_start_date
            end_date = args.order_history_end_date
            if not args.amazon_email or not args.amazon_password:
                self.on_error.emit(
                    'Amazon email or password is empty. '
                    'Please try again')
                return

            items_csv, orders_csv, refunds_csv = fetch_order_history(
                args.report_download_location, start_date, end_date,
                args.amazon_email, args.amazon_password,
                args.session_path, args.headless,
                progress_factory=lambda msg, max: Progress(
                    msg, max, self.on_progress.emit))

        if not items_csv or not orders_csv:  # Refunds are optional
            self.on_error.emit(
                'Order history either not provided at or '
                'unable to fetch. Exiting.')
            return

        self.on_progress.emit('Parse Amazon order history', 0, 0)
        try:
            orders = amazon.Order.parse_from_csv(orders_csv)
            items = amazon.Item.parse_from_csv(items_csv)
            refunds = ([] if not refunds_csv
                       else amazon.Refund.parse_from_csv(refunds_csv))
        except AttributeError as e:
            self.on_error.emit(
                'Error while parsing Amazon Order history report CSV files: '
                '{}'.format(e))
            return

        if not len(orders):
            self.on_error.emit(
                'The Orders report contains no data. Try '
                'downloading again. Report used: {}'.format(
                    orders_csv))
            return
        if not len(items):
            self.on_error.emit(
                'The Items report contains no data. Try '
                'downloading again. Report used: {}'.format(
                    items_csv))
            return

        if self.stopping:
            print(self.stopped)
            self.on_stopped.emit()
            return

        # Initialize the stats. Explicitly initialize stats that might not be
        # accumulated (conditionals).
        stats = Counter(
            adjust_itemized_tax=0,
            already_up_to_date=0,
            misc_charge=0,
            new_tag=0,
            no_retag=0,
            retag=0,
            user_skipped_retag=0,
            personal_cat=0,
        )

        if not args.pickled_epoch and (
                not args.mint_email or not args.mint_password):
            self.on_error.emit('Missing Mint email or password. Try again')
            return

        def on_mint_mfa(prompt):
            print('Asking for Mint MFA')
            self.on_mint_mfa.emit()
            print('Blocking')
            self.mfa_condition.wait()
            print('got code!')
            print(self.mfa_code)
            return self.mfa_code

        self.mint_client = MintClient(
            email=args.mint_email,
            password=args.mint_password,
            session_path=args.session_path,
            headless=args.headless,
            mfa_method=args.mint_mfa_method,
            wait_for_sync=args.mint_wait_for_sync,
            mfa_input_callback=on_mint_mfa,
            progress_factory=lambda msg, max: Progress(
                msg, max, self.on_progress.emit))

        if args.pickled_epoch:
            self.on_progress.emit(
                'Un-pickling Mint transactions from epoch: {} '.format(
                    args.pickled_epoch), 0, 0)
            mint_trans, mint_category_name_to_id = (
                get_trans_and_categories_from_pickle(
                    args.pickled_epoch, args.mint_pickle_location))
        else:
            # Get the date of the oldest Amazon order.
            if not start_date:
                start_date = min([o.order_date for o in orders])
                if refunds:
                    start_date = min(
                        start_date,
                        min([o.order_date for o in refunds]))

            # Double the length of transaction history to help aid in
            # personalized category tagging overrides.
            # TODO: Revise this logic/date range.
            today = datetime.date.today()
            start_date = today - (today - start_date) * 2
            self.on_progress.emit('Getting Mint Categories', 0, 0)
            mint_category_name_to_id = self.mint_client.get_categories()
            self.on_progress.emit('Getting Mint Transactions', 0, 0)
            mint_transactions_json = self.mint_client.get_transactions(
                start_date)
            mint_trans = mint.Transaction.parse_from_json(
                mint_transactions_json)

            if args.save_pickle_backup:
                pickle_epoch = int(time.time())
                self.on_progress.emit(
                    'Backing up Mint to local pickle file, epoch: {} '.format(
                        pickle_epoch), 0, 0)
                dump_trans_and_categories(
                    mint_trans, mint_category_name_to_id, pickle_epoch,
                    args.mint_pickle_location)
                logger.info(
                    'Mint transactions saved to local pickle file, '
                    'epoch: {} '.format(pickle_epoch))

        if self.stopping:
            self.on_stopped.emit()
            return

        self.on_progress.emit(
            'Matching Amazon orders to Mint transactions', 0, 0)
        updates, unmatched_orders = tagger.get_mint_updates(
            orders, items, refunds,
            mint_trans,
            args, stats,
            mint_category_name_to_id,
            progress_factory=lambda msg, max: Progress(
                msg, max, self.on_progress.emit))

        self.on_review_ready.emit(
            updates, unmatched_orders, items, orders, refunds, dict(stats))

    def do_send_updates(self, updates, args):
        num_updates = self.mint_client.send_updates(
            updates,
            progress=Progress(
                'Sending updates to Mint',
                len(updates),
                self.on_progress.emit),
            ignore_category=args.no_tag_categories)

        self.on_updates_sent.emit(num_updates)
        self.mint_client.close()


class Progress:
    def __init__(self, msg, max, emitter):
        self.msg = msg
        self.curr = 0
        self.max = max
        self.emitter = emitter

        self.emitter(self.msg, self.max, self.curr)

    def next(self, incr=1):
        self.curr += incr
        self.emitter(self.msg, self.max, self.curr)

    def finish(self):
        pass


def main():
    root_logger = logging.getLogger()
    root_logger.addHandler(logging.StreamHandler())
    # For helping remote debugging, also log to file.
    # Developers should be vigilant to NOT log any PII, ever (including being
    # mindful of what exceptions might be thrown).
    home = os.path.expanduser("~")
    log_directory = os.path.join(home, 'Tagger Logs')
    os.makedirs(log_directory, exist_ok=True)
    log_filename = os.path.join(log_directory, '{}.log'.format(
        time.strftime("%Y-%m-%d_%H-%M-%S")))
    root_logger.addHandler(logging.FileHandler(log_filename))

    parser = argparse.ArgumentParser(
        description='Tag Mint transactions based on itemized Amazon history.')
    define_gui_args(parser)
    args = parser.parse_args()

    sys.exit(TaggerGui(args, get_name_to_help_dict(parser)).create_gui())


if __name__ == '__main__':
    main()
