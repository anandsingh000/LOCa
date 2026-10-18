![Screenshot](https://github.com/kunal-jangid/LOCa/blob/main/Screenshot%20(22).png "a title")
# Description
Live Option Chain Analyzer data fetcher, data pipeline, and online portal to derive tabular analysis on option chain data from NSE India and display trends for NIFTY, BANKNIFTY, and FINNIFTY option chain traders using Python, pandas data manipulation and styling, and flask integration with HTML/CSS/JS

The app reads index and option-chain data from Upstox when configured, with
an NSE fallback for NSE indexes. The table supports
drag-to-resize columns and marks ATM, support, resistance, and max-pain
strikes under the strike value. The selected table refreshes automatically
every 15 seconds while the page is open. Call and put volumes are displayed
as rounded Indian lakhs (for example, `10,877,460` appears as `109 lakh`),
while calculations continue to use the original raw values.




# Requirements
- Python 3.11 (the Render deployment is pinned to 3.11.9)
    * Libraries Required
        * Flask - Web framework
        * Matplotlib, NumPy, Pandas - Data analysis
        * JSON
        * Requests - Fetching data from API
- HTML + CSS + JavaScript




# Installation
- Clone the repository:  `git clone https://github.com/kunal-jangid/LOCa`
- Install the runtime dependencies: `pip install -r requirements.txt`
- Enter directory: `cd LOCa`
- Initiate the program: `py main.py`
- Open: `http://127.0.0.1:8090/`

On Windows, double-click `launch_loca.bat`. It creates/uses the local Python
environment and starts LOCa on port `8090`. Set `LOCA_PORT` to use another
available local port.

## Render deployment

`render.yaml` and `Procfile` are included for a Render web service. Connect
the GitHub repository in Render and deploy the `live-option-chain` service;
Render supplies the `PORT` environment variable automatically.

The repository also includes a GitHub Actions health ping every 10 minutes to
reduce free-plan sleeping. Render can still restart or suspend a free service;
guaranteed always-on availability requires a paid always-on instance.

## Upstox live data and SENSEX

The dashboard includes a SENSEX button. SENSEX option-chain data is loaded
from Upstox because NSE does not provide the same option-chain feed used by
this app. NIFTY, BANKNIFTY, and FINNIFTY continue to use NSE unless a valid
Upstox token is connected, in which case Upstox is preferred and NSE remains
the fallback.

### Recommended: server-side Analytics Token

Generate an **Analytics Token** from the Analytics tab in
[Upstox Developer Apps](https://account.upstox.com/developer/apps#analytics).
[Upstox documents](https://upstox.com/developer/api-documentation/analytics-token/)
this token as free, read-only, valid for one year unless revoked, and supported
for Market Quote and Option Chain GET APIs without a static IP.

In Render, open the web service's **Environment** page and save these as two
separate variables:

| Key | Value |
| --- | --- |
| `PYTHON_VERSION` | `3.11.9` |
| `UPSTOX_ACCESS_TOKEN` | The full Analytics Token, without a `Bearer ` prefix |

Choose **Save and deploy** after updating environment variables. Publish the
latest application code to the service's linked GitHub branch for the new
SENSEX and Upstox endpoints to be deployed. `.python-version` also pins the
runtime when no environment override is set.

The server reads this token on startup and uses it for visitors' market-data
requests. Visitors do not need to connect Upstox, and the owner's PC can be
switched off. Replace the token on expiry or revocation. The token is never
returned in API responses or sent to the browser. This mode needs no OAuth
client ID, client secret, or redirect URI.

### Optional: browser-session OAuth

The app also supports Upstox's authorization-code flow. It calls market-data
endpoints and does not include order-placement APIs. To enable the Connect
Upstox button, create an app in Upstox Developer Apps and register the exact
callback URL:

```text
https://<your-service-host>/upstox/callback
```

Set these server-side environment variables; never put the secret in the
browser or repository:

```text
UPSTOX_CLIENT_ID=<Upstox API key>
UPSTOX_CLIENT_SECRET=<Upstox API secret>
UPSTOX_REDIRECT_URI=https://<your-service-host>/upstox/callback
LOCA_SECRET_KEY=<stable random Flask session key>
LOCA_COOKIE_SECURE=true
```

OAuth access tokens are held in server memory for the signed browser session
on a single worker. They are cleared when the service restarts. Use the
server-side Analytics Token mode above for a shared dashboard that must remain
connected across restarts. A standard OAuth access token has a shorter validity
and is not a substitute for the one-year Analytics Token.

The option-chain expiry defaults to Upstox's `current_week` relative expiry.
Set `UPSTOX_EXPIRY` to another supported relative value or an explicit
`YYYY-MM-DD` date when needed.

## Checks

```text
python -m unittest -v test_loca.py
python -m py_compile main.py dataparser.py datastyler.py upstox_client.py
node --test test_dashboard.cjs
```

Node.js is needed only for the dashboard request-state tests, not for running
or deploying the Python app.

After deployment, `/health` checks the app, `/upstox/status` reports whether a
token is configured, and `/snapshot/SENSEX` verifies actual Upstox data access.
The status endpoint alone does not prove the saved token is valid.
