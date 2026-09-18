"""Review severity levels and categories."""

from enum import StrEnum


class Severity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class Category(StrEnum):
    BUG = "bug"
    PERFORMANCE = "performance"
    SECURITY = "security"
    STYLE = "style"
    COMPLEXITY = "complexity"


SEVERITY_VALUES = {s.value for s in Severity}
CATEGORY_VALUES = {c.value for c in Category}
