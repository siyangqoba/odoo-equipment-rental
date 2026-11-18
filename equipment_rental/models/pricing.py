"""Rental pricing: pick the cheapest mix of daily, weekly and monthly rates.

Kept free of ORM code so it can be unit-tested on its own.
"""
from __future__ import annotations

from dataclasses import dataclass

WEEK_DAYS = 7
MONTH_DAYS = 30


@dataclass(frozen=True)
class Quote:
    amount: float
    months: int = 0
    weeks: int = 0
    days: int = 0

    @property
    def breakdown(self) -> str:
        parts = []
        for count, unit in ((self.months, "month"), (self.weeks, "week"), (self.days, "day")):
            if count:
                parts.append(f"{count} {unit}{'s' if count > 1 else ''}")
        return " + ".join(parts) or "0 days"


def best_price(days: int, daily: float, weekly: float = 0.0, monthly: float = 0.0) -> Quote:
    """Cheapest way to cover at least ``days`` days.

    A rate of 0 means that period isn't offered. Covering a few extra days is
    allowed when it is cheaper (6 days at R100 = R600 loses to one week at R500).
    """
    if days <= 0:
        return Quote(0.0)
    options = [(1, daily)]
    if weekly > 0:
        options.append((WEEK_DAYS, weekly))
    if monthly > 0:
        options.append((MONTH_DAYS, monthly))

    cost = [0.0] + [float("inf")] * days
    choice = [0] * (days + 1)
    for d in range(1, days + 1):
        for length, price in options:
            candidate = cost[max(0, d - length)] + price
            # prefer the longer period on ties, it reads better on an invoice
            if candidate < cost[d] - 1e-9 or (abs(candidate - cost[d]) < 1e-9 and length > choice[d]):
                cost[d] = candidate
                choice[d] = length

    counts = {1: 0, WEEK_DAYS: 0, MONTH_DAYS: 0}
    d = days
    while d > 0:
        length = choice[d]
        counts[length] += 1
        d = max(0, d - length)
    return Quote(round(cost[days], 2), counts[MONTH_DAYS], counts[WEEK_DAYS], counts[1])
