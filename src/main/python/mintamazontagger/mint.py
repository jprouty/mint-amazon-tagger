from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime
import pickle
import re
import os

from mintamazontagger import category
from mintamazontagger.currency import micro_usd_to_usd_string
from mintamazontagger.currency import parse_usd_as_micro_usd
from mintamazontagger.currency import round_micro_usd_to_cent
from mintamazontagger.progress import NoProgress


def truncate_title(title, target_length, base_str=None):
    words = []
    if base_str:
        words.extend([w for w in base_str.split(' ') if w])
        target_length -= len(base_str)
    for word in title.strip().split(' '):
        if len(word) / 2 < target_length:
            words.append(word)
            target_length -= len(word) + 1
        else:
            break
    truncated = ' '.join(words)
    # Remove any trailing symbol-y crap.
    while truncated and truncated[-1] in ',.-([]{}\\/|~!@#$%^&*_+=`\'" ':
        truncated = truncated[:-1]
    return truncated


# Credit: https://stackoverflow.com/questions/1175208
first_cap_re = re.compile('(.)([A-Z][a-z]+)')
all_cap_re = re.compile('([a-z0-9])([A-Z])')


def convertCamel_to_underscores(name):
    s1 = first_cap_re.sub(r'\1_\2', name)
    return all_cap_re.sub(r'\1_\2', s1).lower()


def pythonify_mint_dict(raw_dict):
    # Parse out the date fields into datetime.date objects.
    raw_dict['date'] = parse_mint_date(raw_dict['date'])
    raw_dict['odate'] = parse_mint_date(raw_dict['odate'])

    # Parse the amount into micro usd.
    amount = parse_usd_as_micro_usd(raw_dict['amount'])
    # Adjust credit transactions such that:
    # - debits are positive
    # - credits are negative
    if not raw_dict['isDebit']:
        amount *= -1
    raw_dict['amount'] = amount

    return dict([
        (convertCamel_to_underscores(k.replace(' ', '_')), v)
        for k, v in raw_dict.items()
    ])


def parse_mint_date(date_str):
    current_year = datetime.isocalendar(date.today())[0]
    try:
        new_date = datetime.strptime(date_str + str(current_year), '%b %d%Y')
    except ValueError:
        new_date = datetime.strptime(date_str, '%m/%d/%y')
    return new_date.date()


class Transaction(object):
    """A Mint tranaction."""

    matched = False
    orders = []
    item = None  # Set in the case of itemized new transactions.
    children = []

    def __init__(self, raw_dict):
        self.__dict__.update(pythonify_mint_dict(raw_dict))

    def split(self, amount, category, desc, note, is_debit=True):
        """Returns a new Transaction split from self."""
        item = deepcopy(self)

        # Itemized should NOT have this info, otherwise there are some lovely
        # cycles.
        item.matched = False
        item.orders = []
        item.children = []

        item.merchant = desc
        item.category = category
        item.amount = amount
        item.is_debit = is_debit
        item.note = note

        return item

    def match(self, orders):
        self.matched = True
        self.orders = orders

    def bastardize(self):
        """Severes the child from the parent, making this a parent itself."""
        self.is_child = False
        del self.__dict__['pid']

    def update_category_id(self, mint_cat_name_to_id):
        # Assert the category name is valid then update the categoryId.
        assert self.category in mint_cat_name_to_id
        self.category_id = mint_cat_name_to_id[self.category]

    def get_compare_tuple(self, ignore_category=False):
        """Returns a 3-tuple used to determine if 2 transactions are equal."""
        # TODO: Add the 'note' field once itemized transactions include notes.
        # Use str to avoid float cmp.
        base = (self.merchant, micro_usd_to_usd_string(self.amount), self.note)
        return base if ignore_category else base + (self.category,)

    def dry_run_str(self, ignore_category=False):
        return '{} \t {} \t {} \t {}'.format(
            self.date.strftime('%m/%d/%y'),
            micro_usd_to_usd_string(self.amount),
            '--IGNORED--' if ignore_category
            else '{}({})'.format(self.category, self.category_id),
            self.merchant)

    def __repr__(self):
        has_note = 'with note' if self.note else ''
        return (
            'Mint Trans({id}): {amount} {date} {merchant} {category} '
            '{has_note}'.format(
                id=self.id,
                amount=micro_usd_to_usd_string(self.amount),
                date=self.date,
                merchant=self.merchant,
                category=self.category,
                has_note=has_note))

    @classmethod
    def parse_from_json(cls, json_dicts, progress=NoProgress()):
        result = []
        for raw_dict in json_dicts:
            result.append(cls(raw_dict))
            progress.next()
        return result

    @staticmethod
    def sum_amounts(trans):
        return sum([t.amount for t in trans])

    @staticmethod
    def unsplit(trans):
        """Reconsistitutes Mint splits/itemizations into parent transaction."""
        parent_id_to_trans = defaultdict(list)
        result = []
        for t in trans:
            if t.is_child:
                parent_id_to_trans[t.pid].append(t)
            else:
                result.append(t)

        for pid, children in parent_id_to_trans.items():
            parent = deepcopy(children[0])

            parent.id = pid
            parent.bastardize()
            parent.amount = round_micro_usd_to_cent(
                Transaction.sum_amounts(children))
            parent.is_debit = parent.amount > 0
            parent.children = children

            result.append(parent)

        return result

    @staticmethod
    def old_and_new_are_identical(old, new, ignore_category=False):
        """Returns True if there is zero difference between old and new."""
        old_set = set(
            [c.get_compare_tuple(ignore_category) for c in old.children]
            if old.children
            else [old.get_compare_tuple(ignore_category)])
        new_set = set([t.get_compare_tuple(ignore_category) for t in new])
        return old_set == new_set


