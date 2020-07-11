# This script takes Amazon "Order History Reports" and annotates your Mint
# transactions based on actual items in each purchase. It can handle orders
# that are split into multiple shipments/charges, and can even itemized each
# transaction for maximal control over categorization.

# First, you must generate and download your order history reports from:
# https://www.amazon.com/gp/b2b/reports

from collections import defaultdict, namedtuple, Counter
import datetime
import itertools
import logging
import readchar
import time

from mintamazontagger import amazon
from mintamazontagger import category
from mintamazontagger import mint
from mintamazontagger.my_progress import no_progress_factory
from mintamazontagger.currency import micro_usd_nearly_equal

from mintamazontagger.mint import (
    get_trans_and_categories_from_pickle, dump_trans_and_categories)
from mintamazontagger.orderhistory import fetch_order_history

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

UpdatesResult = namedtuple(
    'UpdatesResult',
    field_names=(
        'success',
        'items', 'orders', 'refunds', 'updates', 'unmatched_orders', 'stats'),
    defaults=(
        False,
        None, None, None, None, None, None))


def create_updates(
        args,
        mint_client,
        on_critical,
        indeterminate_progress_factory=no_progress_factory,
        determinate_progress_factory=no_progress_factory,
        counter_progress_factory=no_progress_factory):
    items_csv = args.items_csv
    orders_csv = args.orders_csv
    refunds_csv = args.refunds_csv

    start_date = None

    if not items_csv or not orders_csv:
        start_date = args.order_history_start_date
        end_date = args.order_history_end_date
        if not args.amazon_email or not args.amazon_password:
            on_critical(
                'Amazon email or password is empty. Please try again')
            return UpdatesResult()

        items_csv, orders_csv, refunds_csv = fetch_order_history(
            args.report_download_location, start_date, end_date,
            args.amazon_email, args.amazon_password,
            args.session_path, args.headless,
            progress_factory=indeterminate_progress_factory)

    if not items_csv or not orders_csv:  # Refunds are optional
        on_critical(
            'Order history either not provided at or unable to fetch. '
            'Exiting.')
        return UpdatesResult()

    try:
        orders = amazon.Order.parse_from_csv(
            orders_csv,
            progress_factory=determinate_progress_factory)
        items = amazon.Item.parse_from_csv(
            items_csv,
            progress_factory=determinate_progress_factory)
        refunds = ([] if not refunds_csv
                   else amazon.Refund.parse_from_csv(
                       refunds_csv,
                       progress_factory=determinate_progress_factory))

    except AttributeError as e:
        msg = (
            'Error while parsing Amazon Order history report CSV files: '
            '{}'.format(e))
        logger.exception(msg)
        on_critical(msg)
        return UpdatesResult()

    if not len(orders):
        on_critical(
            'The Orders report contains no data. Try '
            'downloading again. Report used: {}'.format(
                orders_csv))
        return UpdatesResult()
    if not len(items):
        on_critical(
            'The Items report contains no data. Try '
            'downloading again. Report used: {}'.format(
                items_csv))
        return UpdatesResult()

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
        on_critical('Missing Mint email or password. Try again')
        return UpdatesResult()

    if args.pickled_epoch:
        pickle_progress = indeterminate_progress_factory(
            'Un-pickling Mint transactions from epoch: {} '.format(
                args.pickled_epoch))
        mint_trans, mint_category_name_to_id = (
            get_trans_and_categories_from_pickle(
                args.pickled_epoch, args.mint_pickle_location))
        pickle_progress.finish()
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

        cat_progress = indeterminate_progress_factory(
            'Getting Mint Categories')
        mint_category_name_to_id = mint_client.get_categories()
        cat_progress.finish()

        trans_progress = indeterminate_progress_factory(
            'Getting Mint Transactions')
        mint_transactions_json = mint_client.get_transactions(
            start_date)
        trans_progress.finish()

        parse_progress = determinate_progress_factory(
            'Parsing Mint Transactions', len(mint_transactions_json))
        mint_trans = mint.Transaction.parse_from_json(
            mint_transactions_json, parse_progress)
        parse_progress.finish()

        if args.save_pickle_backup:
            pickle_epoch = int(time.time())
            pickle_progress = indeterminate_progress_factory(
                'Backing up Mint to local pickl epoch: {} '.format(
                    pickle_epoch))
            dump_trans_and_categories(
                mint_trans, mint_category_name_to_id, pickle_epoch,
                args.mint_pickle_location)
            pickle_progress.finish()

    updates, unmatched_orders = get_mint_updates(
        orders, items, refunds,
        mint_trans,
        args, stats,
        mint_category_name_to_id,
        progress_factory=determinate_progress_factory)
    return UpdatesResult(
        True, items, orders, refunds, updates, unmatched_orders, stats)


