# Double-click this file to start Reolink Alerts with no Terminal window
# (just the tray icon). It runs reolink_alerts.py from this same folder.
# If it's already running, the second copy quietly exits.
import runpy
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here))
runpy.run_path(str(here / "reolink_alerts.py"), run_name="__main__")
