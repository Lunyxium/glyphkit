"""Launch GlyphKit without a console window.

Double-click this file or create a shortcut to it.
Uses pythonw.exe automatically due to .pyw extension.
"""

import os
import sys

# Ensure imports resolve from the script's own directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import main

main()
