import math
import os
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

API_BASE = "https://v3.football.api-sports.io"
LEAGUES = {
    "Premier League (England)": 39,
    "La Liga (Spain)": 140,
    "Bundesliga (Germany)": 78,
    "Serie A (Italy)": 135,
    "Ligue 1 (France)": 61,
    "UEFA Champions League": 2,
    "Europa League": 3,
    "Primeira Liga (Portugal)": 94,
    "Eredivisie (Netherlands)": 88,
    "Saudi Pro League": 307,
    "MLS (USA)": 253,
}

st.set_page_config(page_title="Free Football Analysis Dashboard", page_icon="⚽", layout="wide")
st.title("⚽ Free Football Analysis Dashboard — Version 2")
st.caption("Automated fixtures + recent home/away form + Poisson probabilities for 1X2, goals and BTTS. Estimates are not guarantees.")

with st.sidebar:
    st.header("Data connection")
    api_key = st.text_input("API-Football API key", type="password", help="Create a free account at api-football.com, then copy the key from Account → My Access.")
    st.caption("Free API-Football plan currently advertises 100 requests/day. The app caches responses to reduce usage.")
    selected_leagues = st.multiselect("Competitions", list(LEAGUES.keys()), default=["Premier League (England)", "La Liga (Spain)", "Bundesliga (Germany)", "Serie A (Italy)", "Ligue 1 (France)", "UEFA Champions League"])
    match_date = st.date_input("Fixture date", value=date.today() + timedelta(days=1))
    n_recent = st.slider("Recent matches per team", min_value=5, max_value=15, value=10)
    go = st.button("Load fixtures and analyse", type="primary", use_container_width=True)

st.info("To activate automatic data, add your own free API key in the sidebar. The key is used only for requests from this app session; do not share screenshots containing it.")

def api_get(endpoint: str, params: Dict[str, Any], key: str) -> List[dict]:
    url = f"{API_BASE}/{endpoint.lstrip('/')}"
    try:
        r = requests.get(url, headers={"x-apisports-key": key}, params=params, timeout=25)
        if r.status_code == 401 or r.status_code == 403:
            raise RuntimeError("API key rejected or access not permitted. Check your key and plan.")
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            raise RuntimeError(str(data["errors"]))
        return data.get("response", [])
    except requests.RequestException as e:
        raise RuntimeError(f"Network/API request failed: {e}") from e

@st.cache_data(ttl=3600, show_spinner=False)
def cached_fixtures(day: str, league_id: int, season: int, key: str) -> List[dict]:
    return api_get("fixtures", {"date": day, "league": league_id, "season": season, "timezone": "Africa/Lagos"}, key)

@st.cache_data(ttl=21600, show_spinner=False)
def cached_team_matches(team_id: int, league_id: int, season: int, last: int, key: str) -> List[dict]:
    return api_get("fixtures", {"team": team_id, "league": league_id, "season": season, "last": last, "status": "FT", "timezone": "Africa/Lagos"}, key)

def poisson_p(k: int, lam: float) -> float:
    if lam < 0:
        return 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)

def probabilities(lh: float, la: float, max_goals: int = 10) -> dict:
    scores = [(h, a, poisson_p(h, lh) * poisson_p(a, la))
              for h in range(max_goals + 1) for a in range(max_goals + 1)]
    total = sum(p for _, _, p in scores)
    if total <= 0:
        return {}
    # Normalise the truncated score grid so displayed market probabilities sum coherently.
    scores = [(h, a, p / total) for h, a, p in scores]
    home = sum(p for h, a, p in scores if h > a)
    draw = sum(p for h, a, p in scores if h == a)
    away = sum(p for h, a, p in scores if h < a)
    over15 = sum(p for h, a, p in scores if h + a >= 2)
    over25 = sum(p for h, a, p in scores if h + a >= 3)
    under25 = 1 - over25
    btts_yes = sum(p for h, a, p in scores if h >= 1 and a >= 1)
    top = sorted(scores, key=lambda x: x[2], reverse=True)[:3]
    return {
        "Home win": home, "Draw": draw, "Away win": away,
        "Over 1.5": over15, "Over 2.5": over25, "Under 2.5": under25,
        "BTTS Yes": btts_yes, "BTTS No": 1 - btts_yes,
        "Top scorelines": ", ".join(f"{h}-{a} ({p:.0%})" for h, a, p in top)
    }

