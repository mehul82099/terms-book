#!/usr/bin/env python3
"""terms-book: zero-dependency payment-terms evidence CLI for small businesses.

Record invoices against the payment terms you agreed with each buyer
(Net 30, Net 45, ...), log payments including partial ones, and the book
answers: which buyers actually pay on time, how many days beyond terms
each one averages, what is still outstanding, and how overdue it is -
so the next terms conversation starts from your own data, not memory.

Python 3.9+, standard library only. One JSON file, offline, no accounts.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from datetime import date, timedelta

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "terms_book.json")


class TermsBookError(Exception):
    """User-facing error (bad input, unknown ref, ...)."""


def today_iso():
    return date.today().isoformat()


def parse_date(text):
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise TermsBookError("bad date %r (expected YYYY-MM-DD)" % text)


def parse_amount(text):
    """Rupees string -> integer paise; max 2 decimals, must be > 0."""
    text = (text or "").strip()
    if not re.fullmatch(r"\d+(\.\d{1,2})?", text):
        raise TermsBookError(
            "bad amount %r (e.g. 8000 or 8000.50, paise beyond 2 decimals "
            "not accepted)" % text)
    rupees, _, frac = text.partition(".")
    frac = (frac + "00")[:2]
    paise = int(rupees) * 100 + int(frac)
    if paise <= 0:
        raise TermsBookError("amount must be positive")
    return paise


def format_paise(paise):
    sign = "-" if paise < 0 else ""
    paise = abs(paise)
    s = str(paise // 100)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return "%s%s.%02d" % (sign, s, paise % 100)


def parse_terms(text):
    try:
        days = int(text)
    except (TypeError, ValueError):
        raise TermsBookError("bad terms %r (expected whole days, e.g. 30)"
                             % (text,))
    if days < 0:
        raise TermsBookError("terms cannot be negative (0 = due on receipt)")
    return days


def due_date(invoice):
    return parse_date(invoice["date"]) + timedelta(days=invoice["terms"])


def paid_paise(invoice):
    return sum(p["amount"] for p in invoice["payments"])


def outstanding_paise(invoice):
    return invoice["amount"] - paid_paise(invoice)


def is_fully_paid(invoice):
    return paid_paise(invoice) >= invoice["amount"]


def days_to_pay(invoice):
    """Days from invoice date to the payment that completed it; None if unpaid."""
    if not is_fully_paid(invoice):
        return None
    total = 0
    for p in invoice["payments"]:
        total += p["amount"]
        if total >= invoice["amount"]:
            return (parse_date(p["date"]) - parse_date(invoice["date"])).days
    return None  # pragma: no cover - guarded by is_fully_paid


def delay_days(invoice):
    """Days beyond agreed terms a fully paid invoice took; 0 when on time."""
    dtp = days_to_pay(invoice)
    if dtp is None:
        return None
    return max(0, dtp - invoice["terms"])


def overdue_days(invoice, as_of=None):
    """Days past the agreed due date for an unpaid invoice (0 if within terms)."""
    if is_fully_paid(invoice):
        return 0
    as_of = parse_date(as_of) if as_of else date.today()
    return max(0, (as_of - due_date(invoice)).days)


class Book:
    def __init__(self):
        self.buyers = {}    # name -> {name, contact, created}
        self.invoices = {}  # ref  -> invoice dict

    @classmethod
    def load(cls, path):
        book = cls()
        if not os.path.exists(path):
            return book
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise TermsBookError("cannot read %s: %s" % (path, exc))
        for name, buyer in data.get("buyers", {}).items():
            book.buyers[name] = buyer
        for ref, inv in data.get("invoices", {}).items():
            book.invoices[ref] = inv
        return book

    def save(self, path):
        data = {"buyers": self.buyers, "invoices": self.invoices}
        fd, tmp_name = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # -- buyers ---------------------------------------------------------

    def add_buyer(self, name, contact=""):
        name = (name or "").strip()
        if not name:
            raise TermsBookError("buyer name cannot be empty")
        if name in self.buyers:
            raise TermsBookError("buyer %r already exists" % name)
        self.buyers[name] = {"name": name, "contact": contact,
                             "created": today_iso()}
        return self.buyers[name]

    def get_buyer(self, name):
        try:
            return self.buyers[name]
        except KeyError:
            raise TermsBookError("unknown buyer %r (add it first)" % (name,))

    # -- invoices -------------------------------------------------------

    def add_invoice(self, buyer, ref, amount_paise, when, terms_days):
        self.get_buyer(buyer)
        ref = (ref or "").strip()
        if not ref:
            raise TermsBookError("invoice ref cannot be empty")
        if ref in self.invoices:
            raise TermsBookError("invoice ref %r already exists" % ref)
        parse_date(when)  # validate
        inv = {"ref": ref, "buyer": buyer, "amount": amount_paise,
               "date": when, "terms": terms_days, "payments": []}
        self.invoices[ref] = inv
        return inv

    def get_invoice(self, ref):
        try:
            return self.invoices[ref]
        except KeyError:
            raise TermsBookError("unknown invoice %r" % (ref,))

    def record_payment(self, ref, amount_paise, when):
        inv = self.get_invoice(ref)
        if parse_date(when) < parse_date(inv["date"]):
            raise TermsBookError(
                "payment date %s is before invoice date %s"
                % (when, inv["date"]))
        if is_fully_paid(inv):
            raise TermsBookError("invoice %s is already fully paid" % ref)
        if amount_paise > outstanding_paise(inv):
            raise TermsBookError(
                "payment of %s exceeds the %s still due on %s"
                % (format_paise(amount_paise),
                   format_paise(outstanding_paise(inv)), ref))
        inv["payments"].append({"date": when, "amount": amount_paise})
        return inv

    # -- reporting ------------------------------------------------------

    def buyer_invoices(self, name):
        return [inv for inv in self.invoices.values() if inv["buyer"] == name]

    def buyer_stats(self, name, as_of=None):
        invs = self.buyer_invoices(name)
        paid = [inv for inv in invs if is_fully_paid(inv)]
        on_time = [inv for inv in paid if days_to_pay(inv) <= inv["terms"]]
        delays = [delay_days(inv) for inv in paid if delay_days(inv) > 0]
        dtps = [days_to_pay(inv) for inv in paid]
        open_invs = [inv for inv in invs if not is_fully_paid(inv)]
        overs = [overdue_days(inv, as_of) for inv in open_invs]
        overs = [d for d in overs if d > 0]
        return {
            "invoices": len(invs),
            "paid": len(paid),
            "on_time": len(on_time),
            "on_time_pct": (round(100.0 * len(on_time) / len(paid))
                            if paid else None),
            "avg_days_to_pay": (round(sum(dtps) / len(dtps))
                                if dtps else None),
            "avg_delay": round(sum(delays) / len(delays)) if delays else 0,
            "worst_delay": max(delays) if delays else 0,
            "outstanding": sum(outstanding_paise(inv) for inv in open_invs),
            "open": len(open_invs),
            "overdue_count": len(overs),
            "avg_overdue": round(sum(overs) / len(overs)) if overs else 0,
        }

    def outstanding(self, as_of=None):
        rows = []
        for inv in self.invoices.values():
            if is_fully_paid(inv):
                continue
            rows.append((inv, overdue_days(inv, as_of)))
        rows.sort(key=lambda row: (due_date(row[0]), row[0]["ref"]))
        return rows

    def summary(self, as_of=None):
        total_open = 0
        total_overdue = 0
        for inv in self.invoices.values():
            total_open += outstanding_paise(inv)
            if overdue_days(inv, as_of) > 0:
                total_overdue += 1
        return {"buyers": len(self.buyers), "invoices": len(self.invoices),
                "outstanding": total_open, "overdue_invoices": total_overdue}

    def export_csv(self, out_path, as_of=None):
        fields = ["ref", "buyer", "invoice_date", "amount", "terms_days",
                  "due_date", "paid", "days_to_pay", "days_beyond_terms",
                  "overdue_days", "outstanding"]
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(fields)
            for inv in self.invoices.values():
                dtp = days_to_pay(inv)
                writer.writerow([
                    inv["ref"], inv["buyer"], inv["date"],
                    format_paise(inv["amount"]), inv["terms"],
                    due_date(inv).isoformat(),
                    "yes" if is_fully_paid(inv) else "no",
                    dtp if dtp is not None else "",
                    delay_days(inv) if dtp is not None else "",
                    overdue_days(inv, as_of),
                    format_paise(outstanding_paise(inv)),
                ])


def _pct(stats):
    return "-" if stats["on_time_pct"] is None else "%d%%" % stats["on_time_pct"]


def _avg(stats, key):
    return "-" if stats[key] is None else str(stats[key])


def cmd_buyer_add(book, args):
    book.add_buyer(args.name, contact=args.contact or "")
    print("added buyer %s" % args.name)


def cmd_add(book, args):
    inv = book.add_invoice(args.buyer, args.ref, parse_amount(args.amount),
                           args.date, parse_terms(args.terms))
    print("recorded invoice %s for %s: %s, dated %s, Net %d (due %s)"
          % (inv["ref"], inv["buyer"], format_paise(inv["amount"]),
             inv["date"], inv["terms"], due_date(inv).isoformat()))


def cmd_pay(book, args):
    amount = parse_amount(args.amount)
    inv = book.record_payment(args.ref, amount, args.date)
    left = outstanding_paise(inv)
    if left:
        print("recorded payment of %s on %s; %s still due"
              % (format_paise(amount), args.ref, format_paise(left)))
    else:
        dtp = days_to_pay(inv)
        delay = delay_days(inv)
        note = "on time" if delay == 0 else "%d days beyond Net %d" % (
            delay, inv["terms"])
        print("recorded payment of %s on %s; invoice fully paid in %d days (%s)"
              % (format_paise(amount), args.ref, dtp, note))


def cmd_buyers(book, args):
    if not book.buyers:
        print("no buyers yet")
        return
    print("%-24s %8s %6s %8s %10s %10s %14s" % (
        "buyer", "invoices", "paid", "on time", "avg days", "avg late",
        "outstanding"))
    for name in sorted(book.buyers):
        stats = book.buyer_stats(name, args.as_of)
        print("%-24s %8d %6d %8s %10s %10s %14s" % (
            name, stats["invoices"], stats["paid"], _pct(stats),
            _avg(stats, "avg_days_to_pay"),
            stats["avg_delay"] if stats["paid"] else "-",
            format_paise(stats["outstanding"])))


def cmd_buyer(book, args):
    book.get_buyer(args.name)
    invs = book.buyer_invoices(args.name)
    if not invs:
        print("%s: no invoices recorded" % args.name)
        return
    print("%s" % args.name)
    for inv in sorted(invs, key=lambda i: (i["date"], i["ref"])):
        line = "  %s  %s  %14s  Net %-3d due %s" % (
            inv["ref"], inv["date"], format_paise(inv["amount"]),
            inv["terms"], due_date(inv).isoformat())
        if is_fully_paid(inv):
            dtp, delay = days_to_pay(inv), delay_days(inv)
            state = "paid in %d days, on time" % dtp if delay == 0 else \
                    "paid in %d days, %d late" % (dtp, delay)
        else:
            over = overdue_days(inv, args.as_of)
            if over == 0:
                as_of = parse_date(args.as_of) if args.as_of else date.today()
                left = (due_date(inv) - as_of).days
                state = "due today" if left == 0 else "due in %d days" % left
            else:
                state = "%d days overdue" % over
            state = "%s outstanding, %s" % (
                format_paise(outstanding_paise(inv)), state)
        print("%s  %s" % (line, state))


def cmd_brief(book, args):
    as_of = args.as_of or today_iso()
    names = [args.name] if args.name else sorted(book.buyers)
    if not names:
        print("no buyers yet")
        return
    for name in names:
        book.get_buyer(name)
        stats = book.buyer_stats(name, as_of)
        print("%s - payment terms brief (as of %s)" % (name, as_of))
        if stats["invoices"] == 0:
            print("  no invoices recorded for this buyer")
            continue
        print("  invoices recorded: %d; fully paid: %d" % (
            stats["invoices"], stats["paid"]))
        if stats["paid"]:
            print("  paid on time: %d of %d (%s)" % (
                stats["on_time"], stats["paid"], _pct(stats)))
            print("  average time to pay: %s days" %
                  _avg(stats, "avg_days_to_pay"))
            print("  average delay beyond terms (late payers): %d days; "
                  "worst: %d days" % (stats["avg_delay"],
                                      stats["worst_delay"]))
        if stats["open"]:
            print("  outstanding now: %s across %d invoice(s)" % (
                format_paise(stats["outstanding"]), stats["open"]))
            if stats["overdue_count"]:
                print("  overdue: %d invoice(s), average %d days past due" % (
                    stats["overdue_count"], stats["avg_overdue"]))
        print("  (computed only from invoices you recorded in this book)")


def cmd_outstanding(book, args):
    rows = book.outstanding(args.as_of)
    if not rows:
        print("nothing outstanding")
        return
    as_of = args.as_of or today_iso()
    print("outstanding as of %s:" % as_of)
    for inv, over in rows:
        state = "due %s" % due_date(inv).isoformat() if over == 0 else \
                "%d days overdue" % over
        print("  %-12s %-24s %14s  %s" % (
            inv["ref"], inv["buyer"], format_paise(outstanding_paise(inv)),
            state))


def cmd_export(book, args):
    book.export_csv(args.path, args.as_of)
    print("wrote %s" % args.path)


def cmd_summary(book, args):
    s = book.summary(args.as_of)
    print("buyers: %d, invoices: %d, outstanding: %s, overdue invoices: %d"
          % (s["buyers"], s["invoices"], format_paise(s["outstanding"]),
             s["overdue_invoices"]))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="terms-book",
        description="Payment-terms evidence book: who pays on time, how "
                    "late the rest are, and what is still owed.")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help="JSON data file (default: %(default)s)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_as_of(p):
        p.add_argument("--as-of", default=None,
                       help="compute overdue days as of YYYY-MM-DD "
                            "(default: today)")

    p = sub.add_parser("buyer", help="buyer add / statement")
    buyer_sub = p.add_subparsers(dest="buyer_cmd", required=True)
    pa = buyer_sub.add_parser("add", help="add a buyer")
    pa.add_argument("name")
    pa.add_argument("--contact", default=None)
    pa.set_defaults(func=cmd_buyer_add)
    ps = buyer_sub.add_parser("show", help="one buyer's invoice statement")
    ps.add_argument("name")
    add_as_of(ps)
    ps.set_defaults(func=cmd_buyer)

    p = sub.add_parser("add", help="record an invoice against agreed terms")
    p.add_argument("buyer")
    p.add_argument("ref")
    p.add_argument("amount", help="rupees, e.g. 125000 or 125000.50")
    p.add_argument("date", help="invoice date, YYYY-MM-DD")
    p.add_argument("--terms", default="30",
                   help="agreed payment terms in days (default: 30)")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("pay", help="record a (partial or full) payment")
    p.add_argument("ref")
    p.add_argument("amount")
    p.add_argument("date", help="payment date, YYYY-MM-DD")
    p.set_defaults(func=cmd_pay)

    p = sub.add_parser("buyers", help="per-buyer scorecard table")
    add_as_of(p)
    p.set_defaults(func=cmd_buyers)

    p = sub.add_parser("brief", help="negotiation brief per buyer")
    p.add_argument("name", nargs="?", default=None)
    add_as_of(p)
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("outstanding", help="unpaid invoices with overdue days")
    add_as_of(p)
    p.set_defaults(func=cmd_outstanding)

    p = sub.add_parser("export", help="export all invoices to CSV")
    p.add_argument("path")
    add_as_of(p)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("summary", help="one-line totals")
    add_as_of(p)
    p.set_defaults(func=cmd_summary)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        book = Book.load(args.db)
        args.func(book, args)
        book.save(args.db)
    except TermsBookError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
