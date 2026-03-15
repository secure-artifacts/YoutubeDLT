import os
import sys

if getattr(sys, 'frozen', False):
    base = os.path.dirname(sys.executable)
    plugin_path = os.path.join(base, "..", "Frameworks", "Qt6", "plugins")
    os.environ["QT_PLUGIN_PATH"] = plugin_path
