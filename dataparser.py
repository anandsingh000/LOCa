from contextvars import ContextVar
from datetime import date, datetime, timezone
import os
from threading import Lock
from time import monotonic

import pandas as pd
import requests
from requests import RequestException

import datastyler as ds
from upstox_client import UpstoxClient, UpstoxError, upstox


NSE_BASE_URL = "https://www.nseindia.com"
NSE_TIMEOUT = 20
CACHE_TTL_SECONDS = 5
CONTRACT_CACHE_TTL_SECONDS = 60
headers = {
    "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

_INDEX_ALIASES = {
    "NIFTY": {"NIFTY", "NIFTY 50"},
    "BANKNIFTY": {"BANKNIFTY", "NIFTY BANK"},
    "FINNIFTY": {"FINNIFTY", "NIFTY FIN SERVICE",
                 "NIFTY FINANCIAL SERVICES"},
    "SENSEX": {"SENSEX"},
}
_UPSTOX_INDEX_KEYS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "SENSEX": "BSE_INDEX|SENSEX",
}
_NSE_INDEXES = {"NIFTY", "BANKNIFTY", "FINNIFTY"}
_nse_session = requests.Session()
_nse_session_primed = False
_cache_lock = Lock()
_network_lock = Lock()
_payload_cache = {}
_upstox_context = ContextVar("loca_upstox_context", default=None)


class DataProviderError(RuntimeError):
  """Raised when a live market-data provider cannot answer."""


class NSEDataError(DataProviderError):
  """Raised when NSE cannot provide a usable live-data response."""


class UpstoxDataError(DataProviderError):
  """Raised when Upstox cannot provide a usable live-data response."""


def _cached_payload(key, loader, ttl=CACHE_TTL_SECONDS):
  now = monotonic()
  with _cache_lock:
    cached = _payload_cache.get(key)
    if cached and now - cached[0] < ttl:
      return cached[1]

  # Re-check after acquiring the network lock so simultaneous browser
  # requests do not make duplicate NSE calls on a cold cache.
  with _network_lock:
    now = monotonic()
    with _cache_lock:
      cached = _payload_cache.get(key)
      if cached and now - cached[0] < ttl:
        return cached[1]
    value = loader()
    with _cache_lock:
      _payload_cache[key] = (monotonic(), value)
    return value


def _request_headers(accept="application/json, text/plain, */*"):
  return {
      **headers,
      "Accept": accept,
      "Referer": f"{NSE_BASE_URL}/option-chain",
  }


def _prime_nse_session():
  global _nse_session_primed
  if _nse_session_primed:
    return

  try:
    response = _nse_session.get(
        f"{NSE_BASE_URL}/option-chain",
        headers=_request_headers("text/html,application/xhtml+xml"),
        timeout=NSE_TIMEOUT,
    )
    response.raise_for_status()
  except RequestException as exc:
    raise NSEDataError(
        "NSE option-chain page could not be reached. "
        "Please try again after a short while.") from exc
  _nse_session_primed = True


def _nse_json(path, params=None):
  global _nse_session_primed
  last_error = None

  for attempt in range(2):
    _prime_nse_session()
    try:
      response = _nse_session.get(
          f"{NSE_BASE_URL}{path}",
          params=params,
          headers=_request_headers(),
          timeout=NSE_TIMEOUT,
      )
      if response.status_code in (401, 403) and attempt == 0:
        _nse_session_primed = False
        _nse_session.cookies.clear()
        continue
      response.raise_for_status()
      data = response.json()
      if not data:
        raise NSEDataError(f"NSE returned no data for {path}.")
      return data
    except NSEDataError:
      raise
    except RequestException as exc:
      last_error = exc
    except ValueError as exc:
      last_error = exc

  detail = str(last_error) if last_error else "unknown response error"
  raise NSEDataError(
      f"NSE live data request failed for {path}: {detail}") from last_error


def _validate_index(index):
  if index not in _INDEX_ALIASES:
    raise NSEDataError(f"Unsupported index: {index}")


