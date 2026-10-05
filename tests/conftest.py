import os
import tempfile

import pytest

# 必须在导入 app.main 之前指定临时数据库
_TMP = tempfile.mkdtemp(prefix="drill-test-")
os.environ["DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["APP_PORT"] = "8080"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c
