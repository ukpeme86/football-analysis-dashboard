import math
import time
import hashlib
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Football Analysis Dashboard",
    page_icon="⚽",
    layout="wide"
)

BASE = "https://api.football-data.org/v4"

LEAGUES = {
    "Premier League (England)": "PL",
    "La Liga (Spain)": "PD",
    "Bundesliga (Germany)": "BL1",
    "Serie A (Italy)": "SA",
    "Ligue 1 (France)": "FL1",
    "Champions League": "CL",
    "Europa League": "EL",
    "Eredivisie (Netherlands)": "DED",
    "Primeira Liga (Portugal)": "PPL"
}


# =====================================
# API DATA FETCHING WITH CACHING
# =====================================

@st.cache_data(ttl=900, show_spinner=False)
def fetch_matches(
    competition,
    date_from,
    date_to,
    token_hash,
    _token
):

    url = f"{BASE}/competitions/{competition}/matches"

    headers = {
        "X-Auth-Token": _token.strip()
    }

    params = {
        "dateFrom": date_from,
        "dateTo": date_to
    }

    for attempt in range(2):

        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=30
        )

        if response.status_code == 429:

            if attempt == 0:

                retry = response.headers.get(
                    "Retry-After", "10"
                )

                try:
                    wait = min(max(int(retry), 5), 60)
                except ValueError:
                    wait = 10

                time.sleep(wait)
                continue

            raise RuntimeError(
                "API request limit reached. "
                "Please wait before trying again."
            )

        if response.status_code == 401:
            raise RuntimeError("Invalid API token.")

        if response.status_code == 403:
            raise RuntimeError(
                "Your free plan does not include this competition."
            )

        if response.status_code >= 400:
            raise RuntimeError(
                f"API error {response.status_code}: "
                f"{response.text[:200]}"
            )

        return response.json()

    raise RuntimeError("Unable to retrieve match data.")


# =====================================
# DATE HANDLING
# =====================================

def match_date(match):

    value = match.get("utcDate")

    if not value:
        return datetime.now(timezone.utc)

    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


# =====================================
# BUILD ELO MODEL
# =====================================

def build_model(history):

    matches = []

    for match in history:

        score = match.get(
            "score", {}
        ).get("fullTime", {})

        if (
            match.get("status") == "FINISHED"
            and score.get("home") is not None
            and score.get("away") is not None
        ):

            matches.append({

                "date": match_date(match),

                "home": match.get(
                    "homeTeam", {}
                ).get("name"),

                "away": match.get(
                    "awayTeam", {}
                ).get("name"),

                "hg": int(score["home"]),
                "ag": int(score["away"])

            })

    matches.sort(key=lambda x: x["date"])

    ratings = {}
    records = {}

    total_home = 0
    total_away = 0

    for match in matches:

        home = match["home"]
        away = match["away"]

        hg = match["hg"]
        ag = match["ag"]

        ratings.setdefault(home, 1500.0)
        ratings.setdefault(away, 1500.0)

        records.setdefault(home, [])
        records.setdefault(away, [])

        expected = 1 / (
            1 + 10 ** (
                (
                    ratings[away]
                    - ratings[home]
                    - 55
                ) / 400
            )
        )

        if hg > ag:
            actual = 1
        elif hg == ag:
            actual = 0.5
        else:
            actual = 0

        # Corrected Elo update:
        # Draws must also update ratings.

        goal_difference = abs(hg - ag)

        k_factor = 20 * (
            1 + 0.5 * min(
                math.log1p(goal_difference), 1.5
            )
        )

        change = k_factor * (actual - expected)

        ratings[home] += change
        ratings[away] -= change

        if hg > ag:
            hp, ap = 3, 0
        elif hg == ag:
            hp, ap = 1, 1
        else:
            hp, ap = 0, 3

        records[home].append({
            "date": match["date"],
            "venue": "home",
            "gf": hg,
            "ga": ag,
            "points": hp
        })

        records[away].append({
            "date": match["date"],
            "venue": "away",
            "gf": ag,
            "ga": hg,
            "points": ap
        })

        total_home += hg
        total_away += ag

    count = len(matches)

    return {

        "ratings": ratings,
        "records": records,

        "avg_home": (
            total_home / count if count else 1.45
        ),

        "avg_away": (
            total_away / count if count else 1.15
        ),

        "matches": count

    }


# =====================================
# WEIGHTED TEAM STATISTICS
# =====================================

def weighted_average(records, field):

    if not records:
        return 0

    now = datetime.now(timezone.utc)

    total = 0
    weights = 0

    for record in records:

        age = max(
            0,
            (now - record["date"]).total_seconds()
            / 86400
        )

        weight = math.exp(
            -math.log(2) * age / 60
        )

        total += record[field] * weight
        weights += weight

    return total / weights if weights else 0


