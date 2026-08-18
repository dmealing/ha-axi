"""Allow ``python -m ha_axi`` to behave exactly like the installed console script."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
