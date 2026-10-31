import math
import time
import hashlib
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd
import streamlit as st


st.set_page_config(
    page_title="Football Analysis Dashboard",
    page_icon="⚽",
    layout="wide"
)

FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"

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

# Conservative name aliases for common differences between providers.
TEAM_ALIASES = {
    "manchester city": "man city",
    "manchester united": "man united",
    "tottenham hotspur": "tottenham",
    "wolverhampton wanderers": "wolves",
    "athletic club": "athletic bilbao",
    "paris saint germain": "psg",
    "paris saint-germain": "psg",
    "internazionale": "inter",
    "inter milan": "inter",
    "ac milan": "milan",
    "fc barcelona": "barcelona",
    "atletico madrid": "atletico",
    "atletico de madrid": "atletico",
    "bayern munich": "bayern",
    "borussia dortmund": "dortmund",
    "sporting cp": "sporting",
    "sporting clube de portugal": "sporting",
    "benfica": "benfica",
    "fc porto": "porto"
}


# =====================================
# COMMON HELPERS
# =====================================

def normalise_team(name):
    """Normalise club names before attempting a cross-provider match."""
    value = unicodedata.normalize("NFKD", str(name or ""))
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.lower().replace("&", "and")
    value = re.sub(r"[^a-z0-9 ]", " ", value)
    value = re.sub(
        r"\b(fc|cf|afc|sc|club|football|futbol|de|the)\b",
        " ",
        value
    )
    value = " ".join(value.split())
    return TEAM_ALIASES.get(value, value)


def match_date(match):
    value = match.get("utcDate")
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def poisson(lam):
    values = [
        math.exp(-lam) * lam ** k / math.factorial(k)
        for k in range(11)
    ]
    total = sum(values)
    return [x / total for x in values] if total else values


# =====================================
# FOOTBALL-DATA.ORG
# =====================================

@st.cache_data(ttl=900, show_spinner=False)
def fetch_matches(competition, date_from, date_to, token_hash, _token):
    url = f"{FOOTBALL_DATA_BASE}/competitions/{competition}/matches"
    headers = {"X-Auth-Token": _token.strip()}
    params = {"dateFrom": date_from, "dateTo": date_to}

    for attempt in range(2):
        response = requests.get(
            url, headers=headers, params=params, timeout=30
        )

        if response.status_code == 429:
            if attempt == 0:
                retry = response.headers.get("Retry-After", "10")
                try:
                    wait = min(max(int(retry), 5), 60)
                except (ValueError, TypeError):
                    wait = 10
                time.sleep(wait)
                continue
            raise RuntimeError(
                "football-data.org request limit reached. "
                "Wait before trying again."
            )

        if response.status_code == 401:
            raise RuntimeError("Invalid football-data.org API token.")

        if response.status_code == 403:
            raise RuntimeError(
                "Your football-data.org plan does not include this competition."
            )

        if response.status_code >= 400:
            raise RuntimeError(
                f"football-data.org error {response.status_code}: "
                f"{response.text[:200]}"
            )

        return response.json()

    raise RuntimeError("Unable to retrieve match data.")


# =====================================
# API-FOOTBALL INJURY DATA
# =====================================

@st.cache_data(ttl=21600, show_spinner=False)
def fetch_injuries(date_string, key_hash, _api_key):
    """ One date-based request is used for all selected competitions. Cache duration is six hours to protect the free daily request quota. """
    url = f"{API_FOOTBALL_BASE}/injuries"
    headers = {"x-apisports-key": _api_key.strip()}
    response = requests.get(
        url,
        headers=headers,
        params={"date": date_string},
        timeout=30
    )

    if response.status_code == 401:
        raise RuntimeError("API-Football rejected the API key (401).")
    if response.status_code == 403:
        raise RuntimeError(
            "API-Football denied this request (403). Check your plan/key."
        )
    if response.status_code == 429:
        raise RuntimeError(
            "API-Football request limit reached (429). "
            "The injury request is cached for six hours."
        )
    if response.status_code >= 400:
        raise RuntimeError(
            f"API-Football error {response.status_code}: "
            f"{response.text[:250]}"
        )

    payload = response.json()
    api_errors = payload.get("errors")
    if api_errors:
        raise RuntimeError(f"API-Football response: {api_errors}")

    return payload.get("response", [])


