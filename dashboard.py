#!/usr/bin/env python3
"""
Local dashboard for the Craigslist lead scraper.

Reads data/leads.csv fresh on every request (so it always reflects the
latest scraper run) and serves a single-page dashboard: stat tiles, two
charts, and a filterable/sortable table of leads.

Run:
    python3 dashboard.py
Then open http://127.0.0.1:5050

If you're exposing this via a tunnel (cloudflared, ngrok) so it has a
public URL, set DASHBOARD_PASSWORD first so it isn't wide open to anyone
who gets the link:
    export DASHBOARD_PASSWORD="something-only-you-know"
    python3 dashboard.py
"""

import argparse
import csv
import os
import secrets
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, render_template, request, Response

app = Flask(__name__)
LEADS_CSV = Path("data/leads.csv")

DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "admin")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")


def require_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not DASHBOARD_PASSWORD:
            return view(*args, **kwargs)
        auth = request.authorization
        valid = (
            auth
            and secrets.compare_digest(auth.username, DASHBOARD_USER)
            and secrets.compare_digest(auth.password, DASHBOARD_PASSWORD)
        )
        if not valid:
            return Response(
                "Authentication required.",
                401,
                {"WWW-Authenticate": 'Basic realm="Lead Dashboard"'},
            )
        return view(*args, **kwargs)

    return wrapped


def load_leads() -> list:
    if not LEADS_CSV.exists():
        return []
    with open(LEADS_CSV, newline="") as f:
        return list(csv.DictReader(f))


@app.route("/")
@require_auth
def index():
    return render_template("dashboard.html")


@app.route("/api/leads")
@require_auth
def api_leads():
    return jsonify(load_leads())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    if DASHBOARD_PASSWORD:
        print(f"Password protection ON (user: {DASHBOARD_USER}).")
    else:
        print(
            "WARNING: DASHBOARD_PASSWORD is not set -- this dashboard has no "
            "login. Fine for localhost-only use; if you're exposing it "
            "through a tunnel, stop and set DASHBOARD_PASSWORD first."
        )

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
