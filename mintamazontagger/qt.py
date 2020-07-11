from collections import defaultdict
import operator

from PyQt5.QtCore import Qt, QAbstractTableModel, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QAbstractItemView, QDialog, QLabel, QPushButton, QTableView, QVBoxLayout)

from mintamazontagger import amazon
from mintamazontagger import mint
from mintamazontagger.currency import micro_usd_to_usd_string


class MintUpdatesTableModel(QAbstractTableModel):
    def __init__(self, updates, **kwargs):
        super(MintUpdatesTableModel, self).__init__(**kwargs)
        self.my_data = []
        for i, update in enumerate(updates):
            orig_trans, new_trans = update

            descriptions = []
            categories = []
            amounts = []

            if orig_trans.children:
                for trans in orig_trans.children:
                    descriptions.append('CURRENTLY: ' + trans.merchant)
                    categories.append(trans.category)
                    amounts.append(micro_usd_to_usd_string(trans.amount))
            else:
                descriptions.append('CURRENTLY: ' + orig_trans.merchant)
                categories.append(orig_trans.category)
                amounts.append(micro_usd_to_usd_string(orig_trans.amount))

            if len(new_trans) == 1:
                trans = new_trans[0]
                descriptions.append('PROPOSED: ' + trans.merchant)
                categories.append(trans.category)
                amounts.append(micro_usd_to_usd_string(trans.amount))
            else:
                for trans in reversed(new_trans):
                    descriptions.append('PROPOSED: ' + trans.merchant)
                    categories.append(trans.category)
                    amounts.append(micro_usd_to_usd_string(trans.amount))

            self.my_data.append([
                update,
                True,
                orig_trans.date.strftime('%Y/%m/%d'),
                '\n'.join(descriptions),
                '\n'.join(categories),
                '\n'.join(amounts),
                orig_trans.orders[0].order_id,
            ])

        self.header = [
            '',
            'Date',
            'Description',
            'Category',
            'Amount',
            'Amazon Order'
        ]

    def rowCount(self, parent):
        return len(self.my_data)

    def columnCount(self, parent):
        return len(self.header)

    def data(self, index, role):
        if not index.isValid():
            return None
        if index.column() == 0:
            value = ('' if self.my_data[index.row()][index.column() + 1]
                     else 'Skip')
        else:
            value = self.my_data[index.row()][index.column() + 1]
        if role == Qt.EditRole:
            return value
        elif role == Qt.DisplayRole:
            return value
        elif role == Qt.CheckStateRole:
            if index.column() == 0:
                return (
                    Qt.Checked if self.my_data[index.row()][index.column() + 1]
                    else Qt.Unchecked)

    def headerData(self, col, orientation, role):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.header[col]
        return None

    def sort(self, col, order):
        self.layoutAboutToBeChanged.emit()
        self.my_data = sorted(self.my_data, key=operator.itemgetter(col + 1))
        if order == Qt.DescendingOrder:
            self.my_data.reverse()
        self.layoutChanged.emit()

    def flags(self, index):
        if not index.isValid():
            return None
        if index.column() == 0:
            return (
                Qt.ItemIsEnabled | Qt.ItemIsSelectable |
                Qt.ItemIsUserCheckable)
        else:
            return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def setData(self, index, value, role):
        if not index.isValid():
            return False
        if role == Qt.CheckStateRole and index.column() == 0:
            self.my_data[index.row()][index.column() + 1] = value == Qt.Checked
        self.dataChanged.emit(index, index)
        return True

    def get_selected_updates(self):
        return [d[0] for d in self.my_data if d[1]]


