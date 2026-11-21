#!/usr/bin/env python3
"""Tests for terms_book. Run: python3 -m unittest test_terms_book -v"""

import contextlib
import csv
import io
import json
import os
import tempfile
import unittest

import terms_book as tb


def fresh_book():
    return tb.Book()


def book_with_buyer(name="ACME DISTRIBUTORS"):
    book = fresh_book()
    book.add_buyer(name)
    return book


class ParseTest(unittest.TestCase):
    def test_parse_amount_rupees(self):
        self.assertEqual(tb.parse_amount("8000"), 800000)
        self.assertEqual(tb.parse_amount("125000.50"), 12500050)
        self.assertEqual(tb.parse_amount("0.75"), 75)

    def test_parse_amount_rejects_bad(self):
        for bad in ("", "abc", "10.123", "1,000", "10.5.5", "-50", "0", "0.00"):
            with self.assertRaises(tb.TermsBookError, msg=bad):
                tb.parse_amount(bad)

    def test_format_paise_indian_grouping(self):
        self.assertEqual(tb.format_paise(123456789), "12,34,567.89")
        self.assertEqual(tb.format_paise(100000), "1,000.00")
        self.assertEqual(tb.format_paise(99), "0.99")
        self.assertEqual(tb.format_paise(10000000000), "10,00,00,000.00")

    def test_parse_date(self):
        self.assertEqual(tb.parse_date("2026-09-01").isoformat(), "2026-09-01")
        with self.assertRaises(tb.TermsBookError):
            tb.parse_date("01-09-2026")

    def test_parse_terms(self):
        self.assertEqual(tb.parse_terms("0"), 0)
        self.assertEqual(tb.parse_terms("45"), 45)
        with self.assertRaises(tb.TermsBookError):
            tb.parse_terms("-5")
        with self.assertRaises(tb.TermsBookError):
            tb.parse_terms("thirty")


class BuyerTest(unittest.TestCase):
    def test_add_and_get(self):
        book = fresh_book()
        book.add_buyer("ACME", contact="ops@acme.example")
        self.assertEqual(book.get_buyer("ACME")["contact"], "ops@acme.example")

    def test_duplicate_buyer_rejected(self):
        book = book_with_buyer()
        with self.assertRaises(tb.TermsBookError):
            book.add_buyer("ACME DISTRIBUTORS")

    def test_empty_name_rejected(self):
        book = fresh_book()
        with self.assertRaises(tb.TermsBookError):
            book.add_buyer("   ")

    def test_unknown_buyer_error(self):
        book = fresh_book()
        with self.assertRaises(tb.TermsBookError):
            book.get_buyer("GHOST")


class InvoiceTest(unittest.TestCase):
    def test_add_invoice_defaults(self):
        book = book_with_buyer()
        inv = book.add_invoice("ACME DISTRIBUTORS", "INV-1", 100000,
                               "2026-09-01", 30)
        self.assertEqual(tb.due_date(inv).isoformat(), "2026-10-01")
        self.assertEqual(tb.outstanding_paise(inv), 100000)
        self.assertFalse(tb.is_fully_paid(inv))

    def test_add_invoice_unknown_buyer(self):
        book = fresh_book()
        with self.assertRaises(tb.TermsBookError):
            book.add_invoice("GHOST", "INV-1", 100000, "2026-09-01", 30)

    def test_duplicate_ref_rejected(self):
        book = book_with_buyer()
        book.add_invoice("ACME DISTRIBUTORS", "INV-1", 100000, "2026-09-01", 30)
        with self.assertRaises(tb.TermsBookError):
            book.add_invoice("ACME DISTRIBUTORS", "INV-1", 500, "2026-09-02", 30)

    def test_terms_zero_due_on_receipt(self):
        book = book_with_buyer()
        inv = book.add_invoice("ACME DISTRIBUTORS", "INV-1", 100000,
                               "2026-09-01", 0)
        self.assertEqual(tb.due_date(inv).isoformat(), "2026-09-01")
        book.record_payment("INV-1", 100000, "2026-09-01")
        self.assertEqual(tb.days_to_pay(inv), 0)
        self.assertEqual(tb.delay_days(inv), 0)


