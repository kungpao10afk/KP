#!/usr/bin/env python3
"""
Local dashboard for the Craigslist lead scraper.

Reads data/leads.csv fresh on every request (so it always reflects the
latest scraper run) and serves a single-page dashboard: stat tiles, two
charts, and a filterable/sortable table of leads.

Run:
    python3 dashboard.py
Then open http://127.0.0.1:5050
"""

import argparse
import csv
from pathlib import Path

from flask import Flask, jsonify, render_template

app = Flask(__name__)
LEADS_CSV = Path("data/leads.csv")


def load_leads() -> list:
    if not LEADS_CSV.exists():
        return []
    with open(LEADS_CSV, newline="") as f:
        return list(csv.DictReader(f))


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/leads")
def api_leads():
    return jsonify(load_leads())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
