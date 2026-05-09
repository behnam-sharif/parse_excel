"""F.1 — ``xl_ontology.pass1_tag_agents`` is the new canonical name for
the Pass-1 tag agents package.  The physical directory still lives at
``xl_ontology/agents/`` during the F.1 → F.6 transition; this package
re-exports every submodule of ``xl_ontology.agents`` under the new name
via ``sys.modules`` aliasing.

New code should import from ``xl_ontology.pass1_tag_agents.X``.  Existing
imports through ``xl_ontology.agents.X`` keep working unchanged.

The physical directory rename (``agents/`` → ``pass1_tag_agents/``) is
deferred to a later phase once all consumers (Pass-1 agents internally,
Pass-2 stages, runner, pack_loader, tests) have migrated to the new
import path.  At that point the legacy ``agents/`` shim will be removed.
"""

from __future__ import annotations

import importlib
import sys

# Trigger the agents package to load and register all its submodules.
# Then alias every submodule under our new package path.
_LEGACY = importlib.import_module("xl_ontology.agents")

# Re-export the package's public symbols at our top level.
for _name in getattr(_LEGACY, "__all__", []):
    globals()[_name] = getattr(_LEGACY, _name)

# Submodules to alias.  When users do
# ``from xl_ontology.pass1_tag_agents.runner import OntologyAgent``,
# Python looks up ``xl_ontology.pass1_tag_agents.runner`` in sys.modules.
# We point that key at the already-loaded ``xl_ontology.agents.runner``
# module so the import returns the same object.
_SUBMODULES = (
    "base",
    "prompts",
    "planner_parse",
    "planner_execute",
    "runner",
    "inputs",
    "inputs_helpers",
    "calculations",
    "results",
    "overall_model",
    "heuristic_handlers",
)

for _sub in _SUBMODULES:
    _legacy_mod = importlib.import_module(f"xl_ontology.agents.{_sub}")
    sys.modules[f"{__name__}.{_sub}"] = _legacy_mod
    globals()[_sub] = _legacy_mod

del _name, _sub, _legacy_mod, _LEGACY