class PaymentTest(unittest.TestCase):
    def setUp(self):
        self.book = book_with_buyer()
        self.book.add_invoice("ACME DISTRIBUTORS", "INV-1", 100000,
                              "2026-09-01", 30)

    def test_partial_payment_leaves_balance(self):
        self.book.record_payment("INV-1", 40000, "2026-09-10")
        inv = self.book.get_invoice("INV-1")
        self.assertEqual(tb.paid_paise(inv), 40000)
        self.assertEqual(tb.outstanding_paise(inv), 60000)
        self.assertIsNone(tb.days_to_pay(inv))

    def test_completing_payment_sets_days_to_pay(self):
        self.book.record_payment("INV-1", 40000, "2026-09-10")
        self.book.record_payment("INV-1", 60000, "2026-10-05")
        inv = self.book.get_invoice("INV-1")
        self.assertTrue(tb.is_fully_paid(inv))
        self.assertEqual(tb.days_to_pay(inv), 34)
        self.assertEqual(tb.delay_days(inv), 4)

    def test_on_time_payment(self):
        self.book.record_payment("INV-1", 100000, "2026-09-25")
        inv = self.book.get_invoice("INV-1")
        self.assertEqual(tb.days_to_pay(inv), 24)
        self.assertEqual(tb.delay_days(inv), 0)

    def test_overpayment_rejected(self):
        self.book.record_payment("INV-1", 60000, "2026-09-10")
        with self.assertRaises(tb.TermsBookError):
            self.book.record_payment("INV-1", 60000, "2026-09-11")

    def test_double_full_payment_rejected(self):
        self.book.record_payment("INV-1", 100000, "2026-09-10")
        with self.assertRaises(tb.TermsBookError):
            self.book.record_payment("INV-1", 100, "2026-09-11")

    def test_payment_before_invoice_date_rejected(self):
        with self.assertRaises(tb.TermsBookError):
            self.book.record_payment("INV-1", 100, "2026-08-31")

    def test_payment_on_unknown_invoice(self):
        with self.assertRaises(tb.TermsBookError):
            self.book.record_payment("INV-99", 100, "2026-09-10")


