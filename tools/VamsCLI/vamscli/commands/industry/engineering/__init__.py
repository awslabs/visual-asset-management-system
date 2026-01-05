"""Engineering-specific commands for VamsCLI."""

from .engineering import engineering
from .plm import plm
from .bom import bom

# Register plm under engineering
engineering.add_command(plm)

# Register bom under engineering
engineering.add_command(bom)

__all__ = ['engineering']
