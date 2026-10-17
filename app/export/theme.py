"""Use the supplied Tailwind theme tokens in standalone, browser-native HTML."""
from pathlib import Path
import re

THEME_PATH=Path(__file__).resolve().parents[2]/'theme.css'


def browser_theme():
    source=THEME_PATH.read_text()
    # @theme/@source/@apply are build directives, not browser CSS. This theme
    # contains custom properties and two cursor utilities, so no CDN is needed.
    source=re.sub(r'@source\s+inline\([^;]*\);','',source)
    source=re.sub(r'@theme(?:\s+static)?\s*\{',':root {',source)
    source=source.replace('@apply cursor-pointer;','cursor: pointer;')
    source=source.replace('@apply cursor-not-allowed;','cursor: not-allowed;')
    if re.search(r'@(?:theme|source|apply)\b',source):raise ValueError('THEME_BUILD_DIRECTIVE_UNSUPPORTED')
    return source