def form_points(matches: List[dict], team_id: int) -> Tuple[int, str]:
    pts, symbols = 0, []
    # API returns latest-first; use that order for recent form display.
    for m in matches:
        teams, goals = m.get("teams", {}), m.get("goals", {})
        hid, aid = teams.get("home", {}).get("id"), teams.get("away", {}).get("id")
        hg, ag = goals.get("home"), goals.get("away")
        if hg is None or ag is None:
            continue
        if hg == ag:
            pts += 1
            symbols.append("D")
        else:
            win = (hid == team_id and hg > ag) or (aid == team_id and ag > hg)
            if win:
                pts += 3
                symbols.append("W")
            else:
                symbols.append("L")
    return pts, " ".join(symbols[:5])

def team_rates(matches: List[dict], team_id: int, venue: str) -> Optional[dict]:
    scored, conceded, count = 0, 0, 0
    for m in matches:
        teams, goals = m.get("teams", {}), m.get("goals", {})
        hid, aid = teams.get("home", {}).get("id"), teams.get("away", {}).get("id")
        hg, ag = goals.get("home"), goals.get("away")
        if hg is None or ag is None:
            continue
        if venue == "home" and hid == team_id:
            scored += hg; conceded += ag; count += 1
        elif venue == "away" and aid == team_id:
            scored += ag; conceded += hg; count += 1
    if count < 3:
        return None
    return {"scored": scored / count, "conceded": conceded / count, "n": count}

def analyse_fixture(fixture: dict, season: int, last: int, key: str, league_id: int) -> dict:
    teams = fixture["teams"]
    home, away = teams["home"], teams["away"]
    hm = cached_team_matches(home["id"], league_id, season, last, key)
    am = cached_team_matches(away["id"], league_id, season, last, key)
    hr = team_rates(hm, home["id"], "home")
    ar = team_rates(am, away["id"], "away")
    # Fallback to all venue matches from the requested recent sample if fewer than 3.
    if hr is None:
        hr = team_rates(hm, home["id"], "away")
    if ar is None:
        ar = team_rates(am, away["id"], "home")
    # Conservative fallback if API has insufficient results.
    if hr is None:
        hr = {"scored": 1.35, "conceded": 1.25, "n": 0}
    if ar is None:
        ar = {"scored": 1.15, "conceded": 1.35, "n": 0}
    # Expected goals average attack rate and opposing venue-specific concession rate.
    lh = max(0.15, (hr["scored"] + ar["conceded"]) / 2)
    la = max(0.15, (ar["scored"] + hr["conceded"]) / 2)
    probs = probabilities(lh, la)
    hp, hf = form_points(hm, home["id"])
    ap, af = form_points(am, away["id"])
    return {
        "Date/time (local)": fixture.get("fixture", {}).get("date", ""),
        "League": fixture.get("league", {}).get("name", ""),
        "Home": home.get("name", ""), "Away": away.get("name", ""),
        "Home expected goals": round(lh, 2), "Away expected goals": round(la, 2),
        "1 (Home) %": round(probs["Home win"] * 100, 1),
        "X (Draw) %": round(probs["Draw"] * 100, 1),
        "2 (Away) %": round(probs["Away win"] * 100, 1),
        "Over 1.5 %": round(probs["Over 1.5"] * 100, 1),
        "Over 2.5 %": round(probs["Over 2.5"] * 100, 1),
        "Under 2.5 %": round(probs["Under 2.5"] * 100, 1),
        "BTTS Yes %": round(probs["BTTS Yes"] * 100, 1),
        "BTTS No %": round(probs["BTTS No"] * 100, 1),
        "Home form (last 5)": hf or "n/a", "Away form (last 5)": af or "n/a",
        "Home venue sample": hr["n"], "Away venue sample": ar["n"],
        "Most likely scorelines": probs["Top scorelines"],
        "Data note": "Low/limited venue sample" if hr["n"] < 3 or ar["n"] < 3 else "Statistical estimate; check lineups/injuries"
    }

