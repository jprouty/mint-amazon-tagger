# This script takes Amazon "Order History Reports" and annotates your Mint
# transactions based on actual items in each purchase. It can handle charges
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
import zipfile

from mintamazontagger import amazon
from mintamazontagger import category
from mintamazontagger import mint
from mintamazontagger.my_progress import no_progress_factory
from mintamazontagger.currency import micro_usd_nearly_equal

from mintamazontagger.mint import (
    get_trans_and_categories_from_pickle,
    dump_trans_and_categories,
)

logger = logging.getLogger(__name__)

UpdatesResult = namedtuple(
    "UpdatesResult",
    field_names=(
        "success",
        "items",
        "charges",
        "updates",
        "unmatched_charges",
        "stats",
    ),
    defaults=(False, None, None, None, None, None),
)


def create_updates(
    args,
    mint_client,
    on_critical,
    indeterminate_progress_factory=no_progress_factory,
    determinate_progress_factory=no_progress_factory,
    counter_progress_factory=no_progress_factory,
):
    items = []
    for export_zip in args.amazon_export:
        with zipfile.ZipFile(export_zip.name) as zip_file:
            order_history_csvs = [
                f for f in zip_file.namelist() if amazon.is_order_history_csv(f)
            ]
            if not order_history_csvs:
                on_critical(
                    "Cannot find any order history data in the given Amazon Export."
                )
                return UpdatesResult()

            try:
                for csv in order_history_csvs:
                    items.extend(
                        amazon.Item.parse_from_csv(
                            zip_file.open(csv),
                            progress_factory=determinate_progress_factory,
                        )
                    )
            except AttributeError as e:
                msg = (
                    "Error while parsing Amazon Order history report CSV files: " f"{e}"
                )
                logger.exception(msg)
                on_critical(msg)
                return UpdatesResult()

    if not len(items):
        on_critical(
            "The Items report contains no data. Try "
            f"downloading again. Reports used: {order_history_csvs}"
        )
        return UpdatesResult()

    # Sort all items by date, newest first. This is useful when multiple export zips are given.
    items = sorted(
        items,
        key=lambda i: i.ship_date[0]
        if i.ship_date[0]
        else datetime.datetime.now(datetime.timezone.utc),
        reverse=True,
    )

    # Initialize the stats. Explicitly initialize stats that might not be
    # accumulated (conditionals).
    stats = Counter(
        adjust_itemized_tax=0,
        already_up_to_date=0,
        rm_shipping_error=0,
        misc_charge=0,
        new_tag=0,
        no_retag=0,
        retag=0,
        user_skipped_retag=0,
        personal_cat=0,
    )

    # Remove items from canceled charges or pending charges (only accept "Closed" orders).
    items = [i for i in items if i.order_status == "Closed"]
    # Remove items that haven't shipped yet / aren't charged / or cancelled items out of an otherwise valid order.
    items = [i for i in items if i.shipment_status != "Not Available"]
    # Remove items with zero quantity.
    items = [i for i in items if i.quantity > 0]

    charges = [amazon.Charge([i]) for i in items]
    # THIS IS NOT ALWAYS THE CASE: I HAVE FOUND A CASE WERE THE SHIPMENT ITEM AMOUNTS WERE ACTUALLY SPLIT INTO TWO CC CHARGES FOR THE SAME CARD FOR AN ORDER THAT SHIPPED IN ONE BOX.
    # Merge charges if both the order id and the shipment item amount + shipment item tax align with total owed.
    # ie: Combine items into a charge that have matching:
    # - 'order id'
    # - 'shipment item subtotal'
    # - 'shipment item subtotal tax'
    # oid_to_items = defaultdict(list)
    # for i in items:
    #     oid_to_items[(i.order_id, i.shipment_item_subtotal, i.shipment_item_subtotal_tax)].append(i)

    # charges = []
    # for items_same_id in oid_to_items.values():
    #     if len(items_same_id) == 1:
    #         charges.append(amazon.Charge(items_same_id))
    #         continue
    #     first_item = items_same_id[0]
    #     item_subtotal = first_item.shipment_item_subtotal + first_item.shipment_item_subtotal_tax
    #     discounts = sum(i.total_discounts for i in items_same_id)
    #     shipping_charges = sum(i.shipping_charge for i in items_same_id)
    #     total_owed = item_subtotal + discounts + shipping_charges
    #     is_same_charge = all([
    #         (i.shipment_item_subtotal == first_item.shipment_item_subtotal and
    #          i.shipment_item_subtotal_tax == first_item.shipment_item_subtotal_tax)
    #         for i in items_same_id]) and total_owed == sum(i.total_owed for i in items_same_id)
    #     if is_same_charge:
    #         charges.append(amazon.Charge(items_same_id))
    #     else:
    #         # Something doesn't match up (could be same charge but two different shipments).
    #         # These will be cleaned up later with the combo matching logic per same order.
    #         charges.extend([amazon.Charge([i]) for i in items_same_id])

    if args.pickled_epoch:
        pickle_progress = indeterminate_progress_factory(
            f"Un-pickling Mint transactions from epoch: {args.pickled_epoch} "
        )
        mint_trans, mint_categories = get_trans_and_categories_from_pickle(
            args.pickled_epoch, args.mint_pickle_location
        )
        pickle_progress.finish()
    else:
        # Get the date of the oldest Amazon order.
        start_date = min([date.date() for i in items for date in i.order_date])

        login_progress = indeterminate_progress_factory("Logging in to mint.com")
        if not mint_client.login():
            login_progress.finish()
            on_critical("Cannot log in to mint.com. Check credentials")
            return UpdatesResult()
        login_progress.finish()

        cat_progress = indeterminate_progress_factory("Getting Mint Categories")
        mint_categories = mint_client.get_categories()
        cat_progress.finish()

        trans_progress = indeterminate_progress_factory("Getting Mint Transactions")
        mint_transactions_json = mint_client.get_transactions(start_date)
        trans_progress.finish()

        parse_progress = determinate_progress_factory(
            "Parsing Mint Transactions", len(mint_transactions_json)
        )
        mint_trans = mint.Transaction.parse_from_json(
            mint_transactions_json, parse_progress
        )
        parse_progress.finish()

        if args.save_pickle_backup:
            pickle_epoch = int(time.time())
            pickle_progress = indeterminate_progress_factory(
                f"Backing up Mint to local pickle epoch: {pickle_epoch}"
            )
            dump_trans_and_categories(
                mint_trans, mint_categories, pickle_epoch, args.mint_pickle_location
            )
            pickle_progress.finish()

    updates, unmatched_charges = get_mint_updates(
        items,
        charges,
        mint_trans,
        args,
        stats,
        mint_categories,
        progress_factory=determinate_progress_factory,
    )
    return UpdatesResult(True, items, charges, updates, unmatched_charges, stats)