def itemize_new_trans(new_trans, prefix):
    # Add a prefix to all itemized transactions for easy keyword searching
    # within Mint. Use the same prefix, based on if the original transaction
    for nt in new_trans:
        nt.merchant = prefix + nt.merchant

    # Turns out the first entry is typically displayed last in the Mint
    # UI. Reverse everything for ideal readability.
    return new_trans[::-1]


NON_ITEM_MERCHANTS = set([
    'Misc Charge (Gift wrap, etc)',
    'Promotion(s)',
    'Shipping',
    'Tax adjustment'])


def summarize_title(titles, prefix):
    trun_len = (100 - len(prefix) - 2 * len(titles)) / len(titles)
    return prefix + (', '.join(
        [truncate_title(t, trun_len) for t in titles]))


def summarize_new_trans(t, new_trans, prefix):
    # When not itemizing, create a description by concating the items. Store
    # the full information in the transaction notes. Category is untouched when
    # there's more than one item (this is why itemizing is better!).
    title = summarize_title(
        [nt.merchant
         for nt in new_trans
         if nt.merchant not in NON_ITEM_MERCHANTS],
        prefix)
    notes = '{}\nItem(s):\n{}'.format(
        new_trans[0].note,
        '\n'.join(
            [' - ' + nt.merchant
             for nt in new_trans]))

    summary_trans = deepcopy(t)
    summary_trans.merchant = title
    if len([nt for nt in new_trans
            if nt.merchant not in NON_ITEM_MERCHANTS]) == 1:
        summary_trans.category = new_trans[0].category
        summary_trans.category_id = new_trans[0].category_id
    else:
        summary_trans.category = category.DEFAULT_MINT_CATEGORY
    summary_trans.note = notes
    return [summary_trans]


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


def dump_trans_and_categories(trans, cats, pickle_epoch, pickle_base_path):
    if not os.path.exists(pickle_base_path):
        os.makedirs(pickle_base_path)
    trans_pickle_path = os.path.join(
        pickle_base_path, MINT_TRANS_PICKLE_FMT.format(pickle_epoch))
    cats_pickle_path = os.path.join(
        pickle_base_path, MINT_CATS_PICKLE_FMT.format(pickle_epoch))
    with open(trans_pickle_path, 'wb') as f:
        pickle.dump(trans, f)
    with open(cats_pickle_path, 'wb') as f:
        pickle.dump(cats, f)
