

def get_transaction_json(
        amount='$27.46',
        is_debit=True,
        category='Personal Care',
        date='10/8/10',
        description='Amazon.com: Rave ON: Rave Recovery',
        original_description='AMAZON MKTPLACE PMTS',
        pid=None,
        note='Great note here'):
    trans = {
        'account': 'Amazon Visa',
        'amount': amount,
        'category': category,
        'categoryId': 4,
        'date': date,
        'fi': 'Chase Credit Card',
        'hasAttachments': False,
        'id': 975256256,
        'isAfterFiCreationTime': True,
        'isCheck': False,
        'isChild': False,
        'isDebit': is_debit,
        'isDuplicate': False,
        'isEdited': True,
        'isFirstDate': True,
        'isLinkedToRule': False,
        'isMatched': False,
        'isPending': False,
        'isPercent': False,
        'isSpending': True,
        'isTransfer': False,
        'labels': [],
        'manualType': 0,
        'mcategory': 'Restaurants',
        'merchant': description,
        'mmerchant': 'Amazon Marketplace',
        'note': note,
        'number_matched_by_rule': -1,
        'odate': date,
        'omerchant': original_description,
        'ruleCategory': '',
        'ruleCategoryId': 0,
        'ruleMerchant': '',
        'txnType': 0,
        'userCategoryId': 4,
    }
    if pid:
        trans['isChild'] = True
        trans['pid'] = pid
    return trans