def season_for(d: date) -> int:
    # European football seasons typically start in the second half of the year.
    return d.year if d.month >= 7 else d.year - 1

if go:
    if not api_key.strip():
        st.error("Enter your API-Football key in the sidebar. You can create a free account at api-football.com.")
        st.stop()
    if not selected_leagues:
        st.error("Select at least one competition.")
        st.stop()
    season = season_for(match_date)
    all_fx, errors = [], []
    with st.spinner("Retrieving fixtures and analysing recent team performance. This may use several API requests per match."):
        for league_name in selected_leagues:
            lid = LEAGUES[league_name]
            try:
                fx = cached_fixtures(match_date.isoformat(), lid, season, api_key.strip())
                for f in fx:
                    f.setdefault("league", {})["name"] = league_name
                    all_fx.append((f, lid))
            except Exception as e:
                errors.append(f"{league_name}: {e}")
        results = []
        # Request budget safety: analyse up to 8 fixtures per run on the free API tier.
        for fixture, lid in all_fx[:8]:
            try:
                results.append(analyse_fixture(fixture, season, n_recent, api_key.strip(), lid))
            except Exception as e:
                errors.append(f"{fixture.get('teams',{}).get('home',{}).get('name','Fixture')}: {e}")
    if errors:
        with st.expander("Data/API messages"):
            for e in errors:
                st.warning(e)
    if not all_fx:
        st.warning("No fixtures returned for this date/competition/season. Try another date or verify the competition has data for this season.")
    elif not results:
        st.warning("Fixtures were found, but analysis could not be completed. Check API coverage and your request quota.")
    else:
        st.success(f"Analysed {len(results)} fixture(s). The app limits each run to 8 fixtures to help conserve the free API request quota.")
        df = pd.DataFrame(results)
        st.subheader("Fixture probabilities")
        st.dataframe(df, use_container_width=True, hide_index=True)
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("Download analysis as CSV", csv, file_name=f"football_analysis_{match_date.isoformat()}.csv", mime="text/csv")
        st.subheader("Compare one fixture")
        choices = [f"{r['Home']} vs {r['Away']} ({r['League']})" for r in results]
        choice = st.selectbox("Select fixture", choices)
        row = results[choices.index(choice)]
        c1, c2, c3 = st.columns(3)
        c1.metric("Home win", f"{row['1 (Home) %']:.1f}%")
        c2.metric("Draw", f"{row['X (Draw) %']:.1f}%")
        c3.metric("Away win", f"{row['2 (Away) %']:.1f}%")
        c4, c5, c6 = st.columns(3)
        c4.metric("Over 2.5 goals", f"{row['Over 2.5 %']:.1f}%")
        c5.metric("Under 2.5 goals", f"{row['Under 2.5 %']:.1f}%")
        c6.metric("BTTS Yes", f"{row['BTTS Yes %']:.1f}%")
        st.write(f"**Expected goals:** {row['Home']} {row['Home expected goals']} — {row['Away expected goals']} {row['Away expected goals']}")
        st.write(f"**Most likely scorelines:** {row['Most likely scorelines']}")
        st.write(f"**Recent form:** {row['Home']} — {row['Home form (last 5)']} | {row['Away']} — {row['Away form (last 5)']}")
        st.caption("The model uses recent completed fixtures and a simple independent Poisson goal model. It does not yet include lineup, injury, weather, market-odds or referee adjustments. Probabilities can be miscalibrated, especially with small samples.")

st.divider()
st.subheader("How to set it up")
st.markdown("""
1. Create a free account at [API-Football](https://www.api-football.com/).
2. Open **Account → My Access** and copy your API key.
3. Install Python 3.10+ on a computer, then open a terminal in this folder.
4. Run `pip install -r requirements.txt`.
5. Run `streamlit run app.py` and open the local address shown in the terminal.
6. Enter your key in the sidebar, choose competitions/date, and click **Load fixtures and analyse**.

**Free-tier limits:** API-Football currently advertises 100 requests/day on its free plan. The app caches fixture data for 1 hour and team-match data for 6 hours, and analyses at most 8 fixtures per run. Actual league/season data coverage can vary. Keep your API key private.
""")
st.caption("For research and education only. Not financial advice. Never stake money you cannot afford to lose; no model guarantees betting profit.")
