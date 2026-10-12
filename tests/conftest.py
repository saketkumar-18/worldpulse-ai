import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.environ.setdefault("WORLDPULSE_IMPORT_APP", "0")


@pytest.fixture()
def session(tmp_path):
    from app import db
    from app.models import NewsCategory  # noqa: F401
    from app.normalize import CATEGORIES
    engine = db.configure(f"sqlite:///{tmp_path / 'test.db'}")
    s = db.session_factory()()
    for i, (slug, label) in enumerate(CATEGORIES):
        from app.models import NewsCategory as NC
        s.merge(NC(slug=slug, label=label, sort_order=i))
    s.commit()
    yield s
    s.close()
    engine.dispose()