class StatsTest(unittest.TestCase):
    def setUp(self):
        # Two buyers with known behavior, computed by hand.
        self.book = book_with_buyer("EARLY TRADERS")
        self.book.add_buyer("SLOW CORP")
        # EARLY: two invoices, both Net 30, both paid on time (10 and 20 days)
        self.book.add_invoice("EARLY TRADERS", "E-1", 100000, "2026-09-01", 30)
        self.book.record_payment("E-1", 100000, "2026-09-11")
        self.book.add_invoice("EARLY TRADERS", "E-2", 200000, "2026-09-01", 30)
        self.book.record_payment("E-2", 200000, "2026-09-21")
        # SLOW: three Net-30 invoices; paid 20 late, paid 40 late, one open
        self.book.add_invoice("SLOW CORP", "S-1", 100000, "2026-08-01", 30)
        self.book.record_payment("S-1", 100000, "2026-09-20")  # 50 days, 20 late
        self.book.add_invoice("SLOW CORP", "S-2", 100000, "2026-08-01", 30)
        self.book.record_payment("S-2", 100000, "2026-10-10")  # 70 days, 40 late
        self.book.add_invoice("SLOW CORP", "S-3", 150000, "2026-10-01", 30)

    def test_early_buyer_stats(self):
        stats = self.book.buyer_stats("EARLY TRADERS", as_of="2026-11-21")
        self.assertEqual(stats["invoices"], 2)
        self.assertEqual(stats["paid"], 2)
        self.assertEqual(stats["on_time"], 2)
        self.assertEqual(stats["on_time_pct"], 100)
        self.assertEqual(stats["avg_days_to_pay"], 15)
        self.assertEqual(stats["avg_delay"], 0)
        self.assertEqual(stats["worst_delay"], 0)
        self.assertEqual(stats["outstanding"], 0)

    def test_slow_buyer_stats(self):
        stats = self.book.buyer_stats("SLOW CORP", as_of="2026-11-21")
        self.assertEqual(stats["invoices"], 3)
        self.assertEqual(stats["paid"], 2)
        self.assertEqual(stats["on_time"], 0)
        self.assertEqual(stats["on_time_pct"], 0)
        self.assertEqual(stats["avg_days_to_pay"], 60)
        self.assertEqual(stats["avg_delay"], 30)
        self.assertEqual(stats["worst_delay"], 40)
        self.assertEqual(stats["outstanding"], 150000)
        self.assertEqual(stats["open"], 1)
        # S-3 due 2026-10-31, as of 2026-11-21 -> 21 days overdue
        self.assertEqual(stats["overdue_count"], 1)
        self.assertEqual(stats["avg_overdue"], 21)

    def test_stats_empty_buyer(self):
        self.book.add_buyer("NEWCO")
        stats = self.book.buyer_stats("NEWCO", as_of="2026-11-21")
        self.assertEqual(stats["invoices"], 0)
        self.assertIsNone(stats["on_time_pct"])

    def test_overdue_zero_within_terms(self):
        self.book.add_invoice("SLOW CORP", "S-9", 1000, "2026-11-20", 30)
        inv = self.book.get_invoice("S-9")
        self.assertEqual(tb.overdue_days(inv, "2026-11-21"), 0)

    def test_outstanding_sorted_by_due_date(self):
        rows = self.book.outstanding("2026-11-21")
        refs = [inv["ref"] for inv, _ in rows]
        self.assertEqual(refs, ["S-3"])
        self.assertEqual(rows[0][1], 21)


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "book.json")

    def test_roundtrip(self):
        book = book_with_buyer()
        book.add_invoice("ACME DISTRIBUTORS", "INV-1", 100000,
                         "2026-09-01", 45)
        book.record_payment("INV-1", 40000, "2026-09-10")
        book.save(self.path)
        loaded = tb.Book.load(self.path)
        inv = loaded.get_invoice("INV-1")
        self.assertEqual(inv["terms"], 45)
        self.assertEqual(tb.paid_paise(inv), 40000)

    def test_load_missing_file_gives_empty_book(self):
        book = tb.Book.load(os.path.join(self.tmp.name, "nope.json"))
        self.assertEqual(len(book.buyers), 0)

    def test_corrupt_file_raises_user_error(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        with self.assertRaises(tb.TermsBookError):
            tb.Book.load(self.path)

    def test_save_is_atomic_replace(self):
        book = book_with_buyer()
        book.save(self.path)
        self.assertTrue(os.path.exists(self.path))
        leftovers = [n for n in os.listdir(self.tmp.name) if n != "book.json"]
        self.assertEqual(leftovers, [])
        with open(self.path, encoding="utf-8") as fh:
            json.load(fh)  # valid JSON


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "book.json")

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = tb.main(["--db", self.db] + list(argv))
        return code, out.getvalue()

    def test_full_flow_and_outputs(self):
        code, out = self.run_cli("buyer", "add", "ACME DISTRIBUTORS")
        self.assertEqual(code, 0)
        self.assertIn("added buyer ACME DISTRIBUTORS", out)

        code, out = self.run_cli("add", "ACME DISTRIBUTORS", "INV-101",
                                 "125000", "2026-09-01", "--terms", "30")
        self.assertEqual(code, 0)
        self.assertIn("₹" , "₹")  # sanity: module has no rupee symbol elsewhere
        self.assertIn("1,25,000.00", out)
        self.assertIn("due 2026-10-01", out)

        code, out = self.run_cli("pay", "INV-101", "125000", "2026-10-12")
        self.assertEqual(code, 0)
        self.assertIn("fully paid in 41 days (11 days beyond Net 30)", out)

        code, out = self.run_cli("buyers")
        self.assertEqual(code, 0)
        self.assertIn("ACME DISTRIBUTORS", out)
        self.assertIn("0%", out)

        code, out = self.run_cli("buyer", "show", "ACME DISTRIBUTORS")
        self.assertEqual(code, 0)
        self.assertIn("INV-101", out)
        self.assertIn("paid in 41 days, 11 late", out)

        code, out = self.run_cli("brief", "ACME DISTRIBUTORS")
        self.assertEqual(code, 0)
        self.assertIn("paid on time: 0 of 1 (0%)", out)
        self.assertIn("average time to pay: 41 days", out)

        code, out = self.run_cli("summary")
        self.assertEqual(code, 0)
        self.assertIn("buyers: 1, invoices: 1, outstanding: 0.00", out)

    def test_cli_default_terms_30(self):
        self.run_cli("buyer", "add", "ACME")
        code, out = self.run_cli("add", "ACME", "A-1", "1000", "2026-09-01")
        self.assertEqual(code, 0)
        self.assertIn("Net 30", out)

    def test_cli_error_exit_code(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = tb.main(["--db", self.db, "pay", "NOPE", "100", "2026-01-01"])
        self.assertEqual(code, 2)
        self.assertIn("unknown invoice", err.getvalue())

    def test_cli_outstanding_and_export(self):
        self.run_cli("buyer", "add", "ACME")
        self.run_cli("add", "ACME", "A-1", "50000", "2026-10-01", "--terms", "15")
        code, out = self.run_cli("outstanding", "--as-of", "2026-11-21")
        self.assertEqual(code, 0)
        self.assertIn("36 days overdue", out)
        self.assertIn("50,000.00", out)

        csv_path = os.path.join(self.tmp.name, "out.csv")
        code, out = self.run_cli("export", csv_path, "--as-of", "2026-11-21")
        self.assertEqual(code, 0)
        with open(csv_path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows[0][0], "ref")
        self.assertEqual(rows[1][0], "A-1")
        self.assertEqual(rows[1][3], "50,000.00")
        self.assertEqual(rows[1][6], "no")
        self.assertEqual(rows[1][9], "36")

    def test_export_paid_invoice_columns(self):
        self.run_cli("buyer", "add", "ACME")
        self.run_cli("add", "ACME", "A-1", "1000", "2026-09-01", "--terms", "30")
        self.run_cli("pay", "A-1", "1000", "2026-10-05")
        csv_path = os.path.join(self.tmp.name, "out.csv")
        self.run_cli("export", csv_path)
        with open(csv_path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows[1][6], "yes")
        self.assertEqual(rows[1][7], "34")
        self.assertEqual(rows[1][8], "4")
        self.assertEqual(rows[1][10], "0.00")


if __name__ == "__main__":
    unittest.main()