class AmazonUnmatchedTableDialog(QDialog):
    def __init__(self, unmatched_orders, **kwargs):
        super(AmazonUnmatchedTableDialog, self).__init__(**kwargs)
        self.setWindowTitle('Unmatched Amazon Orders/Refunds')
        self.setModal(True)
        self.model = AmazonUnmatchedTableModel(unmatched_orders)
        v_layout = QVBoxLayout()
        self.setLayout(v_layout)

        label = QLabel(
            'Below are the {} Amazon Orders/Refunds which did not match a '
            'Mint transaction.'.format(len(unmatched_orders)))
        v_layout.addWidget(label)

        table = QTableView()
        table.doubleClicked.connect(self.on_double_click)
        table.clicked.connect(self.on_activated)

        def resize():
            table.resizeColumnsToContents()
            table.resizeRowsToContents()
            min_width = sum(
                table.columnWidth(i) for i in range(5))
            table.setMinimumSize(min_width + 20, 600)

        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setModel(self.model)
        table.setSortingEnabled(True)
        resize()
        self.model.layoutChanged.connect(resize)

        v_layout.addWidget(table)

        close_button = QPushButton('Close')
        v_layout.addWidget(close_button)
        close_button.clicked.connect(self.close)

    def open_amazon_order_id(self, order_id):
        if order_id:
            QDesktopServices.openUrl(QUrl(
                amazon.get_invoice_url(order_id)))

    def on_activated(self, index):
        # Only handle clicks on the order_id cell.
        if index.column() != 3:
            return
        order_id = self.model.data(index, Qt.DisplayRole)
        self.open_amazon_order_id(order_id)

    def on_double_click(self, index):
        if index.column() == 3:
            # Ignore double clicks on the order_id cell.
            return
        order_id_cell = self.model.createIndex(index.row(), 3)
        order_id = self.model.data(order_id_cell, Qt.DisplayRole)
        self.open_amazon_order_id(order_id)


class AmazonUnmatchedTableModel(QAbstractTableModel):
    def __init__(self, unmatched_orders, **kwargs):
        super(AmazonUnmatchedTableModel, self).__init__(**kwargs)

        self.header = [
            'Ship Date',
            'Proposed Mint Description',
            'Amount',
            'Order ID',
        ]
        self.my_data = []
        by_oid = defaultdict(list)
        for uo in unmatched_orders:
            by_oid[uo.order_id].append(uo)
        for unmatched_by_oid in by_oid.values():
            orders = [o for o in unmatched_by_oid if o.is_debit]
            refunds = [o for o in unmatched_by_oid if not o.is_debit]
            if orders:
                merged = amazon.Order.merge(orders)
                self.my_data.append(self._create_row(merged))
            for r in amazon.Refund.merge(refunds):
                self.my_data.append(self._create_row(r))

    def _create_row(self, amzn_obj):
        proposed_mint_desc = mint.summarize_title(
            [i.get_title() for i in amzn_obj.items]
            if amzn_obj.is_debit else [amzn_obj.get_title()],
            '{}{}: '.format(
                amzn_obj.website, '' if amzn_obj.is_debit else ' refund'))
        return [
            amzn_obj.transact_date().strftime('%m/%d/%y')
            if amzn_obj.transact_date()
            else 'Never shipped!',
            proposed_mint_desc,
            micro_usd_to_usd_string(amzn_obj.transact_amount()),
            amzn_obj.order_id,
        ]

    def rowCount(self, parent):
        return len(self.my_data)

    def columnCount(self, parent):
        return len(self.header)

    def data(self, index, role):
        if index.isValid() and role == Qt.DisplayRole:
            return self.my_data[index.row()][index.column()]

    def headerData(self, col, orientation, role):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.header[col]
        return None

    def sort(self, col, order):
        self.layoutAboutToBeChanged.emit()
        self.my_data = sorted(self.my_data, key=operator.itemgetter(col))
        if order == Qt.DescendingOrder:
            self.my_data.reverse()
        self.layoutChanged.emit()

    def flags(self, index):
        if not index.isValid():
            return None
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def setData(self, index, value, role):
        if not index.isValid():
            return False
        return True