def upstox_status():
  client = _current_upstox()
  return {
      "connected": client.connected,
      "oauth_configured": client.oauth_configured,
      "connection_mode": (
          "session" if _upstox_context.get() else
          "server" if client.connected else "none"),
      "provider": "Upstox live" if client.connected else "NSE fallback",
  }


def bind_upstox_token(access_token):
  """Bind a request-local Upstox token without putting it in the session cookie."""
  client = UpstoxClient(access_token=access_token) if access_token else None
  return _upstox_context.set(client)


def reset_upstox_context(token):
  client = _upstox_context.get()
  if client is not None:
    client.session.close()
  _upstox_context.reset(token)


def _current_upstox():
  return _upstox_context.get() or upstox


def _upstox_enabled(index):
  return index in _UPSTOX_INDEX_KEYS and _current_upstox().connected


def _upstox_ltp_value(index):
  client = _current_upstox()
  payload = _cached_payload(
      f"upstox-ltp:{client.cache_key}:{index}",
      lambda: client.ltp(_UPSTOX_INDEX_KEYS[index]),
  )
  data = payload.get("data", {})
  if not isinstance(data, dict) or not data:
    raise UpstoxDataError(f"Upstox returned no quote for {index}.")
  quote = next(iter(data.values()))
  if not isinstance(quote, dict):
    raise UpstoxDataError(f"Upstox returned an invalid quote for {index}.")
  for key in ("last_price", "ltp"):
    if quote.get(key) is not None:
      try:
        return float(quote[key])
      except (TypeError, ValueError) as exc:
        raise UpstoxDataError(
            f"Upstox returned an invalid last price for {index}.") from exc
  raise UpstoxDataError(f"Upstox returned no last price for {index}.")


def _upstox_change_percent(market_data):
  close = float(market_data.get("close_price") or 0)
  last = float(market_data.get("ltp") or 0)
  return ((last - close) / close * 100) if close else 0


def _upstox_option_record(option, strike):
  if not option:
    return None
  market_data = option.get("market_data", {})
  return {
      "strikePrice": strike,
      "pChange": _upstox_change_percent(market_data),
      "lastPrice": market_data.get("ltp", 0),
      "totalTradedVolume": market_data.get("volume", 0),
      "changeinOpenInterest": (
          float(market_data.get("oi") or 0) -
          float(market_data.get("prev_oi") or 0)),
      "openInterest": market_data.get("oi", 0),
      "impliedVolatility": (option.get("option_greeks") or {}).get("iv", 0),
  }


def _upstox_expiry(index, client):
  configured_expiry = os.environ.get("UPSTOX_EXPIRY", "nearest").strip()
  if configured_expiry and configured_expiry.lower() not in ("nearest", "auto"):
    return configured_expiry

  payload = _cached_payload(
      f"upstox-contracts:{client.cache_key}:{index}",
      lambda: client.option_contracts(_UPSTOX_INDEX_KEYS[index]),
      ttl=CONTRACT_CACHE_TTL_SECONDS,
  )
  contracts = payload.get("data", [])
  if not isinstance(contracts, list):
    raise UpstoxDataError(f"Upstox returned invalid option contracts for {index}.")
  expiries = set()
  # Upstox's contracts endpoint lists the currently available contracts.
  # Select its first actual expiry instead of guessing a weekly date, which
  # fails after weekly expiry or for indexes with monthly-only contracts.
  for contract in contracts:
    if not isinstance(contract, dict):
      continue
    try:
      expiries.add(date.fromisoformat(contract["expiry"]))
    except (KeyError, TypeError, ValueError):
      continue
  if not expiries:
    raise UpstoxDataError(f"Upstox returned no available option expiries for {index}.")
  return min(expiries).isoformat()


