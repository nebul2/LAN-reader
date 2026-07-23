"""PyInstaller entry point for the desktop app (all platforms)."""

import sys

from measure.gui.app import main

sys.exit(main())
