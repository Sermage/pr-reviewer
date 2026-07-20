"""AI PR Reviewer — AI code-review webhook service."""

# Single source of truth for the version. pyproject reads it (dynamic version)
# and FastAPI/CLI surface it. Bump on every release-worthy change.
__version__ = "0.2.0"