def get_mint_category_history_for_items(trans, args):
    """Gets a mapping of item name -> category name.

    For use in memorizing personalized categories.
    """
    if args.do_not_predict_categories:
        return None
    # Don't worry about pending.
    trans = [t for t in trans if not t.is_pending]
    # Only do debits for now.
    trans = [t for t in trans if t.amount < 0]

    # Filter for transactions that have been tagged before.
    valid_prefixes = args.amazon_domains.lower().split(",")
    valid_prefixes = [f"{pre}: " for pre in valid_prefixes]
    if args.description_prefix_override:
        valid_prefixes.append(args.description_prefix_override.lower())
    trans = [
        t
        for t in trans
        if any(t.description.lower().startswith(pre) for pre in valid_prefixes)
    ]

    # Filter out the default category: there is no signal here.
    trans = [t for t in trans if t.category.name != category.DEFAULT_MINT_CATEGORY]

    # Filter out non-item descriptions.
    trans = [t for t in trans if t.description not in mint.NON_ITEM_DESCRIPTIONS]

    item_to_cats = defaultdict(Counter)
    for t in trans:
        # Remove the prefix for the item:
        for pre in valid_prefixes:
            item_name = t.description.lower()
            # Find & remove the prefix and remove any leading '3x '.
            if item_name.startswith(pre):
                item_name = amazon.rm_leading_qty(item_name[len(pre) :])
                break

        item_to_cats[item_name][t.category.name] += 1

    item_to_most_common = {}
    for item_name, counter in item_to_cats.items():
        item_to_most_common[item_name] = counter.most_common()[0][0]

    return item_to_most_common


