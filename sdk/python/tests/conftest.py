import pytest
from fake import FakeApi


@pytest.fixture
def fake():
    api = FakeApi()
    yield api
    api.close()
