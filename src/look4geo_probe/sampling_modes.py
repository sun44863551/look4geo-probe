"""UI sampling policy shared by browser drivers: ordinary chat/search only."""

ACTIVE_MODE_SELECTOR = ','.join((
    'button[aria-pressed="true"]', '[role="tab"][aria-selected="true"]',
    '[role="menuitemradio"][aria-checked="true"]', '[role="option"][aria-selected="true"]',
    'button[data-active="true"]', 'button[data-state="active"]',
))
FORBIDDEN_MODE_LABELS = frozenset({
    'work', 'work mode', 'computer', 'computer mode', '工作', '工作模式',
    '电脑', '电脑模式', '计算机', '计算机模式',
})


class SamplingModeError(RuntimeError):
    """Do not retry or send when an allowed UI mode cannot be confirmed."""


def is_forbidden_mode_label(value: str) -> bool:
    return ' '.join(value.casefold().split()) in FORBIDDEN_MODE_LABELS
