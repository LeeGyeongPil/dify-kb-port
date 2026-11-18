"""dify-kb-port: move a Dify knowledge base chunk-for-chunk through the public Knowledge API."""

__version__ = "0.1.0"
FORMAT = "dify-kb-port/1"
# Separators are tried in order; the first one that never appears in any chunk is used.
# They avoid "\n" and backslashes because the server only interprets the two-character "\\n" escape.
SEPARATOR_CANDIDATES = (
    "␞DIFYKBPORT␞",
    "␟DIFYKBPORT␟",
    "§§DIFYKBPORT§§",
)

__all__ = ["__version__", "FORMAT", "SEPARATOR_CANDIDATES"]
