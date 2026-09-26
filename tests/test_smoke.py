"""スモークテスト: 最小プロジェクトが読み込めること."""

import importlib


def test_src_package_importable():
    assert importlib.import_module("src") is not None


def test_app_module_importable():
    app = importlib.import_module("app")
    assert callable(app.main)
    assert app.APP_TITLE