def get_injury_team_name(item):
    return (item.get("team") or {}).get("name", "")


def get_injury_player_name(item):
    return (item.get("player") or {}).get("name", "Unknown player")


def injury_status(item):
    injury_type = str(item.get("type") or "").strip()
    reason = str(item.get("reason") or "").strip()
    combined = f"{injury_type} {reason}".lower()

    if "question" in combined or "doubt" in combined or "late test" in combined:
        return "Questionable"

    if (
        "missing fixture" in combined
        or "suspended" in combined
        or "out" == injury_type.lower()
    ):
        return "Confirmed unavailable"

    # Do not assume an unfamiliar status is a confirmed absence.
    return "Needs verification"


def collect_match_injuries(home, away, injury_records):
    wanted = {
        normalise_team(home): home,
        normalise_team(away): away
    }
    found = []

    for item in injury_records:
        api_team = get_injury_team_name(item)
        key = normalise_team(api_team)

        if key not in wanted:
            continue

        player = item.get("player") or {}
        found.append({
            "Team": wanted[key],
            "Player": get_injury_player_name(item),
            "Status": injury_status(item),
            "Reason": str(item.get("reason") or item.get("type") or "Not supplied"),
            "Position": "Unknown",
            "Importance": 0.5
        })

    # Remove duplicate rows that can occasionally appear in provider responses.
    unique = {}
    for row in found:
        key = (
            row["Team"], row["Player"], row["Status"], row["Reason"]
        )
        unique[key] = row

    return list(unique.values())


# =====================================
# ELO MODEL AND TEAM STATISTICS
# =====================================

def build_model(history):
    matches = []

    for match in history:
        score = match.get("score", {}).get("fullTime", {})
        if (
            match.get("status") == "FINISHED"
            and score.get("home") is not None
            and score.get("away") is not None
        ):
            matches.append({
                "date": match_date(match),
                "home": match.get("homeTeam", {}).get("name"),
                "away": match.get("awayTeam", {}).get("name"),
                "hg": int(score["home"]),
                "ag": int(score["away"])
            })

    matches.sort(key=lambda x: x["date"])
    ratings = {}
    records = {}
    total_home = 0
    total_away = 0

    for match in matches:
        home, away = match["home"], match["away"]
        hg, ag = match["hg"], match["ag"]

        if not home or not away:
            continue

        ratings.setdefault(home, 1500.0)
        ratings.setdefault(away, 1500.0)
        records.setdefault(home, [])
        records.setdefault(away, [])

        expected = 1 / (
            1 + 10 ** ((ratings[away] - ratings[home] - 55) / 400)
        )

        actual = 1 if hg > ag else (0.5 if hg == ag else 0)
        goal_difference = abs(hg - ag)
        k_factor = 20 * (
            1 + 0.5 * min(math.log1p(goal_difference), 1.5)
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
            "date": match["date"], "venue": "home",
            "gf": hg, "ga": ag, "points": hp
        })
        records[away].append({
            "date": match["date"], "venue": "away",
            "gf": ag, "ga": hg, "points": ap
        })

        total_home += hg
        total_away += ag

    count = len(matches)
    return {
        "ratings": ratings,
        "records": records,
        "avg_home": total_home / count if count else 1.45,
        "avg_away": total_away / count if count else 1.15,
        "matches": count
    }


def weighted_average(records, field):
    if not records:
        return 0

    now = datetime.now(timezone.utc)
    total = 0
    weights = 0

    for record in records:
        age = max(
            0,
            (now - record["date"]).total_seconds() / 86400
        )
        weight = math.exp(-math.log(2) * age / 60)
        total += record[field] * weight
        weights += weight

    return total / weights if weights else 0


def team_statistics(model, team, venue):
    records = model["records"].get(team, [])

    if not records:
        return {
            "attack": 1.0, "defence": 1.0,
            "form": 1.35, "samples": 0
        }

    gf = weighted_average(records, "gf")
    ga = weighted_average(records, "ga")

    venue_records = [r for r in records if r["venue"] == venue]

    if venue_records:
        vgf = weighted_average(venue_records, "gf")
        vga = weighted_average(venue_records, "ga")
        factor = len(venue_records) / (len(venue_records) + 5)
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
        records, key=lambda r: r["date"], reverse=True
    )[:5]
    form = (
        sum(r["points"] for r in recent) / len(recent)
        if recent else 1.35
    )

    return {
        "attack": max(0.35, min(2.5, attack)),
        "defence": max(0.35, min(2.5, defence)),
        "form": form,
        "samples": len(venue_records)
    }


