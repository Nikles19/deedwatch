# Deedwatch

Who owns what around University Heights, Ohio (plus South Euclid, Cleveland Heights, Shaker Heights, Beachwood and Lyndhurst): a map of homes sold in the last 60 days, the last 7 days of ownership changes, name/address search across current and past owners, top owners, and sale-price trends.

**Live site:** https://nikles19.github.io/deedwatch/

## How it works

Everything runs on GitHub; there's no server or database to maintain.

| Piece | What it does |
|---|---|
| `scraper/opendata.py` | Weekly: map location, land use, value and street list from the county's Open Data Parcels service. Its ownership data lags months, so it isn't used for owners or sales. |
| `scraper/myplace.py` | MyPlace (myplace.cuyahogacounty.gov), which the county updates daily. Street-by-street address searches give each parcel's current owner; the Transfers page gives full sale history. |
| `python -m scraper sweep` | Nightly: compares every parcel's current owner with yesterday's and logs changes. |
| `python -m scraper history` | Fetches sale history for changed parcels first, then backfills every parcel (University Heights first). |
| `python -m scraper build` | Writes the JSON the site reads into `_site/data/`. |
| `.github/workflows/update.yml` | Runs the above nightly (and every 3 hours while backfilling), commits `data/`, deploys to GitHub Pages. |
| `site/` | The static front end (Leaflet map, no build step). |

Data is stored as plain files in `data/` (one parcel per line, history sharded by parcel book) so each night's commit is a small diff.

Requests to MyPlace are throttled (about 1–2 per second) and identify the project in the User-Agent.

## Run locally

```
pip install -r requirements.txt
python -m scraper base && python -m scraper sweep --city "UNIVERSITY HEIGHTS"
python -m scraper history --minutes 10
python -m scraper build && python -m http.server -d _site 8000
```

County data is provided "as is" and is not the official record.
