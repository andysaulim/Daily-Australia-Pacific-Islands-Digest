"""Run a blocking test suite as if it were a different date.

A suite that gates the send must depend only on the code it tests. If the
calendar can fail it, it will one day cancel a brief for a reason that has
nothing to do with that morning's brief. Two have already been found this way:
this edition's fixture hardcoded a date and would have failed every run from
the day after it was written, and the Australia edition's health assertion
failed at +400 days because a baseline-staleness alert fires on the calendar.

    python timetravel.py 400 smoke_test.py     # run it 400 days from now

The daily workflow runs it at a year out, so a time bomb is found by a red run
long before it can reach a morning.
"""

import datetime as _dt
import os
import runpy
import sys

OFFSET_DAYS = int(sys.argv[1])
TARGET = sys.argv[2]

_real_date, _real_datetime = _dt.date, _dt.datetime
_shift = _dt.timedelta(days=OFFSET_DAYS)


class _Date(_real_date):
    @classmethod
    def today(cls):
        return (_real_date.today() + _shift)


class _DateTime(_real_datetime):
    @classmethod
    def now(cls, tz=None):
        return _real_datetime.now(tz) + _shift

    @classmethod
    def utcnow(cls):
        return _real_datetime.utcnow() + _shift


_dt.date = _Date
_dt.datetime = _DateTime
# The suite imports its siblings by bare name, so its own directory
# must be on the path exactly as it is when run directly.
sys.path.insert(0, os.path.dirname(os.path.abspath(TARGET)))
sys.argv = [TARGET]
runpy.run_path(TARGET, run_name="__main__")