# =====================================
# PROBABILITY CALCULATIONS
# =====================================

def probabilities_from_lambdas(lh, la):
    lh = max(0.15, min(4.5, float(lh)))
    la = max(0.15, min(4.5, float(la)))

    ph = poisson(lh)
    pa = poisson(la)

    hw = dr = aw = 0.0
    ov15 = ov25 = ov35 = un25 = btts = 0.0

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

    return {
        "Expected home goals": round(lh, 2),
        "Expected away goals": round(la, 2),
        "Home win %": round(hw * 100, 1),
        "Draw %": round(dr * 100, 1),
        "Away win %": round(aw * 100, 1),
        "Over 1.5 %": round(ov15 * 100, 1),
        "Over 2.5 %": round(ov25 * 100, 1),
        "Over 3.5 %": round(ov35 * 100, 1),
        "Under 2.5 %": round(un25 * 100, 1),
        "BTTS Yes %": round(btts * 100, 1),
        "BTTS No %": round((1 - btts) * 100, 1)
    }


def predict(model, home, away):
    hs = team_statistics(model, home, "home")
    aws = team_statistics(model, away, "away")

    ratings = model["ratings"]
    home_elo = ratings.get(home, 1500)
    away_elo = ratings.get(away, 1500)

    elo_difference = home_elo + 55 - away_elo
    elo_factor = math.exp(max(
        -0.25, min(0.25, elo_difference / 3000)
    ))

    home_goals = (
        model["avg_home"] * hs["attack"] * aws["defence"]
    )
    away_goals = (
        model["avg_away"] * aws["attack"] * hs["defence"]
    )

    home_form = math.exp((hs["form"] - 1.35) * 0.08)
    away_form = math.exp((aws["form"] - 1.35) * 0.08)

    lh = home_goals * elo_factor * home_form
    la = away_goals / elo_factor * away_form

    baseline = probabilities_from_lambdas(lh, la)

    def strength(rating):
        return round(max(0, min(100, 50 + (rating - 1500) / 8)), 1)

    details = {
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
        "Home samples": hs["samples"],
        "Away samples": aws["samples"]
    }

    return baseline, details


# =====================================
# INJURY IMPACT ENGINE
# =====================================

POSITION_OPTIONS = [
    "Unknown", "Attacker", "Midfielder", "Defender", "Goalkeeper"
]


def calculate_absence_adjustments(absence_rows, home, away):
    """ Conservative, transparent estimates. They are heuristic adjustments, not measured player-value estimates. Unknown positions have no automatic numerical effect until the user classifies them. """
    attack_reduction = {home: 0.0, away: 0.0}
    opponent_goal_increase = {home: 0.0, away: 0.0}

    if absence_rows is None or absence_rows.empty:
        return attack_reduction, opponent_goal_increase

    position_effect = {
        "Attacker": ("attack", 0.12),
        "Midfielder": ("attack", 0.05),
        "Defender": ("defence", 0.10),
        "Goalkeeper": ("defence", 0.16),
        "Unknown": ("none", 0.0)
    }

    for _, row in absence_rows.iterrows():
        team = row.get("Team")
        if team not in (home, away):
            continue

        status = str(row.get("Status", "Needs verification"))
        position = str(row.get("Position", "Unknown"))
        try:
            importance = float(row.get("Importance", 0.5))
        except (ValueError, TypeError):
            importance = 0.5

        importance = max(0.0, min(1.0, importance))

        if status == "Confirmed unavailable":
            confidence = 1.0
        elif status == "Questionable":
            confidence = 0.35
        else:
            confidence = 0.0

        effect_type, base_effect = position_effect.get(
            position, ("none", 0.0)
        )
        effect = base_effect * importance * confidence

        if effect_type == "attack":
            attack_reduction[team] += effect
        elif effect_type == "defence":
            opponent_goal_increase[team] += effect

    # Limit cumulative adjustments to avoid extreme swings.
    for team in (home, away):
        attack_reduction[team] = min(0.35, attack_reduction[team])
        opponent_goal_increase[team] = min(
            0.35, opponent_goal_increase[team]
        )

    return attack_reduction, opponent_goal_increase


