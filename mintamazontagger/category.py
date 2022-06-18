from range_key_dict import RangeKeyDict


# The default Mint category.
DEFAULT_MINT_CATEGORY = 'Shopping'

# The default return category.
DEFAULT_MINT_RETURN_CATEGORY = 'Returned Purchase'

# A range-based nested dictionary that represents UNSPSC category codes mapped
# to the Mint category taxonomy. Nodes can be either a RangeKeyDict (meaning
# look another level deeper during lookup), or a str, meaning this cateogory is
# valid for this sub-tree of the UNSPSC taxonomy. For each sub-level, 00 is
# used to denote a default category (or can be '(0, 1):' as a key).
UNSPSC_TO_MINT_CATEGORY = RangeKeyDict({
    (10, 11): RangeKeyDict({
        (0, 1): 'Lawn & Garden',
        (10, 11): 'Pets',
        (11, 15): 'Pet Food & Supplies',
        (16, 17): 'Arts',  # Fabric
    }),
    (13, 14): 'Home Supplies',
    (14, 15): RangeKeyDict({
        (11, 12): RangeKeyDict({
            (0, 1): 'Office Supplies',
            (17, 18): 'Home Supplies',  # Paper products like TP, paper towels
        }),
    }),
    (15, 16): 'Home Supplies',
    (20, 25): 'Home Supplies',
    (25, 26): 'Service & Parts',  # Auto
    (26, 27): 'Electronics & Software',  # Batteries/cables
    (27, 28): 'Home Improvement',  # Tools
    (30, 32): 'Home Improvement',  # Building mtls/plumbing/hardware/tape/glue
    (32, 33): 'Electronics & Software',  # Computers!
    (39, 40): 'Furnishings',  # Lights and lighting accessories/cords
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
    (43, 44): 'Electronics & Software',  # Computers/networking/cables/gaming
    (44, 45): 'Office Supplies',
    (45, 46): 'Electronics & Software',  # Cameras and AV gear
    (46, 47): RangeKeyDict({
        (0, 1): 'Home Improvement',  # Security cams/smoke detectors/etc
        (18, 19): 'Clothing',  # Gloves and other personal consumables
    }),
    (47, 49): 'Home Supplies',
    (49, 50): 'Sporting Goods',
    # Mostly groceries. Lots of Amazon fresh groceries are simply: 50000000
    (50, 51): 'Groceries',
    (51, 52): 'Personal Care',
    (52, 53): RangeKeyDict({
        (0, 1): 'Home Supplies',
        (14, 15): DEFAULT_MINT_CATEGORY,  # Random - revert to Shopping
        (16, 17): 'Electronics & Software',  # More AV/audio/speaker gear
    }),
    (53, 54): RangeKeyDict({
        (0, 1): 'Clothing',
        (13, 14): 'Personal Care',
    }),
    (54, 55): 'Clothing',
    (55, 56): RangeKeyDict({
        (10, 11): 'Books',
        (11, 12): RangeKeyDict({
            (15, 16): RangeKeyDict({
                (12, 13): 'Music',
                (14, 15): 'Movies & DVDs',
            }),
        }),
        (12, 13): 'Office Supplies',
    }),
    (56, 57): RangeKeyDict({
        (0, 1): 'Home Supplies',
        (10, 11): RangeKeyDict({
            (16, 17): 'Lawn & Garden',
            (17, 18): 'Furnishings',
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
