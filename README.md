# Cricket Schedules Webapp (Python)

A minimal Flask app that fetches and parses `https://hamariweb.com/cricket/schedules.aspx` and exposes:

- GET `/api/schedules/raw` — returns source HTML
- GET `/api/schedules` — returns parsed match items (title, status, teams, time, link)
- GET `/` — minimal frontend listing matches

## Run locally

1) (Optional) Create a venv and install deps


```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If venv is unavailable on your system, you can install with:

```
pip install --break-system-packages --user -r requirements.txt
```

2) Start the server

```
python3 app.py
```

3) Open in browser

- Frontend: http://127.0.0.1:8000/
- JSON: http://127.0.0.1:8000/api/schedules

## Notes
- Parser anchors to `.match_update` blocks on the page for reliable extraction.
- Be respectful of upstream. There is a small in-memory cache to reduce requests.
- This is a Test.
