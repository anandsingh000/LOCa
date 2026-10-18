import os
import unittest
from unittest.mock import patch

import requests

import dataparser as dp
from main import app
from upstox_client import UpstoxClient, UpstoxError, upstox


OPTION_CHAIN_ROWS = [
    {
        "expiry": "2026-10-22",
        "strike_price": strike,
        "underlying_spot_price": 76120.0,
        "call_options": {
            "market_data": {
                "ltp": 120,
                "volume": 100000,
                "oi": 1000,
                "prev_oi": 900,
                "close_price": 100,
            }
        },
        "put_options": {
            "market_data": {
                "ltp": 110,
                "volume": 120000,
                "oi": 1200,
                "prev_oi": 1000,
                "close_price": 100,
            }
        },
    }
    for strike in (76000, 76100, 76200)
]


class UpstoxDashboardTests(unittest.TestCase):
  def setUp(self):
    self.client = app.test_client()
    dp._payload_cache.clear()
    self.nse_stub = patch.object(
        dp, "_nse_json", side_effect=dp.NSEDataError("NSE unavailable in this test"))
    self.nse_stub.start()
    self.addCleanup(self.nse_stub.stop)
    self.contracts_stub = patch.object(UpstoxClient, "option_contracts",
                                      return_value={"status": "success", "data": [
                                          {"expiry": "2026-10-29"},
                                          {"expiry": "2026-10-22"},
                                      ]})
    self.get_contracts = self.contracts_stub.start()
    self.addCleanup(self.contracts_stub.stop)
    self.original_config = (
        upstox.client_id,
        upstox.client_secret,
        upstox.redirect_uri,
        upstox.access_token,
    )
    upstox.client_id = "client-id"
    upstox.client_secret = "client-secret"
    upstox.redirect_uri = "http://localhost:8090/upstox/callback"
    upstox.access_token = ""

  def tearDown(self):
    self.client.post("/upstox/disconnect")
    dp._payload_cache.clear()
    (upstox.client_id, upstox.client_secret, upstox.redirect_uri,
     upstox.access_token) = self.original_config

  def _connect_with_mock_token(self):
    self.client.get("/upstox/connect")
    with self.client.session_transaction() as session:
      state = session["upstox_oauth_state"]
    response = self.client.get(
        f"/upstox/callback?state={state}&code=single-use")
    self.assertEqual(response.status_code, 302)

  def test_sensex_requires_upstox(self):
    response = self.client.get("/snapshot/SENSEX")
    self.assertEqual(response.status_code, 503)
    self.assertIn("Upstox", response.get_json()["error"])

  def test_sensex_uses_nearest_available_expiry(self):
    upstox.access_token = "analytics-test-token"

    def chain(instrument_key, expiry):
      return {"status": "success", "data": (
          OPTION_CHAIN_ROWS if expiry == "2026-10-22" else [])}

    with patch.dict(os.environ):
      os.environ.pop("UPSTOX_EXPIRY", None)
      with patch.object(UpstoxClient, "option_chain", side_effect=chain) as get_chain:
        response = self.client.get("/snapshot/SENSEX")

    self.assertEqual(response.status_code, 200, response.get_json())
    self.assertEqual(response.get_json()["source"], "Upstox live")
    self.assertEqual(response.get_json()["expiry"], "2026-10-22")
    get_chain.assert_called_once_with("BSE_INDEX|SENSEX", "2026-10-22")

  @patch.object(UpstoxClient, "option_chain", return_value={
      "status": "success", "data": OPTION_CHAIN_ROWS,
  })
  def test_explicit_expiry_overrides_discovery(self, get_chain):
    upstox.access_token = "analytics-test-token"
    with patch.dict(os.environ, {"UPSTOX_EXPIRY": "2026-10-22"}):
      response = self.client.get("/snapshot/SENSEX")
    self.assertEqual(response.status_code, 200)
    self.get_contracts.assert_not_called()
    get_chain.assert_called_once_with("BSE_INDEX|SENSEX", "2026-10-22")

  def test_missing_contracts_returns_clear_provider_error(self):
    upstox.access_token = "analytics-test-token"
    self.get_contracts.return_value = {"status": "success", "data": []}
    with patch.dict(os.environ, {"UPSTOX_EXPIRY": "nearest"}):
      response = self.client.get("/snapshot/SENSEX")
    self.assertEqual(response.status_code, 503)
    self.assertIn("no available option expiries", response.get_json()["error"])

  @patch.object(UpstoxClient, "exchange_code",
                return_value={"access_token": "session-token"})
  @patch.object(UpstoxClient, "ltp", return_value={
      "status": "success",
      "data": {"BSE_INDEX:SENSEX": {"last_price": 76120}},
  })
  @patch.object(UpstoxClient, "option_chain", return_value={
      "status": "success",
      "data": OPTION_CHAIN_ROWS,
  })
  def test_oauth_session_loads_sensex_chain(self, option_chain, ltp,
                                            exchange_code):
    self._connect_with_mock_token()

    response = self.client.get("/snapshot/SENSEX")
    self.assertEqual(response.status_code, 200)
    snapshot = response.get_json()
    self.assertEqual(snapshot["source"], "Upstox live")
    self.assertEqual(snapshot["value"], "76,120.00")
    self.assertIn("ATM", snapshot["table_html"])

    quotes = self.client.get("/quotes").get_json()
    self.assertEqual(quotes["values"]["SENSEX"], "76,120.00")
    self.assertTrue(quotes["sources"]["SENSEX"].startswith("Upstox"))

    # Connecting one browser must not connect other visitors, even while its
    # market-data payload is present in the server cache.
    other_visitor = app.test_client()
    self.assertFalse(other_visitor.get("/upstox/status").get_json()["connected"])
    self.assertEqual(other_visitor.get("/snapshot/SENSEX").status_code, 503)
    with self.client.session_transaction() as session:
      self.assertNotIn("access_token", session)
      self.assertNotEqual(session["upstox_connection_id"], "session-token")
    disconnected = self.client.post("/upstox/disconnect")
    self.assertFalse(disconnected.get_json()["connected"])
    self.assertEqual(self.client.get("/snapshot/SENSEX").status_code, 503)

  @patch.object(UpstoxClient, "option_chain", return_value={
      "status": "success", "data": OPTION_CHAIN_ROWS,
  })
  def test_server_token_serves_visitors_without_oauth(self, option_chain):
    upstox.access_token = "analytics-test-token"
    upstox.client_id = upstox.client_secret = upstox.redirect_uri = ""
    for visitor in (self.client, app.test_client()):
      status = visitor.get("/upstox/status").get_json()
      self.assertTrue(status["connected"])
      self.assertEqual(status["connection_mode"], "server")
      self.assertFalse(status["can_connect"])
      self.assertFalse(status["can_disconnect"])
      response = visitor.get("/snapshot/sensex")
      self.assertEqual(response.status_code, 200)
      self.assertEqual(response.get_json()["value"], "76,120.00")
      self.assertNotIn("analytics-test-token", response.get_data(as_text=True))
      self.assertNotIn("Set-Cookie", response.headers)

  @patch.object(UpstoxClient, "exchange_code")
  def test_invalid_oauth_state_never_exchanges_code(self, exchange_code):
    self.client.get("/upstox/connect")
    response = self.client.get("/upstox/callback?state=incorrect&code=single-use")
    self.assertEqual(response.status_code, 400)
    exchange_code.assert_not_called()

  @patch.object(UpstoxClient, "option_chain", side_effect=UpstoxError(
      "Upstox access token is invalid or expired."))
  def test_expired_token_returns_clear_sensex_error(self, option_chain):
    upstox.access_token = "expired-test-token"
    response = self.client.get("/snapshot/SENSEX")
    self.assertEqual(response.status_code, 503)
    self.assertIn("expired", response.get_json()["error"])

  @patch.object(UpstoxClient, "option_chain", side_effect=UpstoxError(
      "Upstox is temporarily unavailable."))
  def test_nifty_falls_back_to_nse(self, option_chain):
    upstox.access_token = "analytics-test-token"
    nse_chain = {"records": {
        "underlyingValue": 23120,
        "timestamp": "18-Oct-2026 15:30:00",
        "data": [{
            "strikePrice": strike,
            "CE": {"strikePrice": strike, "lastPrice": 150,
                   "totalTradedVolume": 200000, "openInterest": 2000},
            "PE": {"strikePrice": strike, "lastPrice": 130,
                   "totalTradedVolume": 300000, "openInterest": 3000},
        } for strike in (23000, 23100, 23200)],
    }}
    with patch.object(dp, "_nse_option_chain_json", return_value=nse_chain):
      response = self.client.get("/snapshot/NIFTY")
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.get_json()["source"], "NSE fallback")
    self.assertIn("lakh", response.get_json()["table_html"])

  def test_upstox_timeout_does_not_crash_dashboard(self):
    upstox.access_token = "analytics-test-token"
    with patch.object(upstox.session, "get", side_effect=requests.Timeout()):
      response = self.client.get("/snapshot/SENSEX")
    self.assertEqual(response.status_code, 503)
    self.assertIn("could not reach Upstox", response.get_json()["error"])

  def test_invalid_api_payload_is_a_provider_error(self):
    api = UpstoxClient(access_token="analytics-test-token")
    with patch.object(api.session, "get") as get:
      get.return_value.status_code = 200
      get.return_value.ok = True
      get.return_value.json.return_value = []
      with self.assertRaises(UpstoxError):
        api.ltp("BSE_INDEX|SENSEX")
    api.session.close()


if __name__ == "__main__":
  unittest.main()
