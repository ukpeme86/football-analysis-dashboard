# Football Analysis Dashboard V3

Uses football-data.org API v4. Enter your private token in the dashboard sidebar; never commit it to GitHub.

## Deploy
On Streamlit Community Cloud, set the app file to `app.py`. Requirements are in `requirements.txt`.

## Notes
The free plan may restrict competition coverage and historical match data. Errors are shown for restricted competitions. The estimates use a simple Poisson model and recent finished matches from the same competition; they are not guarantees and do not account for injuries, line-ups, odds, or weather.