def get_mint_updates(
    items,
    charges,
    trans,
    args,
    stats,
    mint_categories,
    progress_factory=no_progress_factory,
):
    mint_historic_category_renames = get_mint_category_history_for_items(trans, args)

    trans = mint.Transaction.unsplit(trans)
    stats["trans"] = len(trans)
    trans = sorted(trans, key=lambda t: t.date)

    # Skip t if the original description doesn't contain 'amazon'
    merch_whitelist = args.mint_input_description_filter.lower().split(",")

    def get_original_names(t):
        """Returns a tuple of description strings to consider"""
        # Always consider the original description from the financial
        # institution. Conditionally consider the current/user description or
        # the Mint inferred description.

        # Manually added transactions don't have `fi_data.description`, so return user description
        if not hasattr(t.fi_data, "description"):
            return (t.description.lower(),)

        result = (t.fi_data.description.lower(),)
        if args.mint_input_include_user_description:
            result = result + (t.description.lower(),)
        if args.mint_input_include_inferred_description:
            result = result + (t.fi_data.inferred_description.lower(),)
        return result

    trans = [
        t
        for t in trans
        if any(
            any(merch_str in n for n in get_original_names(t))
            for merch_str in merch_whitelist
        )
    ]

    stats["amazon_in_desc"] = len(trans)
    # Skip t if it's pending.
    trans = [t for t in trans if not t.is_pending]
    stats["pending"] = stats["amazon_in_desc"] - len(trans)
    # Skip t if a category filter is given and t does not match.
    if args.mint_input_categories_filter:
        cat_whitelist = set(args.mint_input_categories_filter.lower().split(","))
        trans = [t for t in trans if t.category.name.lower() in cat_whitelist]

    # Match charges.
    orderMatchProgress = progress_factory(
        "Matching Amazon Items w/ Mint Trans", len(items)
    )
    match_transactions_orig_with_shipment_merge2(
        trans, charges, args, orderMatchProgress
    )
    orderMatchProgress.finish()

    unmatched_trans = [t for t in trans if not t.charges]

    unmatched_charges = [c for c in charges if not c.matched]
    matched_charges = [c for c in charges if c.matched]

    unmatched_trans = [t for t in trans if not t.charges]
    matched_trans = [t for t in trans if t.charges]

    num_gift_card = 0
    num_gift_card = len(
        [
            c
            for c in unmatched_charges
            if "Gift Certificate/Card" in c.payment_instrument_types()
        ]
    )
    num_unshipped = len([c for c in unmatched_charges if not c.transact_date()])

    # matched_refunds = [r for r in refunds if r.matched]

    stats["earliest_transaction_date"] = (
        min([t.date for t in unmatched_trans]) if unmatched_trans else None
    )
    stats["latest_transaction_date"] = (
        max([t.date for t in unmatched_trans]) if unmatched_trans else None
    )

    stats["trans_unmatch"] = len(unmatched_trans)
    stats["order_unmatch"] = len(unmatched_charges)
    # stats['refund_unmatch'] = len(unmatched_refunds)
    stats["trans_match"] = len(matched_trans)
    stats["order_match"] = len(matched_charges)
    # stats['refund_match'] = len(matched_refunds)
    stats["skipped_charges_gift_card"] = num_gift_card
    stats["skipped_charges_unshipped"] = num_unshipped

    updateCounter = progress_factory("Determining Mint Updates", len(matched_trans))
    updates = []
    for t in matched_trans:
        updateCounter.next()
        if t.amount < 0:
            charge = amazon.Charge.merge(t.charges)

            prefix = f"{charge.website()}: "
            if args.description_prefix_override:
                prefix = args.description_prefix_override

            if charge.attribute_subtotal_diff_to_misc_charge():
                stats["misc_charge"] += 1
            if charge.attribute_itemized_diff_to_shipping_error():
                stats["rm_shipping_error"] += 1
            if charge.attribute_itemized_diff_to_item_fractional_tax():
                stats["adjust_itemized_tax"] += 1

            assert micro_usd_nearly_equal(t.amount, charge.transact_amount())
            assert micro_usd_nearly_equal(t.amount, -charge.total_by_items())

            new_transactions = charge.to_mint_transactions(
                t, skip_free_shipping=not args.verbose_itemize
            )

        assert micro_usd_nearly_equal(
            t.amount, mint.Transaction.sum_amounts(new_transactions)
        )

        for nt in new_transactions:
            # Look if there's a personal category tagged.
            item_name = amazon.rm_leading_qty(nt.description.lower())
            if (
                mint_historic_category_renames
                and item_name in mint_historic_category_renames
            ):
                suggested_cat = mint_historic_category_renames[item_name]
                if suggested_cat != nt.category.name:
                    stats["personal_cat"] += 1
                    nt.category.name = mint_historic_category_renames[item_name]

            nt.update_category_id(mint_categories)

        summarize_single_item_order = (
            t.amount < 0 and len(charge.items) == 1 and not args.verbose_itemize
        )
        if args.no_itemize or summarize_single_item_order:
            new_transactions = mint.summarize_new_trans(t, new_transactions, prefix)
        else:
            new_transactions = mint.itemize_new_trans(new_transactions, prefix)

        if mint.Transaction.old_and_new_are_identical(
            t, new_transactions, ignore_category=args.no_tag_categories
        ):
            stats["already_up_to_date"] += 1
            continue

        valid_prefixes = args.amazon_domains.lower().split(",") + [prefix.lower()]
        # As per https://github.com/jprouty/mint-amazon-tagger/issues/133, be
        # sure to check for possible prefixes with ": ". Some financial
        # institutions are showing Amazon purchases as "AMAZON.COM ..." in Mint,
        # making a simple prefix search unsuitable.
        has_prefix = any(
            t.description.lower().startswith(pre + ": ") for pre in valid_prefixes
        )
        if has_prefix:
            if args.prompt_retag:
                if args.num_updates > 0 and len(updates) >= args.num_updates:
                    break
                print("\nTransaction already tagged:")
                print_dry_run(
                    [(t, new_transactions)], ignore_category=args.no_tag_categories
                )
                print("\nUpdate tag to proposed? [Yn] ")
                action = readchar.readchar()
                if action == "":
                    exit(1)
                if action not in ("Y", "y", "\r", "\n"):
                    stats["user_skipped_retag"] += 1
                    continue
                stats["retag"] += 1
            elif not args.retag_changed:
                stats["no_retag"] += 1
                continue
            else:
                stats["retag"] += 1
        else:
            stats["new_tag"] += 1
        updates.append((t, new_transactions))

    if args.num_updates > 0:
        updates = updates[: args.num_updates]

    return updates, unmatched_charges
    # return updates, unmatched_charges + unmatched_refunds


