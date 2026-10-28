"""ASGI 入口:`uv run python -m uvicorn app.asgi:app`。

工厂留在 :mod:`app.main`,此模块只做装配,避免测试导入工厂时产生副作用。
"""

from app.main import create_app

app = create_app()
