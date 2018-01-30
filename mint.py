from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime
from pprint import pformat, pprint
import re

import category
from currency import micro_usd_nearly_equal, micro_usd_to_usd_string, parse_usd_as_micro_usd, round_micro_usd_to_cent


def truncate_title(title, target_length, base_str=None):
    words = []
    if base_str:
        words.extend([w for w in base_str.split(' ') if w])
        target_length -= len(base_str)
    for word in title.split(' '):
        if len(word) / 2 < target_length:
            words.append(word)
            target_length -= len(word) + 1
        else:
            break
    truncated = ' '.join(words)
    # Remove any trailing symbol-y crap.
    while truncated and truncated[-1] in ',.-([]{}\/|~!@#$%^&*_+=`\'" ':
        truncated = truncated[:-1]
    return truncated


# Credit: https://stackoverflow.com/questions/1175208/elegant-python-function-to-convert-camelcase-to-snake-case
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
    
    return dict([(convertCamel_to_underscores(k.replace(' ', '_')), v) for k, v in raw_dict.items()])


def parse_mint_date(date_str):
    current_year = datetime.isocalendar(date.today())[0]
    try:
        new_date = datetime.strptime(date_str + str(current_year), '%b %d%Y')
    except:
        new_date = datetime.strptime(date_str, '%m/%d/%y')
    return new_date.date()


class Transaction(object):
    """A Mint tranaction."""

    matched = False
    orders = []
    children = []

    def __init__(self, raw_dict):
        self.__dict__.update(pythonify_mint_dict(raw_dict))

    def split(self, amount, category, desc, note, isDebit=True):
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
        item.isDebit = isDebit
        item.note = note

        return item

    def match(self, orders):
        self.matched = True
        self.orders = orders
        
    def remove_pid(self):
        del self.__dict__['pid']
    
    def update_category_id(self, mint_cat_name_to_id):
        # Assert the category name is valid then update the categoryId.
        assert self.category in mint_cat_name_to_id
        self.category_id = mint_cat_name_to_id[self.category]

    def get_compare_tuple(self, ignore_category=False):
        """Returns a 3-tuple used to determine if 2 transactions are equal."""
        # TODO: Add the 'note' field once itemized transactions include notes.
        # Use str to avoid float cmp.
        base = (self.merchant, micro_usd_to_usd_string(self.amount))
        return base if ignore_category else base + (self.category,)

    def dry_run_str(self, ignore_category=False):
        return '{} \t {} \t {} \t {}'.format(
            self.date.strftime('%m/%d/%y'),
            micro_usd_to_usd_string(self.amount),
            '--IGNORED--' if ignore_category else self.category,
            self.merchant)

    def __repr__(self):
        has_note = 'with note' if self.note else ''
        return 'Mint Trans({id}): {amount} {date} {merchant} {category} {has_note}'.format(
            id=self.id, amount=micro_usd_to_usd_string(self.amount), date=self.date, merchant=self.merchant, category=self.category, has_note=has_note)

    @classmethod
    def parse_from_json(cls, json_dicts):
        return [cls(raw_dict) for raw_dict in json_dicts]

    @staticmethod
    def sum_amounts(trans):
        return sum([t.amount for t in trans])

    @staticmethod
    def unsplit(trans):
        """Reconsistitutes Mint splits/itemizations into a parent transaction."""
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
            parent.is_child = False
            parent.remove_pid()
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


def summarize_new_trans(t, new_trans, prefix):
    # When not itemizing, create a description by concating the items. Store
    # the full information in the transaction notes. Category is untouched when
    # there's more than one item (this is why itemizing is better!).
    trun_len = (100 - len(prefix) - 2 * len(new_trans)) / len(new_trans)
    title = prefix + (', '.join(
        [truncate_title(nt.merchant, trun_len)
         for nt in new_trans
         if nt.merchant not in
         ('Promotion(s)', 'Shipping', 'Tax adjustment')]))
    notes = '{}\nItem(s):\n{}'.format(
        new_trans[0].note,
        '\n'.join(
            [' - ' + nt.merchant
             for nt in new_trans]))

    summary_trans = deepcopy(t)
    summary_trans.merchant = title
    if len(new_trans) == 1:
        summary_trans.category = new_trans[0].category
    else:
        summary_trans.category = category.DEFAULT_MINT_CATEGORY
    summary_trans.note = notes
    return [summary_trans]

    
