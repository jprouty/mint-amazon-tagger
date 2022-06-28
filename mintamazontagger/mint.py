from collections import defaultdict
from copy import deepcopy
from datetime import datetime
import pickle
import re
import os

from mintamazontagger import category
from mintamazontagger.currency import (
    micro_usd_to_usd_string, parse_float_usd_as_micro_usd,
    round_micro_usd_to_cent)
from mintamazontagger.my_progress import NoProgress


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


def convert_camel_dict(raw_dict):
    return dict([
        (convertCamel_to_underscores(k.replace(' ', '_')), v)
        for k, v in raw_dict.items()
    ])


def pythonify_mint_transaction_dict(raw_dict, is_fi_data=False):
    # Parse out the date field into a datetime.date object.
    raw_dict['date'] = parse_mint_date(raw_dict['date'])

    # Parse the float value into micro usd.
    raw_dict['amount'] = parse_float_usd_as_micro_usd(raw_dict['amount'])

    if is_fi_data:
        raw_dict['inferredCategory'] = Category(raw_dict['inferredCategory'])
    else:
        raw_dict['category'] = Category(raw_dict['category'])
        raw_dict['fiData'] = FinancialInstitutionData(raw_dict['fiData'])
        # Ensure the notes field is always present (None if not present).
        raw_dict['notes'] = raw_dict.get('notes')
        raw_dict['parentId'] = raw_dict.get('parentId')

    return convert_camel_dict(raw_dict)


def pythonify_mint_category_dict(raw_dict):
    return convert_camel_dict(raw_dict)


def parse_mint_date(date_str):
    return datetime.strptime(date_str, '%Y-%m-%d').date()


class Category(object):
    """A Mint category."""

    def __init__(self, raw_dict):
        self.__dict__.update(pythonify_mint_category_dict(raw_dict))

    def update_category_id(self, mint_categories):
        # Assert the category name is valid then update the id.
        assert self.name in mint_categories
        self.id = mint_categories[self.name]['id']

    def __repr__(self):
        return '{}({})'.format(self.name, self.id)


class Transaction(object):
    """A Mint tranaction."""

    matched = False
    orders = []
    item = None  # Set in the case of itemized new transactions.
    children = []

    def __init__(self, raw_dict):
        self.__dict__.update(pythonify_mint_transaction_dict(raw_dict))

    def split(self, amount, category_name, description, notes):
        """Returns a new Transaction split from self."""
        item = deepcopy(self)

        # Itemized should NOT have this info, otherwise there are some lovely
        # cycles.
        item.matched = False
        item.orders = []
        item.children = []

        item.amount = amount
        item.category = Category({'name': category_name, 'id': None})
        item.description = description
        item.notes = notes

        return item

    def match(self, orders):
        self.matched = True
        self.orders = orders

    def bastardize(self):
        """Severes the child from the parent, making this a parent itself."""
        self.parent_id = None

    def update_category_id(self, mint_categories):
        self.category.update_category_id(mint_categories)

    def get_compare_tuple(self, ignore_category=False):
        """Returns a 3-tuple used to determine if 2 transactions are equal."""
        # TODO: Add the 'note' field once itemized transactions include notes.
        # Use str to avoid float cmp.
        base = (
            self.description,
            micro_usd_to_usd_string(self.amount),
            self.notes)
        return base if ignore_category else base + (self.category.name,)

    def dry_run_str(self, ignore_category=False):
        return '{} \t {} \t {} \t {}'.format(
            self.date.strftime('%Y-%m-%d'),
            micro_usd_to_usd_string(self.amount),
            '--IGNORED--' if ignore_category else self.category,
            self.description)

    def __repr__(self):
        notes = 'with notes' if self.notes else ''
        return (
            'Mint Trans({id}): {amount} {date} {description} {category} '
            '{notes}'.format(
                id=self.id,
                amount=micro_usd_to_usd_string(self.amount),
                date=self.date,
                description=self.description,
                category=self.category,
                notes=notes))

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
            if t.parent_id:
                parent_id_to_trans[t.parent_id].append(t)
            else:
                result.append(t)

        for parent_id, children in parent_id_to_trans.items():
            parent = deepcopy(children[0])

            parent.id = parent_id
            parent.bastardize()
            parent.amount = round_micro_usd_to_cent(
                Transaction.sum_amounts(children))
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


class FinancialInstitutionData(object):
    """Additional transaction details from the financial institution."""

    def __init__(self, raw_dict):
        self.__dict__.update(pythonify_mint_transaction_dict(raw_dict, True))

    def __repr__(self):
        return (
            'Mint FI Trans({id}): '
            '{amount} {date} {description} {category}'.format(
                id=self.id,
                amount=micro_usd_to_usd_string(self.amount),
                date=self.date,
                description=self.description,
                category=self.inferred_category))


def itemize_new_trans(new_trans, prefix):
    # Add a prefix to all itemized transactions for easy keyword searching
    # within Mint. Use the same prefix, based on if the original transaction
    for nt in new_trans:
        nt.description = prefix + nt.description

    # Turns out the first entry is typically displayed last in the Mint
    # UI. Reverse everything for ideal readability.
    return new_trans[::-1]


NON_ITEM_DESCRIPTIONS = set([
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
        [nt.description
         for nt in new_trans
         if nt.description not in NON_ITEM_DESCRIPTIONS],
        prefix)
    notes = '{}\nItem(s):\n{}'.format(
        new_trans[0].notes,
        '\n'.join(
            [' - ' + nt.description
             for nt in new_trans]))

    summary_trans = deepcopy(t)
    summary_trans.description = title
    if len([nt for nt in new_trans
            if nt.description not in NON_ITEM_DESCRIPTIONS]) == 1:
        summary_trans.category = new_trans[0].category
    else:
        summary_trans.category.name = category.DEFAULT_MINT_CATEGORY
    summary_trans.notes = notes
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