def get_mint_category_history_for_items(trans, args):
    """Gets a mapping of item name -> category name.

    For use in memorizing personalized categories.
    """
    if args.do_not_predict_categories:
        return None
    # Don't worry about pending.
    trans = [t for t in trans if not t.is_pending]
    # Only do debits for now.
    trans = [t for t in trans if t.is_debit]

    # Filter for transactions that have been tagged before.
    valid_prefixes = args.amazon_domains.lower().split(',')
    valid_prefixes = ['{}: '.format(pre) for pre in valid_prefixes]
    if args.description_prefix_override:
        valid_prefixes.append(args.description_prefix_override.lower())
    trans = [t for t in trans if
             any(t.merchant.lower().startswith(pre)
                 for pre in valid_prefixes)]

    # Filter out the default category: there is no signal here.
    trans = [t for t in trans
             if t.category != category.DEFAULT_MINT_CATEGORY]

    # Filter out non-item merchants.
    trans = [t for t in trans
             if t.merchant not in mint.NON_ITEM_MERCHANTS]

    item_to_cats = defaultdict(Counter)
    for t in trans:
        # Remove the prefix for the item:
        for pre in valid_prefixes:
            item_name = t.merchant.lower()
            # Find & remove the prefix and remove any leading '3x '.
            if item_name.startswith(pre):
                item_name = amazon.rm_leading_qty(item_name[len(pre):])
                break

        item_to_cats[item_name][t.category] += 1

    item_to_most_common = {}
    for item_name, counter in item_to_cats.items():
        item_to_most_common[item_name] = counter.most_common()[0][0]

    return item_to_most_common


