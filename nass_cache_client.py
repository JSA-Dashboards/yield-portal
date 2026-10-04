"""
nass_cache_client.py -- read-only USDA NASS QuickStats cache client.

This dashboard does not call the NASS API directly. A separate scheduled job
(the usda-nass-etl repo) holds NASS_API_KEY, pulls on a daily schedule, and
writes results into a shared cache. This file only reads that cache -- it
never holds the NASS key and never calls NASS live.

That split exists because NASS registers API keys per individual, not for
public/shared use -- every visitor to a live client-facing dashboard hitting
NASS under one key is exactly what that restriction is meant to prevent.

Vendored identically across crop-conditions-dashboard, beef-weight-dashboard,
livestock-inventory-dashboard, and domestic-production-dashboard (see the
usda-nass-etl repo). Keep this file byte-for-byte the same across all four --
app-specific shaping belongs in each app's own fetch wrapper functions, not here.

Backends (priority order):
  Snowflake  -- USE_SNOWFLAKE=1 + SNOWFLAKE_ACCOUNT/USER/PASSWORD (+ optional
                ROLE, WAREHOUSE, DATABASE, SCHEMA). Target: JSA.NASS_CACHE.
  PostgreSQL -- DATABASE_URL (Supabase, legacy).
"""
import hashlib
import json
import os

try:
    import streamlit as st
except ImportError:
    st = None

_IGNORED_KEYS = {"key", "format"}


def _secret(key: str, default: str = "") -> str:
    """Read from st.secrets first, fall back to os.environ."""
    if st is not None:
        try:
            v = st.secrets.get(key, "")
            if v:
                return str(v).strip()
        except Exception:
            pass
    return os.environ.get(key, default).strip()


def _use_sf() -> bool:
    return _secret("USE_SNOWFLAKE").lower() in ("1", "true", "yes", "on")


def _database_url() -> str:
    return _secret("DATABASE_URL")


def _cache_key(endpoint: str, params: dict) -> str:
    # Every value is cast to str before hashing so this matches
    # nass_etl.cache_key.make_cache_key exactly regardless of whether a
    # dashboard's own params dict uses int or str (e.g. year=2024 vs
    # year="2024") -- see that module's docstring for why.
    clean = {k: str(v) for k, v in params.items() if k not in _IGNORED_KEYS}
    canon = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{endpoint}|{canon}".encode()).hexdigest()


def _pg_dsn(url: str) -> str:
    if "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def _load_private_key():
    """RSA private key (DER bytes) for key-pair auth; None if not configured (then
    password is used). The account enforces MFA on password sign-ins, so Streamlit
    Cloud must use the key — PEM text in the SNOWFLAKE_PRIVATE_KEY secret."""
    pem = _secret("SNOWFLAKE_PRIVATE_KEY")
    path = _secret("SNOWFLAKE_PRIVATE_KEY_PATH")
    if not pem and not path:
        return None
    from cryptography.hazmat.primitives import serialization
    data = open(path, "rb").read() if path else pem.replace("\\n", "\n").encode()
    pwd = _secret("SNOWFLAKE_PRIVATE_KEY_PWD") or None
    key = serialization.load_pem_private_key(data, password=pwd.encode() if pwd else None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption())


def _sf_connect():
    import snowflake.connector
    kw = dict(
        account=_secret("SNOWFLAKE_ACCOUNT"),
        user=_secret("SNOWFLAKE_USER"),
        role=_secret("SNOWFLAKE_ROLE") or None,
        warehouse=_secret("SNOWFLAKE_WAREHOUSE") or None,
        database=_secret("SNOWFLAKE_DATABASE") or "JSA",
        schema=_secret("SNOWFLAKE_SCHEMA") or "NASS_CACHE",
        login_timeout=30,
    )
    pkey = _load_private_key()
    if pkey is not None:
        kw["private_key"] = pkey
    else:
        kw["password"] = _secret("SNOWFLAKE_PASSWORD")
    return snowflake.connector.connect(**kw)


def fetch_cached(params: dict, endpoint: str = "api_GET") -> dict:
    """
    Read-only cache lookup. Returns the raw NASS response shape, e.g.
    {"data": [...]}, or {"data": []} if nothing has been cached yet for this
    exact query -- add the param combo to the matching jobs/*.py list in
    usda-nass-etl and re-run pull_all.py.

    Raises only on a genuine backend connection failure so a misconfigured
    secret is loud, not a silent blank dashboard.
    """
    key = _cache_key(endpoint, params)

    if _use_sf():
        conn = _sf_connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT data FROM nass_cache WHERE cache_key = %s", (key,))
            row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return {"data": []}
        data = row[0]
        # Snowflake VARIANT columns are returned as Python dicts by the connector
        return data if isinstance(data, (dict, list)) else json.loads(data)

    url = _database_url()
    if not url:
        raise RuntimeError(
            "No NASS cache backend configured. Set USE_SNOWFLAKE=1 (+ SNOWFLAKE_*) "
            "or DATABASE_URL in `.streamlit/secrets.toml` / environment -- "
            "this dashboard reads NASS data from a shared cache (see usda-nass-etl), "
            "not the live API."
        )
    import psycopg2
    conn = psycopg2.connect(_pg_dsn(url))
    try:
        cur = conn.cursor()
        cur.execute("SELECT data FROM nass_cache WHERE cache_key = %s", (key,))
        row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return {"data": []}
    data = row[0]
    return data if isinstance(data, dict) else json.loads(data)


def cache_freshness() -> str:
    """Most recent fetched_at timestamp across the whole cache, or None.
    Handy for an 'as of ...' footer caption."""
    try:
        if _use_sf():
            conn = _sf_connect()
            try:
                cur = conn.cursor()
                cur.execute("SELECT MAX(fetched_at) FROM nass_cache")
                row = cur.fetchone()
                return str(row[0]) if row and row[0] else None
            finally:
                conn.close()

        url = _database_url()
        if not url:
            return None
        import psycopg2
        conn = psycopg2.connect(_pg_dsn(url))
        try:
            cur = conn.cursor()
            cur.execute("SELECT MAX(fetched_at) FROM nass_cache")
            row = cur.fetchone()
            return str(row[0]) if row and row[0] else None
        finally:
            conn.close()
    except Exception:
        return None
