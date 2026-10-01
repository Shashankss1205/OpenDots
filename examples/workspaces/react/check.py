"""Narrow static fixture check; not a React runtime or full accessibility audit."""
from pathlib import Path
import sys

source = Path("src/SearchButton.jsx").read_text()
if 'aria-label="Search"' not in source or 'aria-hidden="true"' not in source:
    print("FAIL: fixture needs a Search accessible label and a decorative SVG")
    sys.exit(1)
print("PASS: local button fixture has an accessible name and decorative SVG")
