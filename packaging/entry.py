"""PyInstaller entry point for the desktop app (all platforms)."""

import sys

from lem.gui.app import main

sys.exit(main())
