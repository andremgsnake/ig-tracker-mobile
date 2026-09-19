import sys

path = "/home/andremgsnake/ig-tracker-mobile/backend"
if path not in sys.path:
    sys.path.insert(0, path)

from app import app as application
