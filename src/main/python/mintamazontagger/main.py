#!/usr/bin/env python3

# This script fetches Amazon "Order History Reports" and annotates your Mint
# transactions based on actual items in each purchase. It can handle orders
# that are split into multiple shipments/charges, and can even itemized each
# transaction for maximal control over categorization.

from collections import Counter
import datetime
from functools import partial
import logging
import pickle
import os
import sys

from PyQt5.QtCore import (
    Q_ARG, QDate, Qt, QMetaObject, QObject, QThread, pyqtSlot, pyqtSignal)
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QCalendarWidget, QCheckBox,
    QComboBox, QDialog, QErrorMessage, QFileDialog,
    QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QProgressBar,
    QPushButton, QShortcut, QTableView, QWidget, QVBoxLayout)
from outdated import check_outdated

from mintamazontagger import amazon
from mintamazontagger import mint
from mintamazontagger import tagger
from mintamazontagger import VERSION
from mintamazontagger.qt import (
    MintUpdatesTableModel, AmazonUnmatchedTableDialog, AmazonStatsDialog,
    TaggerStatsDialog)
from mintamazontagger.orderhistory import fetch_order_history
from mintamazontagger.mintclient import MintClient

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)

NEVER_SAVE_MSG = 'Email & password are *never* saved.'


class Args:
    def __init__(self, **kwds):
        self.__dict__.update(kwds)