def _upstox_option_chain_json(index):
  client = _current_upstox()
  expiry_date = _upstox_expiry(index, client)
  payload = _cached_payload(
      f"upstox-option-chain:{client.cache_key}:{index}:{expiry_date}",
      lambda: client.option_chain(_UPSTOX_INDEX_KEYS[index], expiry_date),
  )
  rows = []
  underlying = None
  expiry = None
  data = payload.get("data", [])
  if not isinstance(data, list):
    raise UpstoxDataError(f"Upstox returned an invalid option chain for {index}.")
  for item in data:
    if not isinstance(item, dict):
      raise UpstoxDataError(f"Upstox returned an invalid option row for {index}.")
    strike = item.get("strike_price")
    if strike is None:
      continue
    underlying = item.get("underlying_spot_price", underlying)
    expiry = item.get("expiry", expiry)
    try:
      record = {
          "strikePrice": strike,
          "expiryDate": expiry,
          "CE": _upstox_option_record(item.get("call_options"), strike),
          "PE": _upstox_option_record(item.get("put_options"), strike),
      }
    except (AttributeError, TypeError, ValueError) as exc:
      raise UpstoxDataError(
          f"Upstox returned invalid option prices for {index}.") from exc
    if record["CE"] or record["PE"]:
      rows.append(record)
  if not rows or underlying is None:
    raise UpstoxDataError(f"Upstox returned no option-chain rows for {index}.")
  try:
    underlying = float(underlying)
  except (TypeError, ValueError) as exc:
    raise UpstoxDataError(f"Upstox returned an invalid spot price for {index}.") from exc
  return {
      "records": {
          "data": rows,
          "underlyingValue": underlying,
          "timestamp": datetime.now(timezone.utc).isoformat(),
          "expiryDate": expiry,
      }
  }


def _provider_option_chain_json(index):
  _validate_index(index)
  if _upstox_enabled(index):
    try:
      return _upstox_option_chain_json(index), "Upstox live"
    except (UpstoxError, UpstoxDataError) as exc:
      if index not in _NSE_INDEXES:
        raise UpstoxDataError(str(exc)) from exc
  if index not in _NSE_INDEXES:
    raise UpstoxDataError(
        "Sensex live data requires an Upstox connection. "
        "Configure UPSTOX_ACCESS_TOKEN or use the Upstox Connect button.")
  return _nse_option_chain_json(index), "NSE fallback"


def _index_value(index):
  _validate_index(index)
  payload = _cached_payload(
      "all-indices",
      lambda: _nse_json("/api/allIndices"),
  )
  aliases = _INDEX_ALIASES[index]

  for item in payload.get("data", []):
    if item.get("index") in aliases or item.get("indexSymbol") in aliases:
      try:
        return float(item["last"])
      except (KeyError, TypeError, ValueError) as exc:
        raise NSEDataError(f"NSE returned an invalid value for {index}.") from exc

  raise NSEDataError(f"NSE did not return a value for {index}.")


def _provider_index_value(index):
  _validate_index(index)
  if _upstox_enabled(index):
    try:
      return _upstox_ltp_value(index), "Upstox live"
    except (UpstoxError, UpstoxDataError) as exc:
      if index not in _NSE_INDEXES:
        raise UpstoxDataError(str(exc)) from exc
  if index not in _NSE_INDEXES:
    raise UpstoxDataError(
        "Sensex live quote requires an Upstox connection. "
        "Configure UPSTOX_ACCESS_TOKEN or use the Upstox Connect button.")
  return _index_value(index), "NSE fallback"


def nifty():
  return niftyval("NIFTY")


def niftyval(index):
  value, _ = _provider_index_value(index)
  return f"{value:,.2f}"


def _nse_option_chain_json(index):
  _validate_index(index)
  contract_info = _cached_payload(
      f"contract-info:{index}",
      lambda: _nse_json(
          "/api/option-chain-contract-info",
          params={"symbol": index},
      ),
      ttl=CONTRACT_CACHE_TTL_SECONDS,
  )
  expiry_dates = contract_info.get("expiryDates", [])
  if not expiry_dates:
    raise NSEDataError(f"NSE returned no expiry dates for {index}.")

  payload = _cached_payload(
      f"option-chain:{index}:{expiry_dates[0]}",
      lambda: _nse_json(
          "/api/option-chain-v3",
          params={
              "type": "Indices",
              "symbol": index,
              "expiry": expiry_dates[0],
          },
      ),
  )
  if not payload.get("records", {}).get("data"):
    raise NSEDataError(f"NSE returned no option-chain rows for {index}.")
  return payload


