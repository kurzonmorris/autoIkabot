"""Formatting utilities for numbers and dates.

Ported from ikabot's helpers/varios.py.
"""

import time
from datetime import datetime


def addThousandSeparator(num, character: str = ".") -> str:
    """Format a number with thousand separators.

    Parameters
    ----------
    num : int or float
        The number to format.
    character : str
        Separator character (default: ".").

    Returns
    -------
    str
        Formatted string (e.g. 3000 -> "3.000").
    """
    return "{0:,}".format(int(num)).replace(",", character)


def getDateTime(timestamp=None) -> str:
    """Format a timestamp as YYYY-mm-dd_HH-MM-SS.

    Parameters
    ----------
    timestamp : float, optional
        Unix timestamp. If None, uses current time.

    Returns
    -------
    str
        Formatted datetime string.
    """
    timestamp = timestamp if timestamp is not None else time.time()
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d_%H-%M-%S")
