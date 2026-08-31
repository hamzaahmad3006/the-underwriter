"""Typed values shared by the Actuary and the Kernel.

Neither module may import the other (the Kernel must be able to adjudicate a
proposal without knowing how it was priced), so the vocabulary they share
lives here.
"""
