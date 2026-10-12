import os
import sqlite3
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def test_migrations_apply_and_match_models(tmp_path):
    db_file = tmp_path / "mig.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file.as_posix()}"}
    up = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env,
                        capture_output=True, text=True)
    assert up.returncode == 0, up.stderr
    tables = {r[0] for r in sqlite3.connect(db_file).execute("select name from sqlite_master where type='table'")}
    for expected in ["users", "user_sessions", "user_preferences", "saved_articles", "news_articles",
                     "notification_delivery_logs", "briefing_runs"]:
        assert expected in tables
    chk = subprocess.run([sys.executable, "-m", "alembic", "check"], cwd=ROOT, env=env,
                         capture_output=True, text=True)
    assert chk.returncode == 0, chk.stdout + chk.stderr
