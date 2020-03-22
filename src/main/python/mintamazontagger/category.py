from range_key_dict import RangeKeyDict


# The default Mint category.
DEFAULT_MINT_CATEGORY = 'Shopping'

# The default return category.
DEFAULT_MINT_RETURN_CATEGORY = 'Returned Purchase'

# A range-based nested dictionary that represents UNSPSC category codes mapped
# to the Mint category taxonomy. Nodes can be either a RangeKeyDict (meaning
# look another level deeper during lookup), or a str, meaning this cateogory is
# valid for this sub-tree of the UNSPSC taxonomy. For each sub-level, 00 is
# used to denote a default category.
UNSPSC_TO_MINT_CATEGORY = RangeKeyDict({
    (10, 11): RangeKeyDict({
        (10, 11): 'Pets',
        (11, 15): 'Pet Food & Supplies',
        (15, 51): 'Lawn & Garden',
    }),
    (13, 14): 'Home Supplies',
    (14, 15): RangeKeyDict({
        (11, 12): RangeKeyDict({
            (0, 1): 'Office Supplies',
            (17, 18): 'Home Supplies',  # Paper products like TP, paper towels
        }),
    }),
    (20, 25): 'Home Supplies',
    (25, 26): 'Service & Parts',  # Auto
    (26, 27): 'Electronics & Software',  # Batteries/cables
    (27, 28): 'Home Supplies',  # Tools
    (30, 32): 'Home Improvement',  # Building mtls/plumbing/hardware/tape/glue
    (32, 33): 'Electronics & Software',  # Computers!
    (39, 40): 'Home Improvement',  # Lights and lighting accessories/cords
    (40, 41): RangeKeyDict({  # Mostly home improvement/parts
        (0, 1): 'Home Improvement',
        (16, 17): RangeKeyDict({
            (15, 16): RangeKeyDict({
                (4, 6): 'Service & Parts',  # Car oil/air filters
            }),
        }),
    }),
    (41, 42): 'Home Supplies',  # Tools and measurement equip
    (42, 43): 'Personal Care',  # Medical
    (43, 44): 'Electronics & Software',  # Computers/networking/cables
    (44, 45): 'Office Supplies',
    (45, 46): 'Electronics & Software',  # Cameras and AV gear
    (46, 47): RangeKeyDict({
        (17, 18): 'Electronics & Software',  # Security cams/etc
        (18, 19): 'Home Supplies',  # Gloves and other personal consumables
    }),
    (47, 49): 'Home Supplies',
    (49, 50): 'Sporting Goods',
    # Personal care, but has groceries mixed up. I need more grocery examples
    # to clean this up.
    (50, 52): 'Personal Care',
    (52, 53): RangeKeyDict({
        (0, 1): 'Home Supplies',
        (16, 17): 'Electronics & Software',  # More AV/audio/speaker gear
    }),
    (53, 54): RangeKeyDict({
        (0, 1): 'Clothing',
        (13, 14): 'Personal Care',
    }),
    (55, 56): RangeKeyDict({
        (10, 11): 'Books',
        (11, 12): RangeKeyDict({
            (15, 16): RangeKeyDict({
                (12, 13): 'Music',
                (14, 15): 'Movies & DVDs',
            }),
        }),
    }),
    (56, 57): RangeKeyDict({
        (0, 1): 'Home Supplies',
        (10, 11): RangeKeyDict({
            (16, 17): 'Lawn & Garden',
            (18, 19): 'Baby Supplies',
        }),
    }),
    (60, 61): RangeKeyDict({
        (10, 11): 'Electronics & Software',
        (12, 13): 'Arts',
        (13, 14): 'Music',
        (14, 15): 'Toys',  # AMZN Also mixes hobbies in :(
    }),
})


def get_mint_category_from_unspsc(unspsc_code):
    """Traverses the UNSPSC tree to find a Mint category for unspsc_code."""
    if not unspsc_code:
        return DEFAULT_MINT_CATEGORY
    if type(unspsc_code) != int:
        unspsc_code = int(unspsc_code)

    segment_code = unspsc_code // 1000000 % 100
    segment_node = UNSPSC_TO_MINT_CATEGORY.get(
        segment_code, DEFAULT_MINT_CATEGORY)
    if type(segment_node) is str:
        return segment_node
    elif type(segment_node) is not RangeKeyDict:
        return DEFAULT_MINT_CATEGORY

    segment_default = segment_node.get(0, DEFAULT_MINT_CATEGORY)
    family_code = unspsc_code // 10000 % 100
    family_node = segment_node.get(
        family_code, segment_default)
    if type(family_node) is str:
        return family_node
    elif type(family_node) is not RangeKeyDict:
        return segment_default

    family_default = family_node.get(0, segment_default)
    class_code = unspsc_code // 100 % 100
    class_node = family_node.get(
        class_code, family_node.get(0, family_default))
    if type(class_node) is str:
        return class_node
    elif type(class_node) is not RangeKeyDict:
        return family_default

    class_default = class_node.get(0, family_default)
    commodity_code = unspsc_code % 100
    commodity_node = class_node.get(
        commodity_code, class_node.get(0, class_default))
    if type(commodity_node) is str:
        return commodity_node

    return commodity_node.get(0, class_default)