def IV(index):
  json_obj, _ = _provider_option_chain_json(index)
  put_values = []
  call_values = []
  for row in json_obj["records"]["data"]:
    if row.get("PE"):
      put_values.append(row["PE"].get("impliedVolatility", 0))
    if row.get("CE"):
      call_values.append(row["CE"].get("impliedVolatility", 0))
  return pd.Series(put_values), pd.Series(call_values)


def removeNeg(value):
  return max(value, 0)


def _prepared_dataframe(json_obj, underlying):
  result_df = raw_to_dataframe(json_obj)
  premium = result_df["STRIKE"] - underlying
  result_df.insert(4, "Prem_c", (premium * -1).tolist())
  result_df.insert(10, "Prem_p", premium.tolist())
  result_df["Prem_c"] = result_df["Prem_c"].apply(removeNeg)
  result_df["Prem_p"] = result_df["Prem_p"].apply(removeNeg)
  return result_df


def _level_value(value):
  value = float(value)
  return int(value) if value.is_integer() else round(value, 2)


def calculate_levels(df, underlying):
  """Calculate the four strike markers shown below the STRIKE value."""
  strikes = sorted(float(value) for value in df["STRIKE"].unique())
  if not strikes:
    raise NSEDataError("NSE returned no strike prices.")

  atm = min(strikes, key=lambda strike: abs(strike - underlying))
  below_atm = df[df["STRIKE"] <= atm]
  above_atm = df[df["STRIKE"] >= atm]
  support_rows = below_atm if not below_atm.empty else df
  resistance_rows = above_atm if not above_atm.empty else df
  support = float(support_rows.loc[
      support_rows["openInterest_p"].idxmax(), "STRIKE"])
  resistance = float(resistance_rows.loc[
      resistance_rows["openInterest_c"].idxmax(), "STRIKE"])

  call_oi = dict(zip(df["STRIKE"].astype(float), df["openInterest_c"]))
  put_oi = dict(zip(df["STRIKE"].astype(float), df["openInterest_p"]))
  max_pain = min(
      strikes,
      key=lambda candidate: sum(
          call_oi[strike] * max(candidate - strike, 0) +
          put_oi[strike] * max(strike - candidate, 0)
          for strike in strikes
      ),
  )

  levels = {
      "atm": _level_value(atm),
      "support": _level_value(support),
      "resistance": _level_value(resistance),
      "maxpain": _level_value(max_pain),
  }
  markers = {}
  for label, value in levels.items():
    markers.setdefault(str(value), []).append(label.upper())
  levels["markers"] = markers
  return levels


