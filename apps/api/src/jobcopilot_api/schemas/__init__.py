"""Pydantic schemas (request / response models).

Mirrors `models/` for ORM, but at the API boundary. Keep enums and
strict validation here so DB columns can stay flexible (VARCHAR) while
the wire contract stays narrow.
"""