# Pulled early 2018.
DEFAULT_MINT_CATEGORIES_TO_IDS = {
    'ATM Fee': 1605,
    'Advertising': 1701,
    'Air Travel': 1501,
    'Alcohol & Bars': 708,
    'Allowance': 610,
    'Amusement': 102,
    'Arts': 101,
    'Auto & Transport': 14,
    'Auto Insurance': 1405,
    'Auto Payment': 1404,
    'Baby Supplies': 611,
    'Babysitter & Daycare': 602,
    'Bank Fee': 1606,
    'Bills & Utilities': 13,
    'Bonus': 3004,
    'Books': 202,
    'Books & Supplies': 1003,
    'Business Services': 17,
    'Buy': 5004,
    'Cash & ATM': 2001,
    'Charity': 802,
    'Check': 2002,
    'Child Support': 603,
    'Clothing': 201,
    'Coffee Shops': 704,
    'Credit Card Payment': 2101,
    'Dentist': 501,
    'Deposit': 5001,
    'Dividend & Cap Gains': 5003,
    'Doctor': 502,
    'Education': 10,
    'Electronics & Software': 204,
    'Entertainment': 1,
    'Eyecare': 503,
    'Fast Food': 706,
    'Federal Tax': 1901,
    'Fees & Charges': 16,
    'Finance Charge': 1604,
    'Financial': 11,
    'Financial Advisor': 1105,
    'Food & Dining': 7,
    'Furnishings': 1201,
    'Gas & Fuel': 1401,
    'Gift': 801,
    'Gifts & Donations': 8,
    'Groceries': 701,
    'Gym': 507,
    'Hair': 403,
    'Health & Fitness': 5,
    'Health Insurance': 506,
    'Hide from Budgets & Trends': 40,
    'Hobbies': 206,
    'Home': 12,
    'Home Improvement': 1203,
    'Home Insurance': 1206,
    'Home Phone': 1302,
    'Home Services': 1204,
    'Home Supplies': 1208,
    'Hotel': 1502,
    'Income': 30,
    'Interest Income': 3005,
    'Internet': 1303,
    'Investments': 50,
    'Kids': 6,
    'Kids Activities': 609,
    'Kitchen': 1562103,
    'Late Fee': 1602,
    'Laundry': 406,
    'Lawn & Garden': 1202,
    'Legal': 1705,
    'Life Insurance': 1102,
    'Loan Fees and Charges': 6005,
    'Loan Insurance': 6002,
    'Loan Interest': 6004,
    'Loan Payment': 6001,
    'Loan Principal': 6003,
    'Loans': 60,
    'Local Tax': 1903,
    'Misc Expenses': 70,
    'Mobile Phone': 1304,
    'Mortgage & Rent': 1207,
    'Movies & DVDs': 104,
    'Music': 103,
    'Newspapers & Magazines': 105,
    'Office Supplies': 1702,
    'Orthodontics': 1671958,
    'Parking': 1402,
    'Paycheck': 3001,
    'Personal Care': 4,
    'Pet Food & Supplies': 901,
    'Pet Grooming': 902,
    'Pets': 9,
    'Pharmacy': 505,
    'Printing': 1703,
    'Property Tax': 1905,
    'Public Transportation': 1406,
    'Rail': 1562093,
    'Reimbursement': 3006,
    'Rental Car & Taxi': 1503,
    'Rental Income': 3007,
    'Restaurants': 707,
    'Returned Purchase': 3003,
    'Sales Tax': 1904,
    'Sell': 5005,
    'Service & Parts': 1403,
    'Service Fee': 1601,
    'Shipping': 1704,
    'Shopping': 2,
    'Spa & Massage': 404,
    'Sporting Goods': 207,
    'Sports': 508,
    'State Tax': 1902,
    'Student Loan': 1002,
    'Taxes': 19,
    'Television': 1301,
    'Toys': 606,
    'Trade Commissions': 1607,
    'Transfer': 21,
    'Transfer for Cash Spending': 2102,
    'Travel': 15,
    'Tuition': 1001,
    'Uncategorized': 20,
    'Utilities': 1306,
    'Vacation': 1504,
    'Veterinary': 903,
    'Withdrawal': 5002,
}