def injury_adjusted_prediction( baseline, home, away, absence_rows ):
    attack_reduction, opponent_goal_increase = (
        calculate_absence_adjustments(absence_rows, home, away)
    )

    base_home = baseline["Expected home goals"]
    base_away = baseline["Expected away goals"]

    adjusted_home = (
        base_home * (1 - attack_reduction[home])
        + opponent_goal_increase[away]
    )
    adjusted_away = (
        base_away * (1 - attack_reduction[away])
        + opponent_goal_increase[home]
    )

    adjusted = probabilities_from_lambdas(
        adjusted_home, adjusted_away
    )

    return adjusted, {
        "Home attacking reduction %": round(
            attack_reduction[home] * 100, 1
        ),
        "Away attacking reduction %": round(
            attack_reduction[away] * 100, 1
        ),
        "Home defence absence effect (away xG)": round(
            opponent_goal_increase[home], 2
        ),
        "Away defence absence effect (home xG)": round(
            opponent_goal_increase[away], 2
        )
    }


# =====================================
# DASHBOARD INTERFACE
# =====================================

st.title("⚽ Football Analysis Dashboard — V5")

st.write(
    "Team-strength and Poisson predictions, enhanced with "
    "injury and suspension information when available."
)

with st.sidebar:
    st.header("Data settings")

    football_token = st.text_input(
        "football-data.org API token",
        type="password"
    )

    api_football_key = st.text_input(
        "API-Football API key (optional)",
        type="password",
        help="Enter your API-Football key here. Do not put it in GitHub code."
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
        st.success("Cached data cleared. Reload your matches.")


if not football_token:
    st.info(
        "Open the sidebar and enter your football-data.org API token."
    )
    st.stop()


if go:
    now = datetime.now(timezone.utc)
    today = now.date()

    start = (today - timedelta(days=days)).isoformat()
    end = today.isoformat()

    football_token_hash = hashlib.sha256(
        football_token.encode()
    ).hexdigest()

    rows = []
    errors = []
    status_rows = []
    match_context = []
    last_request = 0.0

    bar = st.progress(0)
    progress_text = st.empty()

    for i, competition_name in enumerate(selected):
        competition_code = LEAGUES[competition_name]

        try:
            progress_text.write(f"Loading {competition_name}...")

            elapsed = time.monotonic() - last_request
            if elapsed < 1.2 and last_request:
                time.sleep(1.2 - elapsed)

            last_request = time.monotonic()

            data = fetch_matches(
                competition_code,
                start,
                end,
                football_token_hash,
                football_token
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
                    and match.get("status") in ("SCHEDULED", "TIMED")
                ):
                    upcoming.append(match)

            model = build_model(history)

            status_rows.append({
                "Competition": competition_name,
                "Historical matches": model["matches"],
                "Upcoming matches": len(upcoming),
                "Data retrieved": datetime.now().strftime("%H:%M:%S")
            })

            for match in upcoming:
                home = match.get("homeTeam", {}).get("name", "Home")
                away = match.get("awayTeam", {}).get("name", "Away")
                kickoff_dt = match_date(match)
                kickoff_display = kickoff_dt.astimezone().strftime(
                    "%Y-%m-%d %H:%M"
                )

                baseline, details = predict(model, home, away)

                match_context.append({
                    "Competition": competition_name,
                    "Kickoff (device time)": kickoff_display,
                    "Home team": home,
                    "Away team": away,
                    "Baseline": baseline,
                    "Details": details
                })

        except Exception as exc:
            errors.append(f"{competition_name}: {exc}")

        bar.progress((i + 1) / max(1, len(selected)))

    progress_text.empty()

    if errors:
        st.warning(
            "Some competitions could not be loaded. "
            "Successful competitions have been retained."
        )
        for error in errors:
            st.write("• " + error)

    if status_rows:
        st.subheader("Football-data.org loading report")
        st.dataframe(
            pd.DataFrame(status_rows),
            use_container_width=True,
            hide_index=True
        )

    if not match_context:
        st.info(
            "No upcoming matches were returned for today "
            "in the selected competitions."
        )
        st.stop()

    # Fetch the injury feed once for today's UTC date, not once per match.
    injury_records = []
    injury_feed_status = "Not connected"
    injury_feed_note = ""

    if api_football_key:
        try:
            api_key_hash = hashlib.sha256(
                api_football_key.encode()
            ).hexdigest()

            injury_records = fetch_injuries(
                today.isoformat(),
                api_key_hash,
                api_football_key
            )
            injury_feed_status = "Connected"
            injury_feed_note = (
                f"{len(injury_records)} injury records returned for "
                f"{today.isoformat()} (UTC date)."
            )
        except Exception as exc:
            injury_feed_status = "Error"
            injury_feed_note = str(exc)
    else:
        injury_feed_note = (
            "Enter your API-Football key in the sidebar to load "
            "injury and suspension information. Baseline predictions "
            "will still work without it."
        )

    st.subheader("Injury data connection")
    if injury_feed_status == "Connected":
        st.success(injury_feed_note)
    elif injury_feed_status == "Error":
        st.warning(injury_feed_note)
    else:
        st.info(injury_feed_note)

    # Build an editable absence table. API-Football's injury feed does
    # not reliably supply player position/importance, so the user can
    # classify those fields before numerical adjustments are applied.
    absence_rows = []
    for context in match_context:
        home = context["Home team"]
        away = context["Away team"]
        match_injuries = collect_match_injuries(
            home, away, injury_records
        )

        for injury in match_injuries:
            absence_rows.append({
                "Match": f"{home} vs {away}",
                **injury
            })

    edited_absences = pd.DataFrame(
        absence_rows,
        columns=[
            "Match", "Team", "Player", "Status",
            "Reason", "Position", "Importance"
        ]
    )

    if not edited_absences.empty:
        st.subheader("Reported injuries and suspensions")
        st.caption(
            "Check the returned player/status details. The API feed may not "
            "provide position or player importance. Select a position and "
            "adjust importance (0.0–1.0) if you know the player's role. "
            "Unknown positions are not used in the numerical adjustment."
        )

        edited_absences = st.data_editor(
            edited_absences,
            use_container_width=True,
            hide_index=True,
            key="v5_absence_editor",
            disabled=["Match", "Team", "Player", "Status", "Reason"],
            column_config={
                "Position": st.column_config.SelectboxColumn(
                    "Position",
                    options=POSITION_OPTIONS,
                    required=True
                ),
                "Importance": st.column_config.NumberColumn(
                    "Importance (0–1)",
                    min_value=0.0,
                    max_value=1.0,
                    step=0.1,
                    format="%.1f"
                )
            }
        )
    else:
        st.info(
            "No injury records were matched to the listed teams. "
            "This does not prove that the teams have no injuries; "
            "the provider may have no report for those fixtures, or "
            "the club names may not match between data providers."
        )

    # Recalculate each fixture with the edited absence information.
    for context in match_context:
        home = context["Home team"]
        away = context["Away team"]
        baseline = context["Baseline"]

        if not edited_absences.empty:
            match_absences = edited_absences[
                edited_absences["Match"] == f"{home} vs {away}"
            ].copy()
        else:
            match_absences = pd.DataFrame()

        adjusted, impact = injury_adjusted_prediction(
            baseline, home, away, match_absences
        )

        row = {
            "Competition": context["Competition"],
            "Kickoff (device time)": context["Kickoff (device time)"],
            "Home team": home,
            "Away team": away,
            "Baseline home xG": baseline["Expected home goals"],
            "Adjusted home xG": adjusted["Expected home goals"],
            "Baseline away xG": baseline["Expected away goals"],
            "Adjusted away xG": adjusted["Expected away goals"],
            "Home win %": adjusted["Home win %"],
            "Draw %": adjusted["Draw %"],
            "Away win %": adjusted["Away win %"],
            "Over 1.5 %": adjusted["Over 1.5 %"],
            "Over 2.5 %": adjusted["Over 2.5 %"],
            "Over 3.5 %": adjusted["Over 3.5 %"],
            "Under 2.5 %": adjusted["Under 2.5 %"],
            "BTTS Yes %": adjusted["BTTS Yes %"],
            "BTTS No %": adjusted["BTTS No %"],
            "Home Elo": context["Details"]["Home Elo"],
            "Away Elo": context["Details"]["Away Elo"],
            "Home strength /100": context["Details"]["Home strength /100"],
            "Away strength /100": context["Details"]["Away strength /100"],
            "Home form PPG": context["Details"]["Home form PPG"],
            "Away form PPG": context["Details"]["Away form PPG"],
            "Home attacking reduction %": impact["Home attacking reduction %"],
            "Away attacking reduction %": impact["Away attacking reduction %"],
            "Home defence absence effect (away xG)": impact[
                "Home defence absence effect (away xG)"
            ],
            "Away defence absence effect (home xG)": impact[
                "Away defence absence effect (home xG)"
            ],
            "Injury records matched": len(match_absences)
        }
        rows.append(row)

    df = pd.DataFrame(rows).sort_values(
        ["Kickoff (device time)", "Competition"]
    )

    st.success(
        f"Successfully analysed {len(df)} upcoming matches."
    )

    st.subheader("Today's Match Predictions — V5")
    st.caption(
        "The displayed probabilities use injury-adjusted expected goals "
        "where a reported absence has a classified position. Otherwise, "
        "the baseline model remains unchanged."
    )
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True
    )

    st.download_button(
        "Download V5 analysis as CSV",
        df.to_csv(index=False).encode("utf-8"),
        "football_analysis_v5.csv",
        "text/csv"
    )

    st.subheader("Match-by-match injury impact")
    for context in match_context:
        home = context["Home team"]
        away = context["Away team"]
        label = f"{home} vs {away}"

        if not edited_absences.empty:
            match_absences = edited_absences[
                edited_absences["Match"] == label
            ].copy()
        else:
            match_absences = pd.DataFrame()

        baseline = context["Baseline"]
        adjusted, impact = injury_adjusted_prediction(
            baseline, home, away, match_absences
        )

        with st.expander(label):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**Baseline expected goals**")
                st.metric("Home", baseline["Expected home goals"])
                st.metric("Away", baseline["Expected away goals"])
            with col2:
                st.markdown("**Injury-adjusted expected goals**")
                st.metric(
                    "Home",
                    adjusted["Expected home goals"],
                    delta=round(
                        adjusted["Expected home goals"]
                        - baseline["Expected home goals"], 2
                    )
                )
                st.metric(
                    "Away",
                    adjusted["Expected away goals"],
                    delta=round(
                        adjusted["Expected away goals"]
                        - baseline["Expected away goals"], 2
                    )
                )

            if not match_absences.empty:
                st.dataframe(
                    match_absences,
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.write("No matched absence records for this fixture.")

            st.write(
                "Home win / Draw / Away win: "
                f"{baseline['Home win %']}% / {baseline['Draw %']}% / "
                f"{baseline['Away win %']}% (baseline) → "
                f"{adjusted['Home win %']}% / {adjusted['Draw %']}% / "
                f"{adjusted['Away win %']}% (adjusted)."
            )

    st.subheader("Understanding the model")
    st.write(""" - **Elo:** A rating of historical team strength. - **Attack:** Above 1.00 indicates above-average scoring in the model. - **Defence:** Below 1.00 indicates fewer goals conceded in the model. - **Form PPG:** Points per game over the latest five recorded matches. - **Expected goals (xG):** Model-estimated goals, not a provider's event-based expected-goals statistic. - **Injury adjustment:** A conservative heuristic. Confirmed absences receive more weight than questionable players. Attackers affect the team's estimated scoring; defenders and goalkeepers affect the opponent's estimated scoring. - **Player importance:** A manual 0–1 estimate because the injury feed does not reliably provide a player's expected minutes or importance. - **Strength /100:** A transformed Elo rating, not a probability of winning. """)

    st.caption(
        "Important: predictions are statistical estimates, not guarantees. "
        "The injury feed may be incomplete or may not match every club name. "
        "A missing API record must not be interpreted as proof that a team "
        "has no injuries. Player positions and importance should be checked "
        "before relying on injury-adjusted probabilities. The model has not "
        "yet been calibrated through historical backtesting."
    )

else:
    st.info(
        "Enter your football-data.org token, optionally enter your "
        "API-Football key, select competitions and tap "
        "'Load today's upcoming matches'."
    )