def get_mint_updates(
        orders, items, refunds,
        trans,
        args, stats,
        mint_category_name_to_id=category.DEFAULT_MINT_CATEGORIES_TO_IDS,
        progress_factory=no_progress_factory):
    mint_historic_category_renames = get_mint_category_history_for_items(
        trans, args)

    # Remove items from canceled orders.
    items = [i for i in items if not i.is_cancelled()]
    # Remove items that haven't shipped yet (also aren't charged).
    items = [i for i in items if i.order_status == 'Shipped']
    # Remove items with zero quantity (it happens!)
    items = [i for i in items if i.quantity > 0]
    # Make more Items such that every item is quantity 1. This is critical
    # prior to associate_items_with_orders such that items with non-1
    # quantities split into different packages can be associated with the
    # appropriate order.
    items = [si for i in items for si in i.split_by_quantity()]

    order_item_to_unspsc = dict(
        ((i.title, i.order_id), i.unspsc_code)
        for i in items)

    itemProgress = progress_factory(
        'Matching Amazon Items with Orders',
        len(items))
    amazon.associate_items_with_orders(orders, items, itemProgress)
    itemProgress.finish()

    # Only match orders that have items.
    orders = [o for o in orders if o.items]

    trans = mint.Transaction.unsplit(trans)
    stats['trans'] = len(trans)
    # Skip t if the original description doesn't contain 'amazon'
    merch_whitelist = args.mint_input_merchant_filter.lower().split(',')

    def get_original_names(t):
        """Returns a tuple of 'original' merchant strings to consider"""
        result = (t.omerchant.lower(), )
        if args.mint_input_include_mmerchant:
            result = result + (t.mmerchant.lower(), )
        if args.mint_input_include_merchant:
            result = result + (t.merchant.lower(), )
        return result

    trans = [t for t in trans if any(
                 any(merch_str in n for n in get_original_names(t))
                 for merch_str in merch_whitelist)]
    stats['amazon_in_desc'] = len(trans)
    # Skip t if it's pending.
    trans = [t for t in trans if not t.is_pending]
    stats['pending'] = stats['amazon_in_desc'] - len(trans)
    # Skip t if a category filter is given and t does not match.
    if args.mint_input_categories_filter:
        cat_whitelist = set(
            args.mint_input_categories_filter.lower().split(','))
        trans = [t for t in trans if t.category.lower() in cat_whitelist]

    # Match orders.
    orderMatchProgress = progress_factory(
        'Matching Amazon Orders w/ Mint Trans',
        len(orders))
    match_transactions(trans, orders, args, orderMatchProgress)
    orderMatchProgress.finish()

    unmatched_trans = [t for t in trans if not t.orders]

    # Match refunds.
    refundMatchProgress = progress_factory(
        'Matching Amazon Refunds w/ Mint Trans',
        len(refunds))
    match_transactions(unmatched_trans, refunds, args, refundMatchProgress)
    refundMatchProgress.finish()

    unmatched_orders = [o for o in orders if not o.matched]
    unmatched_trans = [t for t in trans if not t.orders]
    unmatched_refunds = [r for r in refunds if not r.matched]

    num_gift_card = len([o for o in unmatched_orders
                         if 'Gift Certificate' in o.payment_instrument_type])
    num_unshipped = len([o for o in unmatched_orders if not o.shipment_date])

    matched_orders = [o for o in orders if o.matched]
    matched_trans = [t for t in trans if t.orders]
    matched_refunds = [r for r in refunds if r.matched]

    stats['trans_unmatch'] = len(unmatched_trans)
    stats['order_unmatch'] = len(unmatched_orders)
    stats['refund_unmatch'] = len(unmatched_refunds)
    stats['trans_match'] = len(matched_trans)
    stats['order_match'] = len(matched_orders)
    stats['refund_match'] = len(matched_refunds)
    stats['skipped_orders_gift_card'] = num_gift_card
    stats['skipped_orders_unshipped'] = num_unshipped

    merged_orders = []
    merged_refunds = []

    updateCounter = progress_factory(
        'Determining Mint Updates',
        len(matched_trans))
    updates = []
    for t in matched_trans:
        updateCounter.next()
        if t.is_debit:
            order = amazon.Order.merge(t.orders)
            merged_orders.extend(orders)

            prefix = '{}: '.format(order.website)
            if args.description_prefix_override:
                prefix = args.description_prefix_override

            if order.attribute_subtotal_diff_to_misc_charge():
                stats['misc_charge'] += 1
            # It's nice when "free" shipping cancels out with the shipping
            # promo, even though there is tax on said free shipping. Spread
            # that out across the items instead.
            # if order.attribute_itemized_diff_to_shipping_tax():
            #     stats['add_shipping_tax'] += 1
            if order.attribute_itemized_diff_to_per_item_tax():
                stats['adjust_itemized_tax'] += 1

            assert micro_usd_nearly_equal(t.amount, order.total_charged)
            assert micro_usd_nearly_equal(t.amount, order.total_by_subtotals())
            assert micro_usd_nearly_equal(t.amount, order.total_by_items())

            new_transactions = order.to_mint_transactions(
                t,
                skip_free_shipping=not args.verbose_itemize)

        else:
            refunds = amazon.Refund.merge(t.orders)
            merged_refunds.extend(refunds)
            prefix = '{} refund: '.format(refunds[0].website)

            if args.description_return_prefix_override:
                prefix = args.description_return_prefix_override

            new_transactions = []
            for r in refunds:
                new_tran = r.to_mint_transaction(t)
                new_transactions.append(new_tran)

                # Attempt to find the category from the original purchase.
                unspsc = order_item_to_unspsc.get((r.title, r.order_id), None)
                if unspsc:
                    new_tran.category = category.get_mint_category_from_unspsc(
                        unspsc)

        assert micro_usd_nearly_equal(
            t.amount,
            mint.Transaction.sum_amounts(new_transactions))

        for nt in new_transactions:
            # Look if there's a personal category tagged.
            item_name = amazon.rm_leading_qty(nt.merchant.lower())
            if (mint_historic_category_renames and
                    item_name in mint_historic_category_renames):
                suggested_cat = mint_historic_category_renames[item_name]
                if suggested_cat != nt.category:
                    stats['personal_cat'] += 1
                    nt.category = mint_historic_category_renames[item_name]

            nt.update_category_id(mint_category_name_to_id)

        summarize_single_item_order = (
            t.is_debit and len(order.items) == 1 and not args.verbose_itemize)
        if args.no_itemize or summarize_single_item_order:
            new_transactions = mint.summarize_new_trans(
                t, new_transactions, prefix)
        else:
            new_transactions = mint.itemize_new_trans(new_transactions, prefix)

        if mint.Transaction.old_and_new_are_identical(
                t, new_transactions, ignore_category=args.no_tag_categories):
            stats['already_up_to_date'] += 1
            continue

        valid_prefixes = (
            args.amazon_domains.lower().split(',') + [prefix.lower()])
        if any(t.merchant.lower().startswith(pre) for pre in valid_prefixes):
            if args.prompt_retag:
                if args.num_updates > 0 and len(updates) >= args.num_updates:
                    break
                print('\nTransaction already tagged:')
                print_dry_run(
                    [(t, new_transactions)],
                    ignore_category=args.no_tag_categories)
                print('\nUpdate tag to proposed? [Yn] ')
                action = readchar.readchar()
                if action == '':
                    exit(1)
                if action not in ('Y', 'y', '\r', '\n'):
                    stats['user_skipped_retag'] += 1
                    continue
                stats['retag'] += 1
            elif not args.retag_changed:
                stats['no_retag'] += 1
                continue
            else:
                stats['retag'] += 1
        else:
            stats['new_tag'] += 1
        updates.append((t, new_transactions))

    if args.num_updates > 0:
        updates = updates[:args.num_updates]

    return updates, unmatched_orders + unmatched_refunds