def team_statistics(model, team, venue):

    records = model["records"].get(team, [])

    if not records:

        return {
            "attack": 1.0,
            "defence": 1.0,
            "form": 1.35,
            "samples": 0
        }

    gf = weighted_average(records, "gf")
    ga = weighted_average(records, "ga")

    venue_records = [
        r for r in records
        if r["venue"] == venue
    ]

    if venue_records:

        vgf = weighted_average(
            venue_records, "gf"
        )

        vga = weighted_average(
            venue_records, "ga"
        )

        factor = len(venue_records) / (
            len(venue_records) + 5
        )

        gf = factor * vgf + (1 - factor) * gf
        ga = factor * vga + (1 - factor) * ga

    if venue == "home":

        scoring_base = model["avg_home"]
        conceding_base = model["avg_away"]

    else:

        scoring_base = model["avg_away"]
        conceding_base = model["avg_home"]

    attack = gf / max(scoring_base, 0.1)

    defence = ga / max(conceding_base, 0.1)

    recent = sorted(
        records,
        key=lambda r: r["date"],
        reverse=True
    )[:5]

    form = (
        sum(r["points"] for r in recent)
        / len(recent)
        if recent else 1.35
    )

    return {

        "attack": max(
            0.35, min(2.5, attack)
        ),

        "defence": max(
            0.35, min(2.5, defence)
        ),

        "form": form,

        "samples": len(venue_records)

    }


# =====================================
# POISSON MODEL
# =====================================

def poisson(lam):

    values = [
        math.exp(-lam)
        * lam ** k
        / math.factorial(k)
        for k in range(11)
    ]

    total = sum(values)

    return [x / total for x in values]


# =====================================
# MATCH PREDICTION ENGINE
# =====================================

def predict(model, home, away):

    hs = team_statistics(
        model, home, "home"
    )

    aws = team_statistics(
        model, away, "away"
    )

    ratings = model["ratings"]

    home_elo = ratings.get(home, 1500)
    away_elo = ratings.get(away, 1500)

    elo_difference = (
        home_elo + 55 - away_elo
    )

    elo_factor = math.exp(
        max(
            -0.25,
            min(0.25, elo_difference / 3000)
        )
    )

    home_goals = (
        model["avg_home"]
        * hs["attack"]
        * aws["defence"]
    )

    away_goals = (
        model["avg_away"]
        * aws["attack"]
        * hs["defence"]
    )

    home_form = math.exp(
        (hs["form"] - 1.35) * 0.08
    )

    away_form = math.exp(
        (aws["form"] - 1.35) * 0.08
    )

    lh = home_goals * elo_factor * home_form

    la = away_goals / elo_factor * away_form

    lh = max(0.15, min(4.5, lh))
    la = max(0.15, min(4.5, la))

    ph = poisson(lh)
    pa = poisson(la)

    hw = dr = aw = 0

    ov15 = ov25 = ov35 = un25 = btts = 0

    for i, pi in enumerate(ph):

        for j, pj in enumerate(pa):

            p = pi * pj

            if i > j:
                hw += p

            elif i == j:
                dr += p

            else:
                aw += p

            if i + j >= 2:
                ov15 += p

            if i + j >= 3:
                ov25 += p

            if i + j >= 4:
                ov35 += p

            if i + j <= 2:
                un25 += p

            if i >= 1 and j >= 1:
                btts += p

    def strength(rating):

        return round(
            max(
                0,
                min(100, 50 + (rating - 1500) / 8)
            ),
            1
        )

    return {

        "Expected home goals": round(lh, 2),
        "Expected away goals": round(la, 2),

        "Home Elo": round(home_elo),
        "Away Elo": round(away_elo),

        "Home strength /100": strength(home_elo),
        "Away strength /100": strength(away_elo),

        "Home attack": round(hs["attack"], 2),
        "Away attack": round(aws["attack"], 2),

        "Home defence": round(hs["defence"], 2),
        "Away defence": round(aws["defence"], 2),

        "Home form PPG": round(hs["form"], 2),
        "Away form PPG": round(aws["form"], 2),

        "Home win %": round(hw * 100, 1),
        "Draw %": round(dr * 100, 1),
        "Away win %": round(aw * 100, 1),

        "Over 1.5 %": round(ov15 * 100, 1),
        "Over 2.5 %": round(ov25 * 100, 1),
        "Over 3.5 %": round(ov35 * 100, 1),

        "Under 2.5 %": round(un25 * 100, 1),

        "BTTS Yes %": round(btts * 100, 1),
        "BTTS No %": round((1-btts) * 100, 1),

        "Home samples": hs["samples"],
        "Away samples": aws["samples"]

    }


# =====================================
# DASHBOARD
# =====================================

st.title("⚽ Football Analysis Dashboard — V4.1")

