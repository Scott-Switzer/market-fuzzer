"""Compatibility entry point for `uvicorn app.main:app`."""

from app.api.app import app

__all__ = ["app"]
