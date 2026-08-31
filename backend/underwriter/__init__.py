"""The Underwriter — an autonomous AI options underwriting desk.

Authority boundary (SRS §10.2): only the execution engine ever holds Alpaca
trading credentials, and it transmits nothing without a signed KernelVerdict.
"""

__version__ = "0.1.0"
