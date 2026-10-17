![Screenshot](https://github.com/kunal-jangid/LOCa/blob/main/Screenshot%20(22).png "a title")
# Description
Live Option Chain Analyzer data fetcher, data pipeline, and online portal to derive tabular analysis on option chain data from NSE India and display trends for NIFTY, BANKNIFTY, and FINNIFTY option chain traders using Python, pandas data manipulation and styling, and flask integration with HTML/CSS/JS

The app reads live index and option-chain data from NSE. The table supports
drag-to-resize columns and marks ATM, support, resistance, and max-pain
strikes under the strike value. The selected table refreshes automatically
every 15 seconds while the page is open. Call and put volumes are displayed
as rounded Indian lakhs (for example, `10,877,460` appears as `109 lakh`),
while calculations continue to use the original raw values.




# Requirements
- Python (v3.6+)
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
the GitHub repository in Render and deploy the `loca-option-chain` service;
Render supplies the `PORT` environment variable automatically.
