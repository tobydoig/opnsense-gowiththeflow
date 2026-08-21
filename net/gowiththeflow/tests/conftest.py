import os
import sys

_SCRIPTS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "src", "opnsense", "scripts", "gowiththeflow"
)
sys.path.insert(0, os.path.abspath(_SCRIPTS_DIR))
