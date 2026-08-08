"""Entry point so `python -m backend.migrations` invokes the CLI."""
from .cli import main
import sys

sys.exit(main())
