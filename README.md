# terms-book

A zero-dependency command-line payment-terms evidence book for small
businesses. Record each invoice against the terms you agreed with a buyer
(Net 30, Net 45, ...), log payments including partial ones, and the book
answers: which buyers actually pay on time, how many days beyond terms
each one averages, what is still outstanding, and how overdue it is - so
your next terms conversation starts from your own data, not memory.

Single Python file, standard library only (Python 3.9+). Fully offline;
all data lives in one local JSON file you own.

## Why this exists

On Razorpay's public "Fix My Itch" list, one entry asks: "Why can't SMEs
negotiate favorable payment terms with large buyers?" (page fetched and
entry re-verified on 2026-11-21; https://razorpay.com/m/fix-my-itch/).
That list is curated by Razorpay - it shows the problem was submitted and
published, not how common it is or that a tool like this is in demand.

Negotiating better terms needs evidence. A buyer who says "we always pay
on time" is hard to argue with from memory. This book keeps the record
that answers with numbers: on-time percentage, average days beyond the
agreed terms, the worst case, and what is overdue right now.

## What it does

- Buyers: add each buyer once, with an optional contact note.
- Invoices: record ref, amount, invoice date and agreed terms in days
  (`--terms`, default 30). The due date is computed for you.
- Payments: full or partial, each dated. Overpaying an invoice, paying
  before the invoice date, and paying an already-settled invoice are all
  rejected.
- Scorecard (`buyers`): per buyer - invoices, fully paid, on-time
  percentage, average days to pay, average days late, outstanding amount.
- Statement (`buyer show`): every invoice with its terms, due date, and
  how it actually went - paid in N days (on time / N late), or still
  outstanding with days overdue.
- Brief (`brief`): a per-buyer summary written for a terms conversation,
  computed only from invoices you recorded.
- Outstanding (`outstanding`): unpaid invoices sorted by due date, with
  days overdue (optionally `--as-of` a date).
- Export (`export`): all invoices to CSV for a spreadsheet or CA.
- Amounts are stored as integer paise and printed with Indian digit
  grouping (1,25,000.00).

## Install and run

Requires Python 3.9 or newer. Nothing else.

```sh
python3 terms_book.py --help
```

Data is stored in `terms_book.json` next to the script (override with
`--db PATH`).

## Example

```console
$ python3 terms_book.py buyer add ACME DISTRIBUTORS
added buyer ACME DISTRIBUTORS

$ python3 terms_book.py add ACME DISTRIBUTORS INV-101 125000 2026-09-01 --terms 30
recorded invoice INV-101 for ACME DISTRIBUTORS: 1,25,000.00, dated 2026-09-01, Net 30 (due 2026-10-01)

$ python3 terms_book.py add ACME DISTRIBUTORS INV-115 86000.50 2026-09-20 --terms 45
recorded invoice INV-115 for ACME DISTRIBUTORS: 86,000.50, dated 2026-09-20, Net 45 (due 2026-11-04)

$ python3 terms_book.py pay INV-101 125000 2026-10-12
recorded payment of 1,25,000.00 on INV-101; invoice fully paid in 41 days (11 days beyond Net 30)

$ python3 terms_book.py pay INV-115 50000 2026-09-30
recorded payment of 50,000.00 on INV-115; 36,000.50 still due

$ python3 terms_book.py buyers
buyer                    invoices   paid  on time   avg days   avg late    outstanding
ACME DISTRIBUTORS               2      1       0%         41         11      36,000.50

$ python3 terms_book.py brief ACME DISTRIBUTORS --as-of 2026-11-21
ACME DISTRIBUTORS - payment terms brief (as of 2026-11-21)
  invoices recorded: 2; fully paid: 1
  paid on time: 0 of 1 (0%)
  average time to pay: 41 days
  average delay beyond terms (late payers): 11 days; worst: 11 days
  outstanding now: 36,000.50 across 1 invoice(s)
  overdue: 1 invoice(s), average 17 days past due
  (computed only from invoices you recorded in this book)

$ python3 terms_book.py outstanding --as-of 2026-11-21
outstanding as of 2026-11-21:
  INV-115      ACME DISTRIBUTORS             36,000.50  17 days overdue
```

(Every line above is real output from a run of this script, with dates
chosen so the math is easy to check.)

## What else is out there (as of 2026-11-21)

Hosted receivables apps for Indian SMEs exist and are more complete for
day-to-day billing - for example Vyapar, myBillBook, ProfitBooks, NudgePay,
Receivly and FastFee all do invoicing with payment tracking and reminders.
There are also open-source invoice/payment-log CLI projects on GitHub.
This repo does not claim to be the only tool in this space. Its specific
scope is the terms-evidence angle: an offline, zero-install record that
turns your invoice history into per-buyer on-time and days-beyond-terms
statistics for a negotiation, in a file you fully own. If you want
reminders, GST invoicing or payment links, use a hosted app.

## Limits

- No invoicing itself: it does not generate, send or GST-format invoices.
- No reminders, emails or payment links. It is a record and report tool.
- Statistics describe only what you recorded; they are not proof of what
  a buyer did with invoices you never entered.
- "On time" compares the completing payment's date against invoice date +
  terms. Weekends, holidays and partial-payment goodwill are not modeled.
- Single user, single file. No multi-user access, no sync.
- Durability: saves are atomic (write to a temp file, then replace), which
  protects against interruption mid-save, not against deleting the file.
  Back up the JSON file like any other business record.

## Tests

```sh
python3 -m unittest test_terms_book -v
```

34 tests cover money parsing and Indian formatting, invoice/payment rules
(partial payments, overpayment and double-payment rejection, date order),
on-time/delay/overdue math, per-buyer statistics, CSV export, atomic
save/load roundtrip, and the CLI end to end. CI runs the suite on Ubuntu
and Windows against Python 3.9 and 3.13.

## License

MIT. See [LICENSE](LICENSE).
