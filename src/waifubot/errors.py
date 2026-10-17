"""Normalized error taxonomy: every failure gets exactly one category (SPEC §4)."""
from __future__ import annotations


class WaifuBotError(Exception):
    """Base class; `code` is the stable, loggable category."""

    code = "error"


class ConfigError(WaifuBotError):
    code = "config_error"


class LocaleError(WaifuBotError):
    code = "locale_error"


class DatabaseError(WaifuBotError):
    code = "database_error"


class ValidationError(WaifuBotError):
    code = "validation_error"


class StorageError(WaifuBotError):
    code = "storage_error"


class AiError(WaifuBotError):
    code = "ai_error"


class ProviderError(WaifuBotError):
    code = "provider_error"


class BlockedByProvider(ProviderError):
    """CAPTCHA / challenge / rate-limit / access-denied page.

    Permanent: never retried (SPEC §10). Stop, record, notify admin.
    """

    code = "blocked_by_provider"
