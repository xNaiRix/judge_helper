"""Точка входа для uvicorn: `uvicorn judge_helper.asgi:app --reload`."""

from .main import create_app

app = create_app()
