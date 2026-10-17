"""Telegram safe HTML formatting utilities."""

import html


def escape_html(text: str) -> str:
    """Escapes HTML special characters for Telegram HTML mode."""
    return html.escape(str(text), quote=True)


def bold(text: str) -> str:
    """Formats text as bold HTML."""
    return f"<b>{escape_html(text)}</b>"


def italic(text: str) -> str:
    """Formats text as italic HTML."""
    return f"<i>{escape_html(text)}</i>"


def code(text: str) -> str:
    """Formats text as inline code HTML."""
    return f"<code>{escape_html(text)}</code>"


def pre(text: str, language: str = "") -> str:
    """Formats text as a preformatted code block with optional language."""
    escaped = escape_html(text)
    if language:
        return f'<pre><code class="language-{escape_html(language)}">{escaped}</code></pre>'
    return f"<pre>{escaped}</pre>"


def link(text: str, url: str) -> str:
    """Formats text as a hyperlink."""
    return f'<a href="{escape_html(url)}">{escape_html(text)}</a>'


def spoiler(text: str) -> str:
    """Formats text as a spoiler."""
    return f'<span class="tg-spoiler">{escape_html(text)}</span>'
