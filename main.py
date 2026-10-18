import os
import secrets
from hmac import compare_digest
from threading import Lock

from flask import (Flask, g, jsonify, redirect, render_template, request,
                   session, url_for)
from markupsafe import escape
import dataparser as dp
from upstox_client import UpstoxClient, UpstoxError, upstox

app = Flask(__name__, template_folder="")
app.config["SECRET_KEY"] = os.environ.get("LOCA_SECRET_KEY") or secrets.token_urlsafe(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("LOCA_COOKIE_SECURE", "").lower() == "true")

_upstox_tokens = {}
_upstox_tokens_lock = Lock()


def _token_for_session():
  connection_id = session.get("upstox_connection_id")
  if not connection_id:
    return None
  with _upstox_tokens_lock:
    return _upstox_tokens.get(connection_id)


@app.before_request
def bind_request_upstox():
  g.upstox_context_token = dp.bind_upstox_token(_token_for_session())


@app.teardown_request
def reset_request_upstox(_error):
  context_token = g.pop("upstox_context_token", None)
  if context_token is not None:
    dp.reset_upstox_context(context_token)


@app.errorhandler(dp.DataProviderError)
def handle_data_provider_error(error):
  message = escape(str(error))
  title = ("Live Upstox data is temporarily unavailable."
           if isinstance(error, dp.UpstoxDataError)
           else "Live market data is temporarily unavailable.")
  if request.path.startswith(("/quotes", "/snapshot/", "/pcr/")):
    return jsonify({"error": str(error)}), 503
  return (
      "<div style='padding:20px; color:#7a1f1f; background:#ffe8e8; "
      "border:1px solid #e0a0a0; border-radius:8px;'>"
      f"<b>{title}</b><br>"
      f"{message}</div>",
      503,
  )


def load_data(index):
  return dp.snapshot(index.upper())["table_html"]


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


@app.route('/upstox/status')
def upstox_status():
  status = dp.upstox_status()
  status["can_connect"] = status["oauth_configured"]
  status["can_disconnect"] = status["connection_mode"] == "session"
  status["message"] = (
      ("Upstox server token configured. Market data loads automatically."
       if status["connection_mode"] == "server" else
       "Upstox browser session connected.")
      if status["connected"] else
      ("Connect Upstox to load live SENSEX data."
       if status["oauth_configured"] else
       "Set UPSTOX_ACCESS_TOKEN on the server to enable SENSEX data.")
  )
  return jsonify(status)


@app.route('/upstox/connect')
def upstox_connect():
  if not upstox.oauth_configured:
    return jsonify({
        "error": "Upstox OAuth is not configured on this server.",
        "required": [
            "UPSTOX_CLIENT_ID", "UPSTOX_CLIENT_SECRET",
            "UPSTOX_REDIRECT_URI"
        ],
    }), 503

  state = secrets.token_urlsafe(32)
  session["upstox_oauth_state"] = state
  return redirect(upstox.authorization_url(state))


@app.route('/upstox/callback')
def upstox_callback():
  expected_state = session.pop("upstox_oauth_state", None)
  returned_state = request.args.get("state", "")
  if not expected_state or not returned_state or not compare_digest(
      expected_state, returned_state):
    return "Invalid Upstox OAuth state.", 400

  if request.args.get("error") or not request.args.get("code"):
    return redirect(url_for("index", upstox="cancelled"))

  try:
    # Keep the OAuth token out of the process-wide default client. It is held
    # in an in-memory store keyed by the signed browser session identifier.
    oauth_client = UpstoxClient()
    token_payload = oauth_client.exchange_code(request.args["code"])
  except UpstoxError:
    return redirect(url_for("index", upstox="error"))

  connection_id = secrets.token_urlsafe(24)
  with _upstox_tokens_lock:
    previous_id = session.get("upstox_connection_id")
    if previous_id:
      _upstox_tokens.pop(previous_id, None)
    _upstox_tokens[connection_id] = token_payload["access_token"]
  session["upstox_connection_id"] = connection_id
  return redirect(url_for("index", upstox="connected"))


@app.route('/upstox/disconnect', methods=["POST"])
def upstox_disconnect():
  connection_id = session.pop("upstox_connection_id", None)
  if connection_id:
    with _upstox_tokens_lock:
      _upstox_tokens.pop(connection_id, None)
  context_token = dp.bind_upstox_token(None)
  try:
    return jsonify(dp.upstox_status())
  finally:
    dp.reset_upstox_context(context_token)


# Route to fetch and display put-call ratio
@app.route('/pcr/<index>')
def get_pcr(index):
  return render_pcr(dp.pcr(index.upper()))


@app.route('/quotes')
def get_quotes():
  return jsonify(dp.quotes())


@app.route('/snapshot/<index>')
def get_snapshot(index):
  return jsonify(dp.snapshot(index.upper()))


# Route to fetch and display the updated data
@app.route('/index/<index>')
def get_data1(index):
  data = load_data(index)
  return data


@app.route('/<index>')
def get_banknifty(index):
  data = dp.niftyval(index.upper())
  return data


# Main function to run the Flask app
if __name__ == '__main__':
  host = os.environ.get('LOCA_HOST', '0.0.0.0')
  port = int(os.environ.get('LOCA_PORT') or os.environ.get('PORT') or 8090)
  app.run(host=host, port=port, threaded=True)
