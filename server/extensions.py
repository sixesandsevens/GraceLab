import sqlite3

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()

login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    """
    WAL lets stations keep reading (config, session status, auth lookups)
    while a slow write is in progress, instead of failing with "database is
    locked". synchronous=NORMAL is safe with WAL (no corruption on power
    loss, at worst the last few commits are lost) and cuts fsyncs, which is
    what stalls on the SD card.
    """
    if not isinstance(dbapi_conn, sqlite3.Connection):
        return
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()
