"""Market Data Layer — `underwriter/data/` (§11.1).

Turns Alpaca into a `MarketSnapshot` the Actuary can price deterministically.

Two properties this package exists to hold:

* **Credential isolation (ALP-004).** Only read-only data credentials are used
  here. Nothing in this package can place an order, and `credentials.py`
  refuses to hand out a trading key.
* **Never estimate (FR-004).** A missing Greek, IV, or quote discards the
  contract with a recorded reason. There is no interpolation, no default, no
  last-known-good. G-08: the book never holds risk the system cannot measure.
"""
