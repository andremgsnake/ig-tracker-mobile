# PythonAnywhere setup

Username: andremgsnake

## Bash console

```bash
git clone https://github.com/andremgsnake/ig-tracker-mobile.git
cd ~/ig-tracker-mobile/backend
python3.13 -m venv ~/.virtualenvs/igtracker
source ~/.virtualenvs/igtracker/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Web app

Create a **Manual configuration** web app using the same Python version.

Source code:
`/home/andremgsnake/ig-tracker-mobile/backend`

Working directory:
`/home/andremgsnake/ig-tracker-mobile/backend`

Virtualenv:
`/home/andremgsnake/.virtualenvs/igtracker`

WSGI:
```python
import sys
path = "/home/andremgsnake/ig-tracker-mobile/backend"
if path not in sys.path:
    sys.path.insert(0, path)
from app import app as application
```

Then reload the web app and open `/health`.