def mark_best_as_matched(t, list_of_charges_or_refunds, args, progress=None):
    if not list_of_charges_or_refunds:
        return

    # Only consider it a match if the posted date (transaction date) is
    # within a low number of days of the ship date of the order.
    max_days = args.max_days_between_payment_and_shipping
    closest_match_num_days = max_days + 365  # Large number
    closest_match = None

    for charges in list_of_charges_or_refunds:
        last_shipment = max([c.transact_date() for c in charges if c.transact_date()])
        if not last_shipment:
            continue
        num_days = (t.date - last_shipment).days
        # TODO: consider charges even if it has a matched_transaction if this
        # transaction is closer.
        already_matched = any([c.matched for c in charges])
        if (
            num_days <= max_days
            and num_days >= 0
            and num_days < closest_match_num_days
            and not already_matched
        ):
            closest_match = charges
            closest_match_num_days = num_days

    if closest_match:
        for c in closest_match:
            c.match(t)

        t.match(closest_match)
        if progress:
            progress.next(len(closest_match))


def match_transactions_orig(unmatched_trans, unmatched_charges, args, progress=None):
    # First pass: Match up transactions that exactly equal an order's charged
    # amount.
    amount_to_charges = defaultdict(list)

    for c in unmatched_charges:
        amount_to_charges[c.transact_amount()].append([c])

    for t in unmatched_trans:
        mark_best_as_matched(t, amount_to_charges[t.amount], args, progress)

    unmatched_charges = [c for c in unmatched_charges if not c.matched]
    unmatched_trans = [t for t in unmatched_trans if not t.charges]

    # Second pass: Match up transactions to a combination of charges (sometimes
    # they are charged together).
    oid_to_charges = defaultdict(list)
    for c in unmatched_charges:
        oid_to_charges[c.order_id()].append(c)

    amount_to_charges = defaultdict(list)
    for charges_same_id in oid_to_charges.values():
        if len(charges_same_id) == 1:
            continue

        # Expanding all combinations does not scale, so short-circuit out order ids that have a high unmatched count
        if len(charges_same_id) > args.max_unmatched_charges_combinations:
            continue

        combos = []
        for r in range(2, len(charges_same_id) + 1):
            combos.extend(itertools.combinations(charges_same_id, r))
        for c in combos:
            charges_total = sum([charge.transact_amount() for charge in c])
            amount_to_charges[charges_total].append(c)

    for t in unmatched_trans:
        mark_best_as_matched(t, amount_to_charges[t.amount], args, progress)


