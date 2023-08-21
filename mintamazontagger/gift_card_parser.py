from datetime import datetime
from pprint import pprint
from recordtype import recordtype
import tabula

def main():
    tables = tabula.read_pdf('Retail.GiftCertificates.Transactions.pdf', pages='all')
    # Only keep the ones with 4 columns:
    tables = [t for t in tables if len(t.columns) == 4]
    # First table has the correct column names:
    GCTransaction = recordtype('GCTransaction', list(tables[0].columns))
    first_table = tables[0]
    trans = []
    for row in first_table.values:
        trans.append(GCTransaction(*row))
    
    for table in tables[1:]:
        # The header (or column names) are actually a data row:
        trans.append(GCTransaction(*table.columns))
        for row in table.values:
            trans.append(GCTransaction(*row))
    for t in trans:
        # Parse all dates into datetime.
        format = '%d-%m-%Y %H:%M'
        t.transactionDate = datetime.strptime(t.transactionDate, format)

    # Only look at MarkShipmentCompletion records:
    trans = [t for t in trans if t.transactionType == 'MarkShipmentCompletion']
    pprint(trans)
    print(len(trans))
    

if __name__ == '__main__':
    main()
