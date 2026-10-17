import os

from flask import Flask, jsonify, render_template
from markupsafe import escape
import dataparser as dp

app = Flask(__name__, template_folder="")


@app.errorhandler(dp.NSEDataError)
def handle_nse_data_error(error):
  message = escape(str(error))
  return (
      "<div style='padding:20px; color:#7a1f1f; background:#ffe8e8; "
      "border:1px solid #e0a0a0; border-radius:8px;'>"
      "<b>Live NSE data is temporarily unavailable.</b><br>"
      f"{message}</div>",
      503,
  )


def load_data(index):
  return dp.snapshot(index)["table_html"]


def render_pcr(items):
  pcr_html = ""
  for item in items:
    pcr_html += (
        "<li><b>" + item[3] + "</b><br><b>Call: </b>" + item[1] +
        "<br><b>Put: </b>" + item[0] + "<br><b>Ratio: </b>" + item[2] +
        "</li>")
  return pcr_html


# Route to render the webpage
@app.route('/')
def index():
  return render_template('webpage.html')


@app.route('/health')
def health():
  return jsonify({"status": "ok"})


# Route to fetch and display put-call ratio
@app.route('/pcr/<index>')
def get_pcr(index):
  return render_pcr(dp.pcr(index))


@app.route('/quotes')
def get_quotes():
  return jsonify(dp.quotes())


@app.route('/snapshot/<index>')
def get_snapshot(index):
  return jsonify(dp.snapshot(index))


# Route to fetch and display the updated data
@app.route('/index/<index>')
def get_data1(index):
  data = load_data(index)
  return data


@app.route('/<index>')
def get_banknifty(index):
  data = dp.niftyval(index)
  return data


# Main function to run the Flask app
if __name__ == '__main__':
  host = os.environ.get('LOCA_HOST', '0.0.0.0')
  port = int(os.environ.get('LOCA_PORT') or os.environ.get('PORT') or 8090)
  app.run(host=host, port=port, threaded=True)