def _pcr_from_dataframe(df, underlying):
  target_strike = (int(underlying) // 100) * 100
  target_row = (df["STRIKE"] - target_strike).abs().idxmin()
  target_position = df.index.get_loc(target_row)

  result = []
  for column in ["VOLUME", "CHNG_OI", "openInterest"]:
    put_start = max(0, target_position - 10)
    put_sum = df[f"{column}_p"].iloc[put_start:target_position].sum()
    call_sum = df[f"{column}_c"].iloc[target_position:target_position + 10].sum()
    ratio = round(put_sum / call_sum, 4) if call_sum else 0
    result.append([str(put_sum), str(call_sum), str(ratio), column])
  return result


def _underlying_value(json_obj, index):
  underlying = json_obj["records"].get("underlyingValue")
  return (float(underlying) if underlying is not None else
          _provider_index_value(index)[0])


def quotes():
  """Return all supported index values and the active provider per index."""
  values = {}
  sources = {}
  errors = {}
  for index in _INDEX_ALIASES:
    try:
      value, source = _provider_index_value(index)
      values[index] = f"{value:,.2f}"
      sources[index] = source
    except UpstoxDataError as exc:
      values[index] = None
      sources[index] = ("Connect Upstox" if not _current_upstox().connected
                        else "Upstox unavailable")
      errors[index] = str(exc)
    except DataProviderError as exc:
      values[index] = None
      sources[index] = "Unavailable"
      errors[index] = str(exc)
  return {
      "values": values,
      "sources": sources,
      "errors": errors,
      "updated_at": datetime.now(timezone.utc).isoformat(),
  }


def snapshot(index):
  """Build table, PCR and strike markers from one option-chain payload."""
  json_obj, source = _provider_option_chain_json(index)
  underlying = _underlying_value(json_obj, index)
  result_df = _prepared_dataframe(json_obj, underlying)
  levels = calculate_levels(result_df, underlying)
  return {
      "index": index,
      "value": f"{underlying:,.2f}",
      "table_html": ds.prettier(
          result_df,
          index,
          nifty_value=underlying,
          strike_markers=levels["markers"],
      ),
      "pcr": _pcr_from_dataframe(result_df, underlying),
      "levels": levels,
      "source": source,
      "expiry": json_obj["records"].get("expiryDate"),
      "source_timestamp": json_obj["records"].get("timestamp"),
      "updated_at": datetime.now(timezone.utc).isoformat(),
  }


def optionchain(index):
  return snapshot(index)["table_html"]


def _normalise_option(option, fallback_strike):
  return {
      "strikePrice": option.get("strikePrice", fallback_strike),
      "pChange": option.get("pChange", option.get("PChange", 0)),
      "lastPrice": option.get("lastPrice", 0),
      "totalTradedVolume": option.get("totalTradedVolume", 0),
      "changeinOpenInterest": option.get("changeinOpenInterest", 0),
      "openInterest": option.get("openInterest", 0),
  }


def _option_rows(json_obj, side):
  rows = []
  for row in json_obj.get("records", {}).get("data", []):
    option = row.get(side)
    if option:
      rows.append(_normalise_option(option, row.get("strikePrice", 0)))
  return rows


def raw_to_dataframe(json_obj):
  put_rows = _option_rows(json_obj, "PE")
  call_rows = _option_rows(json_obj, "CE")
  if not put_rows or not call_rows:
    raise DataProviderError("The provider returned an incomplete option-chain response.")

  po = pd.DataFrame.from_records(put_rows)[[
      "strikePrice", "pChange", "lastPrice", "totalTradedVolume",
      "changeinOpenInterest", "openInterest"
  ]]
  po.columns = ["STRIKE", "%", "LTP", "VOLUME", "CHNG_OI",
                "openInterest"]
  co = pd.DataFrame.from_records(call_rows)[[
      "openInterest", "changeinOpenInterest", "totalTradedVolume",
      "lastPrice", "pChange", "strikePrice"
  ]]
  co.columns = ["openInterest", "CHNG_OI", "VOLUME", "LTP", "%",
                "STRIKE"]

  result_df = pd.merge(
      co,
      po,
      how="outer",
      on="STRIKE",
      suffixes=("_c", "_p"),
  ).sort_values("STRIKE").reset_index(drop=True)

  numeric_columns = [
      "openInterest_c", "CHNG_OI_c", "VOLUME_c", "LTP_c", "%_c",
      "STRIKE", "%_p", "LTP_p", "VOLUME_p", "CHNG_OI_p",
      "openInterest_p",
  ]
  for column in numeric_columns:
    result_df[column] = pd.to_numeric(result_df[column], errors="coerce").fillna(0)

  result_df.insert(5, "Br-Ev_c",
                   (result_df["LTP_c"] + result_df["STRIKE"]).tolist())
  result_df.insert(6, "Br-Ev_p",
                   (result_df["STRIKE"] - result_df["LTP_p"]).tolist())
  result_df[["LTP_c", "LTP_p"]] = result_df[["LTP_c", "LTP_p"]].round(1)
  result_df[["%_c", "Br-Ev_c", "%_p", "Br-Ev_p"]] = result_df[[
      "%_c", "Br-Ev_c", "%_p", "Br-Ev_p"
  ]].round(0)
  result_df["Br-Ev_p"] = result_df["Br-Ev_p"].astype(int)
  result_df["Br-Ev_c"] = result_df["Br-Ev_c"].astype(int)
  return result_df.fillna(0)


def pcr(index):
  json_obj, _ = _provider_option_chain_json(index)
  underlying = _underlying_value(json_obj, index)
  return _pcr_from_dataframe(raw_to_dataframe(json_obj), underlying)
