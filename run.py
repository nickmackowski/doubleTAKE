"""Starts DoubleTake. The doubletake command runs this file.

Running a file, rather than "python -m", makes Python look for the program next
to this file and not in whatever folder the terminal happens to be in.
"""

import sys

from doubletake.cli import main

sys.exit(main())