def mark_best_as_matched(t, list_of_orders_or_refunds, args, progress=None):
    if not list_of_orders_or_refunds:
        return

    # Only consider it a match if the posted date (transaction date) is
    # within a low number of days of the ship date of the order.
    max_days = args.max_days_between_payment_and_shipping
    closest_match_num_days = max_days + 365  # Large number
    closest_match = None

    for orders in list_of_orders_or_refunds:
        an_order = next((o for o in orders if o.transact_date()), None)
        if not an_order:
            continue
        num_days = (t.odate - an_order.transact_date()).days
        # TODO: consider orders even if it has a matched_transaction if this
        # transaction is closer.
        already_matched = any([o.matched for o in orders])
        if (abs(num_days) <= max_days and
                abs(num_days) < closest_match_num_days and
                not already_matched):
            closest_match = orders
            closest_match_num_days = abs(num_days)

    if closest_match:
        for o in closest_match:
            o.match(t)
        t.match(closest_match)
        if progress:
            progress.next(len(closest_match))


def match_transactions(unmatched_trans, unmatched_orders, args, progress=None):
    # Also works with Refund objects.
    # First pass: Match up transactions that exactly equal an order's charged
    # amount.
    amount_to_orders = defaultdict(list)

    for o in unmatched_orders:
        amount_to_orders[o.transact_amount()].append([o])

    for t in unmatched_trans:
        mark_best_as_matched(t, amount_to_orders[t.amount], args, progress)

    unmatched_orders = [o for o in unmatched_orders if not o.matched]
    unmatched_trans = [t for t in unmatched_trans if not t.orders]

    # Second pass: Match up transactions to a combination of orders (sometimes
    # they are charged together).
    oid_to_orders = defaultdict(list)
    for o in unmatched_orders:
        oid_to_orders[o.order_id].append(o)
    amount_to_orders = defaultdict(list)
    for orders_same_id in oid_to_orders.values():
        combos = []
        for r in range(2, len(orders_same_id) + 1):
            combos.extend(itertools.combinations(orders_same_id, r))
        for c in combos:
            orders_total = sum([o.transact_amount() for o in c])
            amount_to_orders[orders_total].append(c)

    for t in unmatched_trans:
        mark_best_as_matched(t, amount_to_orders[t.amount], args, progress)


def print_dry_run(orig_trans_to_tagged, ignore_category=False):
    for orig_trans, new_trans in orig_trans_to_tagged:
        oid = orig_trans.orders[0].order_id
        print('\nFor Amazon {}: {}\nInvoice URL: {}'.format(
            'Order' if orig_trans.is_debit else 'Refund',
            oid, amazon.get_invoice_url(oid)))

        if orig_trans.children:
            for i, trans in enumerate(orig_trans.children):
                print('{}{}) Current: \t{}'.format(
                    '\n' if i == 0 else '',
                    i + 1,
                    trans.dry_run_str()))
        else:
            print('\nCurrent: \t{}'.format(
                orig_trans.dry_run_str()))

        if len(new_trans) == 1:
            trans = new_trans[0]
            print('\nProposed: \t{}'.format(
                trans.dry_run_str(ignore_category)))
        else:
            for i, trans in enumerate(reversed(new_trans)):
                print('{}{}) Proposed: \t{}'.format(
                    '\n' if i == 0 else '',
                    i + 1,
                    trans.dry_run_str(ignore_category)))
