"""Business orchestration on top of `models/` + `infra/`.

Routers (`routers/*.py`) are kept thin; anything that touches more than
one ORM row or coordinates with `infra/` belongs here.
"""