st.write(
    "Advanced football analysis using team strength, "
    "Elo ratings, attack, defence, recent form "
    "and Poisson probabilities."
)

with st.sidebar:

    st.header("Data settings")

    token = st.text_input(
        "football-data.org API token",
        type="password"
    )

    selected = st.multiselect(
        "Competitions",
        list(LEAGUES),
        default=[
            "Premier League (England)",
            "La Liga (Spain)",
            "Bundesliga (Germany)",
            "Serie A (Italy)",
            "Ligue 1 (France)"
        ]
    )

    days = st.selectbox(
        "Recent-results window",
        [30, 60, 90],
        index=1
    )

    go = st.button(
        "Load today's upcoming matches",
        type="primary"
    )

    if st.button("Clear cached data"):

        st.cache_data.clear()

        st.success(
            "Cached data cleared. Reload your matches."
        )


if not token:

    st.info(
        "Open the sidebar and enter your private API token."
    )

    st.stop()


if go:

    now = datetime.now(timezone.utc)

    today = now.date()

    start = (
        today - timedelta(days=days)
    ).isoformat()

    end = today.isoformat()

    token_hash = hashlib.sha256(
        token.encode()
    ).hexdigest()

    rows = []
    errors = []
    status_rows = []

    bar = st.progress(0)

    progress_text = st.empty()

    last_request = 0

    for i, name in enumerate(selected):

        code = LEAGUES[name]

        try:

            progress_text.write(
                f"Loading {name}..."
            )

            # Avoid rapid requests.
            elapsed = time.monotonic() - last_request

            if elapsed < 1.2 and last_request:
                time.sleep(1.2 - elapsed)

            last_request = time.monotonic()

            # One API request supplies both history
            # and today's fixtures.

            data = fetch_matches(
                code,
                start,
                end,
                token_hash,
                token
            )

            matches = data.get("matches", [])

            history = [
                m for m in matches
                if m.get("status") == "FINISHED"
            ]

            upcoming = []

            for match in matches:

                kickoff = match_date(match)

                if (
                    kickoff >= now
                    and match.get("status") in (
                        "SCHEDULED",
                        "TIMED"
                    )
                ):

                    upcoming.append(match)

            model = build_model(history)

            status_rows.append({

                "Competition": name,
                "Historical matches": model["matches"],
                "Upcoming matches": len(upcoming),
                "Data retrieved": datetime.now().strftime(
                    "%H:%M:%S"
                )

            })

            for match in upcoming:

                home = match.get(
                    "homeTeam", {}
                ).get("name", "Home")

                away = match.get(
                    "awayTeam", {}
                ).get("name", "Away")

                kickoff = match_date(
                    match
                ).astimezone().strftime(
                    "%Y-%m-%d %H:%M"
                )

                prediction = predict(
                    model,
                    home,
                    away
                )

                rows.append({

                    "Competition": name,

                    "Kickoff (device time)": kickoff,

                    "Home team": home,

                    "Away team": away,

                    **prediction

                })

        except Exception as e:

            errors.append(
                f"{name}: {e}"
            )

        bar.progress(
            (i + 1) / max(1, len(selected))
        )

    progress_text.empty()

    if errors:

        st.warning(
            "Some competitions could not be loaded. "
            "Other successful competitions are preserved."
        )

        for error in errors:
            st.write("• " + error)

    if status_rows:

        st.subheader("Data loading report")

        st.dataframe(
            pd.DataFrame(status_rows),
            use_container_width=True,
            hide_index=True
        )

    if rows:

        df = pd.DataFrame(rows)

        df = df.sort_values(
            [
                "Kickoff (device time)",
                "Competition"
            ]
        )

        st.success(
            f"Successfully loaded {len(df)} upcoming matches."
        )

        st.subheader("Today's Match Predictions")

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True
        )

        st.download_button(
            "Download analysis as CSV",
            df.to_csv(index=False).encode(),
            "football_analysis_v41.csv",
            "text/csv"
        )

        st.subheader("Understanding the model")

        st.write("""
        **Elo:** Measures historical team strength.

        **Attack:** Above 1.00 indicates above-average scoring.

        **Defence:** Below 1.00 indicates fewer goals conceded.

        **Form PPG:** Points per game over the last five matches.

        **Expected goals:** Estimated goals for each team.

        **Strength /100:** A transformed Elo rating, not a
        probability of winning.
        """)

        st.caption(
            "Predictions are statistical estimates, not guarantees. "
            "The model does not yet include injuries, suspensions, "
            "confirmed lineups, betting odds or weather. "
            "Short history windows may reduce reliability."
        )

    else:

        st.info(
            "No upcoming matches were returned for today "
            "in the selected competitions."
        )

else:

    st.info(
        "Enter your API token, select competitions "
        "and tap Load today's upcoming matches."
    )
