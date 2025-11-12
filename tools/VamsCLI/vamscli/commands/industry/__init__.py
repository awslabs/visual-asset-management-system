"""Industry-specific commands for VamsCLI."""

from .industry import industry
from .engineering import engineering
from .spatial import spatial

# Register engineering under industry
industry.add_command(engineering)

# Register spatial command group
industry.add_command(spatial)

__all__ = ['industry']