def match_transactions_orig_inverted(
    unmatched_trans, unmatched_charges, args, progress=None
):
    # Second pass: Match up transactions to a combination of charges (sometimes
    # they are charged together).
    oid_to_charges = defaultdict(list)
    for c in unmatched_charges:
        oid_to_charges[c.order_id()].append(c)

    amount_to_charges = defaultdict(list)
    for charges_same_id in oid_to_charges.values():
        if len(charges_same_id) == 1:
            continue

        # Expanding all combinations does not scale, so short-circuit out order ids that have a high unmatched count
        if len(charges_same_id) > args.max_unmatched_charges_combinations:
            continue

        combos = []
        for r in range(2, len(charges_same_id) + 1):
            combos.extend(itertools.combinations(charges_same_id, r))
        for c in combos:
            charges_total = sum([charge.transact_amount() for charge in c])
            amount_to_charges[charges_total].append(c)

    for t in unmatched_trans:
        mark_best_as_matched(t, amount_to_charges[t.amount], args, progress)

    unmatched_charges = [c for c in unmatched_charges if not c.matched]
    unmatched_trans = [t for t in unmatched_trans if not t.charges]

    # First pass: Match up transactions that exactly equal an order's charged
    # amount.
    amount_to_charges = defaultdict(list)

    for c in unmatched_charges:
        amount_to_charges[c.transact_amount()].append([c])

    for t in unmatched_trans:
        mark_best_as_matched(t, amount_to_charges[t.amount], args, progress)


def match_transactions_all_combo_singles(
    unmatched_trans, unmatched_charges, args, progress=None
):
    # First pass: Match up transactions where all charges are charged together for orders with more than one item:
    oid_to_charges = defaultdict(list)
    for c in unmatched_charges:
        oid_to_charges[c.order_id()].append(c)

    amount_to_charges = defaultdict(list)
    for charges_same_id in oid_to_charges.values():
        if len(charges_same_id) == 1:
            continue

        charges_total = sum([charge.transact_amount() for charge in charges_same_id])
        amount_to_charges[charges_total].append(charges_same_id)

    for t in unmatched_trans:
        mark_best_as_matched(t, amount_to_charges[t.amount], args, progress)

    unmatched_charges = [c for c in unmatched_charges if not c.matched]
    unmatched_trans = [t for t in unmatched_trans if not t.charges]

    # Second pass: Match up transactions to a combination of charges (but not all, and not singletons).
    oid_to_charges = defaultdict(list)
    for c in unmatched_charges:
        oid_to_charges[c.order_id()].append(c)

    amount_to_charges = defaultdict(list)
    for charges_same_id in oid_to_charges.values():
        if len(charges_same_id) == 1:
            continue

        # Expanding all combinations does not scale, so short-circuit out order ids that have a high unmatched count
        if len(charges_same_id) > args.max_unmatched_charges_combinations:
            continue

        combos = []
        for r in range(2, len(charges_same_id)):
            combos.extend(itertools.combinations(charges_same_id, r))
        for c in combos:
            charges_total = sum([charge.transact_amount() for charge in c])
            amount_to_charges[charges_total].append(c)

    for t in unmatched_trans:
        mark_best_as_matched(t, amount_to_charges[t.amount], args, progress)

    unmatched_charges = [c for c in unmatched_charges if not c.matched]
    unmatched_trans = [t for t in unmatched_trans if not t.charges]

    # Third pass: Match up transactions that exactly equal an order's charged
    # amount.
    amount_to_charges = defaultdict(list)

    for c in unmatched_charges:
        amount_to_charges[c.transact_amount()].append([c])

    for t in unmatched_trans:
        mark_best_as_matched(t, amount_to_charges[t.amount], args, progress)


def match_transactions_single_pass_singletons(
    unmatched_trans, unmatched_charges, args, progress=None
):
    # First pass: Match up transactions that exactly equal an order's charged
    # amount.
    amount_to_charges = defaultdict(list)

    for c in unmatched_charges:
        amount_to_charges[c.transact_amount()].append([c])

    for t in unmatched_trans:
        mark_best_as_matched(t, amount_to_charges[t.amount], args, progress)


