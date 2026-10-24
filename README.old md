# Free Football Analysis Dashboard — Version 2

## What it does
- Retrieves fixtures for selected major competitions from API-Football.
- Pulls recent completed matches for each team.
- Calculates home/away goal rates, expected goals, recent form and Poisson-model probabilities for:
  - Home win / draw / away win (1X2)
  - Over 1.5 goals
  - Over 2.5 goals / Under 2.5 goals
  - BTTS Yes / No
- Exports analysis to CSV.

## Requirements
Python 3.10 or newer and an internet connection.

## Setup
1. Create a free API-Football account: https://www.api-football.com/
2. Copy your key from Account → My Access.
3. Extract this folder on your computer.
4. In a terminal in this folder run:
   `pip install -r requirements.txt`
5. Run:
   `streamlit run app.py`
6. Open the local web address Streamlit prints and enter the API key in the sidebar.

## Important limitations
- API-Football free plan advertises 100 requests/day; coverage can vary by league and season.
- The app analyses up to 8 fixtures per run to conserve requests.
- Recent-team match calls are cached in memory for six hours and fixture calls for one hour.
- The model is a simple independent Poisson baseline, not a trained machine-learning model.
- It does not yet account for injuries, lineups, suspensions, travel/rest, weather, odds or bookmaker margin.
- If fewer than three matches are available for a team's expected venue, the model falls back to the opposite venue sample; if that is unavailable, it uses conservative default rates. The dashboard flags small venue samples.
- Historical accuracy and profitability are not guaranteed. Test on past/future unseen matches before considering any real stakes.
- Keep the API key private; do not publish it or commit it to a public repository.

## Model outline
Expected home goals = mean(home team's home goals scored per match, away team's away goals conceded per match).
Expected away goals = mean(away team's away goals scored per match, home team's home goals conceded per match).
An independent Poisson scoreline grid (0–10 goals per side) produces the market probabilities. The truncated grid is normalised.