class TaggerGui:
    def __init__(self):
        home = os.path.expanduser("~")
        # This dict's keys exactly match the argparse names/options in cli.
        # This dict is then made to act like an Args object.
        self.args = {
            'session_path': os.path.join(
                home, '.minttagger', 'session'),
            'report_download_location': os.path.join(
                home, 'AMZN Reports'),
            'headless': False,

            'amazon_email': '',
            'amazon_password': '',
            'order_history_start_date': (
                datetime.date.today() - datetime.timedelta(days=90)),
            'order_history_end_date': datetime.date.today(),
            'orders_csv': None,
            'items_csv': None,
            'refunds_csv': None,
            'amazon_domains': (
                'amazon.com,amazon.cn,amazon.in,amazon.co.jp,'
                'amazon.com.sg,amazon.com.tr,amazon.fr,amazon.de,'
                'amazon.it,amazon.nl,amazon.es,amazon.co.uk,amazon.ca,'
                'amazon.com.mx,amazon.com.au,amazon.com.br'),

            'mint_email': '',
            'mint_password': '',
            'mint_mfa_method': 'sms',
            'wait_for_sync': False,
            'mint_input_merchant_filter': 'amazon,amzn',
            'mint_input_categories_filter': '',
            'mint_input_include_mmerchant': False,
            'mint_input_include_merchant': False,

            'verbose_itemize': False,
            'no_itemize': False,

            'description_prefix_override': '',
            'description_return_prefix_override': '',
            'num_updates': 0,  # Unlimited
            'prompt_retag': False,
            'retag_changed': False,
            'no_tag_categories': False,
            'do_not_predict_categories': False,
            'max_days_between_payment_and_shipping': 3,
        }

    def create_gui(self):
        try:
            from fbs_runtime.application_context.PyQt5 import (
                ApplicationContext)
            appctxt = ApplicationContext()
            app = appctxt.app
        except ImportError:
            app = QApplication()
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
        amazon_layout = QVBoxLayout()

        amazon_mode = QComboBox()
        amazon_mode.addItem('Fetch Reports')
        amazon_mode.addItem('Use Local Reports')

        self.amazon_mode_layout = self.create_amazon_fetch_layout()

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
        mint_layout = QFormLayout()

        mint_layout.addRow(
            'Email:',
            self.create_line_edit(
                'mint_email', tool_tip=NEVER_SAVE_MSG))
        mint_layout.addRow(
            'Password:',
            self.create_line_edit(
                'mint_password', tool_tip=NEVER_SAVE_MSG))
        mint_layout.addRow(
            'MFA Code:',
            self.create_combobox(
                'mint_mfa_method',
                ['SMS', 'Email'],
                lambda x: x.lower(),
                tool_tip='The Mint MFA method (2factor auth codes).'))
        mint_layout.addRow(
            'Sync first?',
            self.create_checkbox(
                'wait_for_sync',
                tool_tip=(
                    'By default, do not wait for accounts to sync with the \n'
                    'backing financial institutions. If this flag is \n'
                    'present, instead wait for them to sync, up to 5 \n'
                    'minutes.')))

        mint_layout.addRow(
            'Merchant Filter',
            self.create_line_edit(
                'mint_input_merchant_filter',
                tool_tip=(
                    'Only consider Mint transactions that have one of these \n'
                    'strings in the merchant field. Case-insensitive \n'
                    'comma-separated.')))
        mint_layout.addRow(
            'Include MMerchant',
            self.create_checkbox(
                'mint_input_include_mmerchant',
                tool_tip=(
                    'If set, consider using the mmerchant field when \n'
                    'determining if a transaction is an Amazon purchase. \n'
                    'This can be necessary when your bank renames \n'
                    'transactions to "Debit card payment". Mint sometimes \n'
                    'auto-recovers these into "Amazon", and flipping this \n'
                    'flag will help match these. To know if you should use \n'
                    'it, find a transaction in the Mint tool, and click on \n'
                    'the details. Look for "Appears on your BANK ACCOUNT \n'
                    'NAME statement as NOT USEFUL NAME on DATE".')))
        mint_layout.addRow(
            'Include Merchant',
            self.create_checkbox(
                'mint_input_include_merchant',
                tool_tip=(
                    'If set, consider using the merchant field when \n'
                    'determining if a transaction is an Amazon purchase. \n'
                    'This is similar to --mint_input_include_mmerchant but \n'
                    'also includes any user edits to the transaction name.')))
        mint_layout.addRow(
            'Input Categories Filter',
            self.create_line_edit(
                'mint_input_categories_filter',
                tool_tip=(
                    'Only consider Mint transactions that have one of \n'
                    'these strings in the merchant field. Case-insensitive \n'
                    'comma-separated.')))
        mint_group.setLayout(mint_layout)
        h_layout.addWidget(mint_group)

        tagger_group = QGroupBox('Tagger Options')
        tagger_layout = QHBoxLayout()
        tagger_left = QFormLayout()

        tagger_left.addRow(
            'Verbose Itemize',
            self.create_checkbox(
                'verbose_itemize',
                tool_tip=(
                    'When unchecked, the behavior is to not itemize out \n'
                    'shipping/promos/etc if there is only one item per \n'
                    'Mint transaction. Will also remove free shipping. \n'
                    'Check this to itemize everything.')))
        tagger_left.addRow(
            'Do not Itemize',
            self.create_checkbox(
                'no_itemize',
                tool_tip=(
                    'When checked, Do not split Mint transactions into \n'
                    'individual items with attempted categorization.')))
        tagger_left.addRow(
            'Retag Changed',
            self.create_checkbox(
                'retag_changed',
                tool_tip=(
                    'When checked, for transactions that have been \n'
                    'previously tagged by this script, override any edits \n'
                    '(like adjusting the category). This feature works by \n'
                    'looking for "Amazon.com: " at the start of a \n'
                    'transaction. If the user changes the description, then \n'
                    'the tagger won\'t know to leave it alone.')))

        tagger_right = QFormLayout()
        tagger_right.addRow(
            'Do not tag categories',
            self.create_checkbox(
                'no_tag_categories',
                tool_tip=(
                    'If checked, do not update Mint categories. This is \n'
                    'useful as Amazon doesn\'t provide the best \n'
                    'categorization and it is pretty common user behavior \n'
                    'to manually change the categories. This flag prevents \n'
                    'tagger from wiping out that user work.')))
        tagger_right.addRow(
            'Do not predict categories',
            self.create_checkbox(
                'do_not_predict_categories',
                tool_tip=(
                    'If checked, do not attempt to predict custom category \n'
                    'tagging based on any tagging overrides. By default \n'
                    '(no arg) tagger will attempt to find items that you \n'
                    'have manually changed categories for.')))
        tagger_right.addRow(
            'Max days between payment/shipment',
            self.create_combobox(
                'max_days_between_payment_and_shipping',
                ['3', '4', '5', '6', '7', '8', '9', '10'],
                lambda x: int(x),
                tool_tip=(
                    'How many days are allowed to pass between when Amazon \n'
                    'has shipped an order and when the payment has posted \n'
                    'to your bank account (as per Mint\'s view).')))

        tagger_layout.addLayout(tagger_left)
        tagger_layout.addLayout(tagger_right)
        tagger_group.setLayout(tagger_layout)
        v_layout.addWidget(tagger_group)

        self.start_button = QPushButton('Start Tagging')
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
            'Email:', self.create_line_edit('amazon_email'))
        amazon_fetch_layout.addRow(
            'Password:', self.create_line_edit('amazon_password'))
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
            'Orders CSV:',
            self.create_file_edit(
                'orders_csv',
                'Select Amazon Orders Report'
            ))
        amazon_import_layout.addRow(
            'Items CSV:',
            self.create_file_edit(
                'items_csv',
                'Select Amazon Items Report'
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

    def on_start_button_clicked(self):
        self.start_button.setEnabled(False)
        self.tagger = TaggerDialog(
            args=Args(**self.args),
            parent=self.window)
        self.tagger.show()
        self.tagger.finished.connect(
            lambda x: self.start_button.setEnabled(True))

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
            Qt.Checked if self.args[name] else Qt.Unchecked)
        if tool_tip:
            x_box.setToolTip(tool_tip)

        def on_changed(state):
            self.args[name] = (
                state != Qt.Checked if invert else state == Qt.Checked)
        x_box.stateChanged.connect(on_changed)
        return x_box

    def create_line_edit(self, name, tool_tip=None):
        line_edit = QLineEdit(self.args[name])
        if tool_tip:
            line_edit.setToolTip(tool_tip)

        def on_changed(state):
            self.args[name] = state
        line_edit.textChanged.connect(on_changed)
        return line_edit

    def create_date_edit(
            self, name, popup_title, max_date=datetime.date.today(),
            tool_tip=None):
        date_edit = QPushButton(str(self.args[name]))
        if tool_tip:
            date_edit.setToolTip(tool_tip)

        def on_date_edit_clicked():
            dlg = QDialog(self.window)
            dlg.setWindowTitle(popup_title)
            layout = QVBoxLayout()
            cal = QCalendarWidget()
            cal.setMaximumDate(QDate(max_date))
            cal.setSelectedDate(QDate(self.args[name]))
            cal.selectionChanged.connect(lambda: dlg.accept())
            layout.addWidget(cal)
            okay = QPushButton('Select')
            okay.clicked.connect(lambda: dlg.accept())
            layout.addWidget(okay)
            dlg.setLayout(layout)
            dlg.exec()

            self.args[name] = cal.selectedDate().toPyDate()
            date_edit.setText(str(self.args[name]))

        date_edit.clicked.connect(on_date_edit_clicked)
        return date_edit

    def create_file_edit(
            self, name, popup_title, filter='CSV files (*.csv)',
            tool_tip=None):
        file_button = QPushButton(
            'Select a file' if not self.args[name]
            else os.path.split(self.args[name])[1])

        if tool_tip:
            file_button.setToolTip(tool_tip)

        def on_button():
            dlg = QFileDialog()
            selection = dlg.getOpenFileName(
                self.window, popup_title, '', filter)
            if selection[0]:
                if self.args[name]:
                    self.args[name].close()
                self.args[name] = open(selection[0], 'r')
                file_button.setText(os.path.split(selection[0])[1])
            elif not self.amazon_orders_csv_path:
                self.args[name] = None
                file_button.setText('Select a file')

        file_button.clicked.connect(on_button)
        return file_button

    def create_combobox(self, name, items, transform, tool_tip=None):
        combo = QComboBox()
        if tool_tip:
            combo.setToolTip(tool_tip)
        combo.addItems(items)

        def on_change(option):
            self.args[name] = transform(option)
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

        self.thread.started.connect(partial(self.worker.create_updates, args))
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
        self.label.setText('Error: {}'.format(msg))
        self.label.setStyleSheet(
            'QLabel { color: red; font-weight: bold; }')
        self.cancel_button.setText('Close')
        self.cancel_button.clicked.connect(self.close)

    def on_review_ready(
            self, updates, unmatched_orders, items, orders, refunds, stats):
        self.reviewing = True
        self.progress_bar.hide()

        self.label.setText('Select below which updates to send to Mint.')

        self.updates_table_model = MintUpdatesTableModel(updates)
        self.updates_table = QTableView()

        def resize():
            self.updates_table.resizeColumnsToContents()
            self.updates_table.resizeRowsToContents()

        self.updates_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.updates_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.updates_table.setModel(self.updates_table_model)
        self.updates_table.setSortingEnabled(True)
        self.updates_table.setMinimumSize(700, 400)
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


class TaggerWorker(QObject):
    on_error = pyqtSignal(str)
    on_review_ready = pyqtSignal(list, list, list, list, list, dict)
    on_updates_sent = pyqtSignal(int)
    on_stopped = pyqtSignal()
    on_progress = pyqtSignal(str, int, int)
    stopping = False

    @pyqtSlot()
    def stop(self):
        self.stopping = True

    @pyqtSlot(object)
    def create_updates(self, args):
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
                progress_factory=lambda x: self.on_progress.emit(x, 0, 0))

        if not items_csv or not orders_csv:  # Refunds are optional
            self.on_error.emit(
                'Order history either not provided at or '
                'unable to fetch. Exiting.')
            return

        self.on_progress.emit('Parse Amazon order history', 0, 0)
        orders = amazon.Order.parse_from_csv(orders_csv)
        items = amazon.Item.parse_from_csv(items_csv)
        refunds = ([] if not refunds_csv
                   else amazon.Refund.parse_from_csv(refunds_csv))

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

        if not args.mint_email or not args.mint_password:
            self.on_error.emit('Missing Mint email or password. Try again')
            return

        self.on_progress.emit(
            'Logging into Mint', 0, 0)
        self.mint_client = MintClient(
            args.mint_email, args.mint_password,
            args.session_path, False,
            args.mint_mfa_method, args.wait_for_sync)

        # Get the date of the oldest Amazon order.
        if not start_date:
            start_date = min([o.order_date for o in orders])
            if refunds:
                start_date = min(
                    start_date,
                    min([o.order_date for o in refunds]))

        # Double the length of transaction history to help aid in
        # personalized category tagging overrides.
        today = datetime.date.today()
        start_date = today - (today - start_date) * 2
        self.on_progress.emit('Getting Mint Categories', 0, 0)
        mint_category_name_to_id = self.mint_client.get_categories()
        self.on_progress.emit('Getting Mint Transactions', 0, 0)
        mint_transactions_json = self.mint_client.get_transactions(start_date)

        mint_trans = mint.Transaction.parse_from_json(mint_transactions_json)
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

    @pyqtSlot(list, object)
    def send_updates(self, updates, args):
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


MINT_TRANS_PICKLE_FMT = 'Mint {} Transactions.pickle'
MINT_CATS_PICKLE_FMT = 'Mint {} Categories.pickle'


def get_trans_and_categories_from_pickle(pickle_epoch, pickle_base_path):
    trans_pickle_path = os.path.join(
        pickle_base_path, MINT_TRANS_PICKLE_FMT.format(pickle_epoch))
    cats_pickle_path = os.path.join(
        pickle_base_path, MINT_CATS_PICKLE_FMT.format(pickle_epoch))
    with open(trans_pickle_path, 'rb') as f:
        trans = pickle.load(f)
    with open(cats_pickle_path, 'rb') as f:
        cats = pickle.load(f)

    return trans, cats


def main():
    sys.exit(TaggerGui().create_gui())


if __name__ == '__main__':
    main()
