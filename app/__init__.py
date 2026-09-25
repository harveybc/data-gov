"""Compatibility shim for the former top-level package name.

data-gov's application package was renamed `app` -> `data_gov` on 2026-09-25.
The rename is the fix for a real defect: several sibling repositories of the
programme (heuristic-strategy, predictor, agent-multi, ...) also ship a
top-level package called `app`.  When any of them is installed in the same
interpreter, its `app/` wins on `sys.path` and the `data-gov` console script
resolves into the *other* project's `app.main`, which fails at import.  A
package name nothing else claims cannot be shadowed.

This shim is deliberately **not installed** (`setup.py` excludes it from
`find_packages`), so it can never re-create the collision in `site-packages`.
It exists only for callers that run from the checkout with the checkout on
`PYTHONPATH`, the documented invocation of the other repositories:

    PYTHONPATH=<data-gov checkout> python -m app.main --help

New code must import `data_gov` directly.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_CHECKOUT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _CHECKOUT not in _sys.path:
    _sys.path.insert(0, _CHECKOUT)

import data_gov as _data_gov  # noqa: E402

# Submodules resolve out of `data_gov/`: `app.client` is `data_gov/client.py`.
__path__ = list(_data_gov.__path__)
__doc_renamed_to__ = "data_gov"
