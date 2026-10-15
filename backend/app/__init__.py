"""Backend application package.

The FastAPI application is exposed by :mod:`backend.app.main`.  Keep package
initialization side-effect free so importing configuration or service modules
does not construct the application and validate environment secrets.
"""

__all__ = ["app", "create_app"]


def __getattr__(name: str):
    """Load the application exports only when callers explicitly request them."""
    if name in __all__:
        from backend.app.main import app, create_app

        return {"app": app, "create_app": create_app}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