class AmazonStatsDialog(QDialog):
    def __init__(self, items, orders, refunds, **kwargs):
        super(AmazonStatsDialog, self).__init__(**kwargs)
        self.setWindowTitle('Amazon Stats for Items/Orders/Refunds')
        self.setModal(True)
        v_layout = QVBoxLayout()
        self.setLayout(v_layout)

        v_layout.addWidget(QLabel('Amazon Stats:'))
        if len(orders) == 0 or len(items) == 0:
            v_layout.addWidget(QLabel(
                'There were not Amazon orders/items!'))

            close_button = QPushButton('Close')
            v_layout.addWidget(close_button)
            close_button.clicked.connect(self.close)
            return

        v_layout.addWidget(QLabel(
            '\n{} orders with {} matching items'.format(
                len([o for o in orders if o.items_matched]),
                len([i for i in items if i.matched]))))
        v_layout.addWidget(QLabel(
            '{} unmatched orders and {} unmatched items'.format(
                len([o for o in orders if not o.items_matched]),
                len([i for i in items if not i.matched]))))

        first_order_date = min([o.order_date for o in orders])
        last_order_date = max([o.order_date for o in orders])

        v_layout.addWidget(QLabel(
            'Orders ranging from {} to {}'.format(
                first_order_date, last_order_date)))

        per_item_totals = [i.item_total for i in items]
        per_order_totals = [o.total_charged for o in orders]

        v_layout.addWidget(QLabel(
            '{} total spend'.format(
                micro_usd_to_usd_string(sum(per_order_totals)))))
        v_layout.addWidget(QLabel(
            '{} avg order total (range: {} - {})'.format(
                micro_usd_to_usd_string(sum(per_order_totals) / len(orders)),
                micro_usd_to_usd_string(min(per_order_totals)),
                micro_usd_to_usd_string(max(per_order_totals)))))
        v_layout.addWidget(QLabel(
            '{} avg item price (range: {} - {})'.format(
                micro_usd_to_usd_string(sum(per_item_totals) / len(items)),
                micro_usd_to_usd_string(min(per_item_totals)),
                micro_usd_to_usd_string(max(per_item_totals)))))

        if refunds:
            first_refund_date = min(
                [r.refund_date for r in refunds if r.refund_date])
            last_refund_date = max(
                [r.refund_date for r in refunds if r.refund_date])
            v_layout.addWidget(QLabel(
                '\n{} refunds dating from {} to {}'.format(
                    len(refunds), first_refund_date, last_refund_date)))

            per_refund_totals = [r.total_refund_amount for r in refunds]

            v_layout.addWidget(QLabel(
                '{} total refunded'.format(
                    micro_usd_to_usd_string(sum(per_refund_totals)))))

        close_button = QPushButton('Close')
        v_layout.addWidget(close_button)
        close_button.clicked.connect(self.close)


class TaggerStatsDialog(QDialog):
    def __init__(self, stats, **kwargs):
        super(TaggerStatsDialog, self).__init__(**kwargs)
        self.setWindowTitle('Tagger Stats')
        self.setModal(True)
        v_layout = QVBoxLayout()
        self.setLayout(v_layout)

        v_layout.addWidget(QLabel(
            '\nTransactions: {trans}\n'
            'Transactions w/ "Amazon" in description: {amazon_in_desc}\n'
            'Transactions ignored: is pending: {pending}\n'
            '\n'
            'Orders matched w/ transactions: {order_match} (unmatched orders: '
            '{order_unmatch})\n'
            'Refunds matched w/ transactions: {refund_match} '
            '(unmatched refunds: '
            '{refund_unmatch})\n'
            'Transactions matched w/ orders/refunds: {trans_match} '
            '(unmatched: '
            '{trans_unmatch})\n'
            '\n'
            'Orders skipped: not shipped: {skipped_orders_unshipped}\n'
            'Orders skipped: gift card used: {skipped_orders_gift_card}\n'
            '\n'
            'Order fix-up: incorrect tax itemization: {adjust_itemized_tax}\n'
            'Order fix-up: has a misc charges (e.g. gift wrap): '
            '{misc_charge}\n'
            '\n'
            'Transactions ignored; already tagged & up to date: '
            '{already_up_to_date}\n'
            'Transactions ignored; ignore retags: {no_retag}\n'
            'Transactions ignored; user skipped retag: {user_skipped_retag}\n'
            '\n'
            'Transactions with personalize categories: {personal_cat}\n'
            '\n'
            'Transactions to be retagged: {retag}\n'
            'Transactions to be newly tagged: {new_tag}\n'.format(**stats)))

        close_button = QPushButton('Close')
        v_layout.addWidget(close_button)
        close_button.clicked.connect(self.close)
