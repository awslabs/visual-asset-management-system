"""Industry-specific commands for VamsCLI."""

from .industry import industry
from .engineering import engineering

# Register engineering under industry
industry.add_command(engineering)

__all__ = ['industry']
