"""Small read-only Upstox API client used by the LOCa dashboard.

The client deliberately exposes market-data and OAuth methods only. It does
not contain order-placement methods, so connecting an Upstox account cannot
place or modify trades from this application.
"""

import hashlib
import os
from urllib.parse import urlencode

import requests


UPSTOX_BASE_URL = "https://api.upstox.com"
UPSTOX_TIMEOUT = 20


class UpstoxError(RuntimeError):
  """Raised when Upstox authentication or market-data access fails."""


class UpstoxClient:
  def __init__(self, access_token=None):
    self.base_url = os.environ.get("UPSTOX_BASE_URL", UPSTOX_BASE_URL).rstrip("/")
    self.client_id = os.environ.get("UPSTOX_CLIENT_ID", "").strip()
    self.client_secret = os.environ.get("UPSTOX_CLIENT_SECRET", "").strip()
    self.redirect_uri = os.environ.get("UPSTOX_REDIRECT_URI", "").strip()
    configured_token = os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()
    self.access_token = (access_token if access_token is not None else
                         configured_token).strip()
    self.session = requests.Session()

  @property
  def connected(self):
    return bool(self.access_token)

  @property
  def oauth_configured(self):
    return bool(self.client_id and self.client_secret and self.redirect_uri)

  @property
  def cache_key(self):
    """Stable, non-secret cache partition for this access token."""
    if not self.access_token:
      return "disconnected"
    return hashlib.sha256(self.access_token.encode()).hexdigest()[:16]

  def authorization_url(self, state):
    if not self.client_id or not self.redirect_uri:
      raise UpstoxError(
          "UPSTOX_CLIENT_ID and UPSTOX_REDIRECT_URI must be configured.")
    params = {
        "response_type": "code",
        "client_id": self.client_id,
        "redirect_uri": self.redirect_uri,
        "state": state,
    }
    return (
        f"{self.base_url}/v2/login/authorization/dialog?{urlencode(params)}")

  def exchange_code(self, code):
    if not self.oauth_configured:
      raise UpstoxError(
          "UPSTOX_CLIENT_ID, UPSTOX_CLIENT_SECRET, and "
          "UPSTOX_REDIRECT_URI must be configured.")

    try:
      response = self.session.post(
          f"{self.base_url}/v2/login/authorization/token",
          headers={
              "Accept": "application/json",
              "Content-Type": "application/x-www-form-urlencoded",
          },
          data={
              "code": code,
              "client_id": self.client_id,
              "client_secret": self.client_secret,
              "redirect_uri": self.redirect_uri,
              "grant_type": "authorization_code",
          },
          timeout=UPSTOX_TIMEOUT,
      )
    except requests.RequestException as exc:
      raise UpstoxError("Upstox token exchange could not reach Upstox.") from exc
    if not response.ok:
      raise UpstoxError(
          f"Upstox token exchange failed (HTTP {response.status_code}).")
    try:
      payload = response.json()
    except ValueError as exc:
      raise UpstoxError("Upstox token exchange returned invalid JSON.") from exc
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token.strip():
      raise UpstoxError("Upstox did not return an access token.")
    self.access_token = token
    return payload

  def _get(self, path, params):
    if not self.access_token:
      raise UpstoxError(
          "Upstox is not connected. Configure UPSTOX_ACCESS_TOKEN or "
          "complete the OAuth connection first.")

    try:
      response = self.session.get(
          f"{self.base_url}{path}",
          params=params,
          headers={
              "Accept": "application/json",
              "Content-Type": "application/json",
              "Authorization": f"Bearer {self.access_token}",
          },
          timeout=UPSTOX_TIMEOUT,
      )
    except requests.RequestException as exc:
      raise UpstoxError("Upstox market-data request could not reach Upstox.") from exc
    if response.status_code in (401, 403):
      raise UpstoxError(
          "Upstox access token is invalid or expired. Generate a fresh token "
          "or reconnect the account.")
    if not response.ok:
      raise UpstoxError(
          f"Upstox market-data request failed (HTTP {response.status_code}).")
    try:
      payload = response.json()
    except ValueError as exc:
      raise UpstoxError("Upstox returned an invalid JSON response.") from exc
    if not isinstance(payload, dict):
      raise UpstoxError("Upstox returned an invalid market-data response.")
    if payload.get("status") not in (None, "success"):
      raise UpstoxError("Upstox returned an unsuccessful market-data response.")
    return payload

  def ltp(self, instrument_keys):
    if isinstance(instrument_keys, (list, tuple)):
      instrument_keys = ",".join(instrument_keys)
    return self._get(
        "/v3/market-quote/ltp",
        {"instrument_key": instrument_keys},
    )

  def option_chain(self, instrument_key, expiry="current_week"):
    return self._get(
        "/v2/option/chain",
        {
            "instrument_key": instrument_key,
            "expiry_date": expiry,
        },
    )


upstox = UpstoxClient()