def match_transactions_single_pass_multi_combos(
    unmatched_trans, unmatched_charges, args, progress=None
):
    # Match up transactions to a combination of charges (sometimes they are charged together).
    oid_to_charges = defaultdict(list)
    for c in unmatched_charges:
        oid_to_charges[c.order_id()].append(c)

    amount_to_charges = defaultdict(list)
    for charges_same_id in oid_to_charges.values():
        if len(charges_same_id) == 1:
            continue

        # Expanding all combinations does not scale, so short-circuit out order ids that have a high unmatched count
        if len(charges_same_id) > args.max_unmatched_charges_combinations:
            continue

        combos = []
        for r in range(2, len(charges_same_id) + 1):
            combos.extend(itertools.combinations(charges_same_id, r))
        for c in combos:
            charges_total = sum([charge.transact_amount() for charge in c])
            amount_to_charges[charges_total].append(c)

    for t in unmatched_trans:
        mark_best_as_matched(t, amount_to_charges[t.amount], args, progress)


def match_transactions_single_pass_all_combos(
    unmatched_trans, unmatched_charges, args, progress=None
):
    # Match up transactions to a combination of charges (sometimes they are charged together).
    oid_to_charges = defaultdict(list)
    for c in unmatched_charges:
        oid_to_charges[c.order_id()].append(c)

    amount_to_charges = defaultdict(list)
    for charges_same_id in oid_to_charges.values():
        # Expanding all combinations does not scale, so short-circuit out order ids that have a high unmatched count
        if len(charges_same_id) > args.max_unmatched_charges_combinations:
            continue

        combos = []
        for r in range(1, len(charges_same_id) + 1):
            combos.extend(itertools.combinations(charges_same_id, r))
        for c in combos:
            charges_total = sum([charge.transact_amount() for charge in c])
            amount_to_charges[charges_total].append(c)

    for t in unmatched_trans:
        mark_best_as_matched(t, amount_to_charges[t.amount], args, progress)


def match_transactions_orig_with_shipment_merge1(
    unmatched_trans, unmatched_charges, args, progress=None
):
    # THIS IS NOT ALWAYS THE CASE: I HAVE FOUND A CASE WERE THE SHIPMENT ITEM AMOUNTS WERE ACTUALLY SPLIT INTO TWO CC CHARGES FOR THE SAME CARD FOR AN ORDER THAT SHIPPED IN ONE BOX.
    # Merge charges if both the order id and the shipment item amount + shipment item tax align with total owed.
    # ie: Combine items into a charge that have matching:
    # - 'order id'
    # - 'shipment item subtotal'
    # - 'shipment item subtotal tax'
    oid_to_items = defaultdict(list)
    for c in unmatched_charges:
        for i in c.items:
            oid_to_items[
                (i.order_id, i.shipment_item_subtotal, i.shipment_item_subtotal_tax)
            ].append(i)

    unmatched_charges.clear()
    for items_same_id in oid_to_items.values():
        if len(items_same_id) == 1:
            unmatched_charges.append(amazon.Charge(items_same_id))
            continue
        first_item = items_same_id[0]
        item_subtotal = (
            first_item.shipment_item_subtotal + first_item.shipment_item_subtotal_tax
        )
        discounts = sum(i.total_discounts for i in items_same_id)
        shipping_charges = sum(i.shipping_charge for i in items_same_id)
        total_owed = item_subtotal + discounts + shipping_charges
        is_same_charge = all(
            [
                (
                    i.shipment_item_subtotal == first_item.shipment_item_subtotal
                    and i.shipment_item_subtotal_tax
                    == first_item.shipment_item_subtotal_tax
                )
                for i in items_same_id
            ]
        ) and total_owed == sum(i.total_owed for i in items_same_id)
        if is_same_charge:
            unmatched_charges.append(amazon.Charge(items_same_id))
        else:
            # Something doesn't match up (could be same charge but two different shipments).
            # These will be cleaned up later with the combo matching logic per same order.
            unmatched_charges.extend([amazon.Charge([i]) for i in items_same_id])
    match_transactions_orig(unmatched_trans, unmatched_charges, args, progress=None)


