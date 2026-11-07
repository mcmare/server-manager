"""Dependency-free color terminal presentation; safe fallback for pipes and NO_COLOR."""
import os
import sys

COLORS = {'green': '32', 'red': '31', 'yellow': '33', 'cyan': '36', 'blue': '34', 'bold': '1', 'dim': '2'}

def paint(text, color, *, color_enabled=None, **kwargs):
    if 'color' in kwargs:  # compatibility for simple callers
        color_enabled = kwargs['color']
    if color_enabled is None:
        color_enabled = sys.stdout.isatty() and 'NO_COLOR' not in os.environ and os.environ.get('TERM') != 'dumb'
    if not color_enabled or color not in COLORS:
        return str(text)
    return f'\033[{COLORS[color]}m{text}\033[0m'

def site_status(enabled):
    return paint('● включён' if enabled else '○ отключён', 'green' if enabled else 'yellow')

def heading(title):
    print('\n' + paint('━' * 54, 'blue'))
    print('  ' + paint(title, 'bold'))
    print(paint('━' * 54, 'blue'))

def note(message, level='cyan'):
    print(paint(message, level))
