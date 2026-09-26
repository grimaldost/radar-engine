"""stack-radar: a tech radar that governs a Claude Code stack, and the gate behind it.

The engine only. The catalogue it reads - entries, evidence, feedback, profiles - lives in
a separate directory called a DATA ROOT, found by the `radar.toml` marker at its top. One
engine serves any number of data roots and knows none of them by name.
"""

from __future__ import annotations

# THE THIRD VERSION SITE. `pyproject.toml [project].version` and CHANGELOG.md's
# newest released heading are the other two; `radar_lib.version_site_errors` compares all
# three and `tests/test_version_sites.py` fails when they disagree. It exists here, rather
# than being derived from the installed metadata, because the engine is now DISTRIBUTED:
# `importlib.metadata` answers for whatever copy is installed, which is exactly the wrong
# question when a checkout is being judged, and answers nothing at all for the corporate
# fallback that runs the package straight off a PYTHONPATH without installing it.
__version__ = "0.2.0"