def match_transactions_orig_with_shipment_merge2(
    unmatched_trans, unmatched_charges, args, progress=None
):
    # THIS IS NOT ALWAYS THE CASE: I HAVE FOUND A CASE WERE THE SHIPMENT ITEM AMOUNTS WERE ACTUALLY SPLIT INTO TWO CC CHARGES FOR THE SAME CARD FOR AN ORDER THAT SHIPPED IN ONE BOX.
    # Merge charges if both the order id and the shipment item amount + shipment item tax align with total owed.
    # ie: Combine items into a charge that have matching:
    # - 'order id'
    # - 'shipment item subtotal'
    # - 'shipment item subtotal tax'
    oid_to_items = defaultdict(list)
    for c in unmatched_charges:
        for i in c.items:
            oid_to_items[
                (i.order_id, i.shipment_item_subtotal, i.shipment_item_subtotal_tax)
            ].append(i)

    unmatched_charges.clear()
    for items_same_id in oid_to_items.values():
        if len(items_same_id) == 1:
            unmatched_charges.append(amazon.Charge(items_same_id))
            continue

        items_by_shipment = defaultdict(list)
        for i in items_same_id:
            items_by_shipment[
                (i.shipment_item_subtotal, i.shipment_item_subtotal_tax)
            ].append(i)

        for items in items_by_shipment.values():
            charge = amazon.Charge(items)
            if len(items) == 1:
                unmatched_charges.append(charge)
                continue
            total_owed_by_shipment_details = (
                items[0].shipment_item_subtotal
                + items[0].shipment_item_subtotal_tax
                + charge.total_discounts()
                + charge.shipping_charge()
            )
            if total_owed_by_shipment_details == charge.total_owed():
                unmatched_charges.append(charge)
            else:
                unmatched_charges.extend([amazon.Charge([i]) for i in items_same_id])

    match_transactions_orig(unmatched_trans, unmatched_charges, args, progress=None)


def match_transactions(unmatched_trans, unmatched_charges, args, progress=None):
    # Also works with Refund objects.
    # First pass: Match up transactions that exactly equal an order's charged
    # amount.
    amount_to_charges = defaultdict(list)

    for c in unmatched_charges:
        amount_to_charges[c.transact_amount()].append([c])

    for t in unmatched_trans:
        mark_best_as_matched(t, amount_to_charges[t.amount], args, progress)

    unmatched_charges = [c for c in unmatched_charges if not c.matched]
    unmatched_trans = [t for t in unmatched_trans if not t.charges]

    # Second pass: Match up transactions to a combination of charges (sometimes
    # they are charged together).
    oid_to_charges = defaultdict(list)
    for c in unmatched_charges:
        oid_to_charges[c.order_id()].append(c)

    amount_to_charges = defaultdict(list)
    for charges_same_id in oid_to_charges.values():
        if len(charges_same_id) == 1:
            continue

        # Expanding all combinations does not scale, so short-circuit out order ids that have a high unmatched count
        if len(charges_same_id) > args.max_unmatched_charges_combinations:
            continue

        combos = []
        for r in range(2, len(charges_same_id) + 1):
            combos.extend(itertools.combinations(charges_same_id, r))
        for c in combos:
            charges_total = sum([charge.transact_amount() for charge in c])
            amount_to_charges[charges_total].append(c)

    for t in unmatched_trans:
        mark_best_as_matched(t, amount_to_charges[t.amount], args, progress)


def print_dry_run(orig_trans_to_tagged, ignore_category=False):
    for orig_trans, new_trans in orig_trans_to_tagged:
        oid = orig_trans.charges[0].order_id()
        order_type = "Order" if orig_trans.amount < 0 else "Refund"
        print(
            f"\nFor Amazon {order_type}: {oid}\n"
            f"Invoice URL: {amazon.get_invoice_url(oid)}"
        )

        if orig_trans.children:
            for i, trans in enumerate(orig_trans.children):
                print(
                    "{}{}) Current: \t{}".format(
                        "\n" if i == 0 else "", i + 1, trans.dry_run_str()
                    )
                )
        else:
            print(f"\nCurrent: \t{orig_trans.dry_run_str()}")

        if len(new_trans) == 1:
            trans = new_trans[0]
            print(f"\nProposed: \t{trans.dry_run_str(ignore_category)}")
        else:
            for i, trans in enumerate(reversed(new_trans)):
                print(
                    "{}{}) Proposed: \t{}".format(
                        "\n" if i == 0 else "",
                        i + 1,
                        trans.dry_run_str(ignore_category),
                    )
                )
