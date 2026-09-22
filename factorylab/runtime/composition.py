"""Composition through contracts (essay II.I, II.I.b; Chapter II rulings §2, R11, W4).

Seats compose one another's work only through published contracts: an assembly's
self-description, a tool's promised return, and a request addressed to a *kind* of
work rather than to a peer's id. What composition earns flows back through the one
reward channel there is. Nothing here is a channel between seats.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from factorylab.cortex.registration import AssemblyProposal, ToolProposal
from factorylab.cortex.tools import as_spec


class CompositionMixin:
    """Contracts that carry their own description, and the composition built on them."""

    def _register(self, handle: str, prop: Any, *, predicted_effect: Any = None) -> None:
        """Register as governance does, then publish the contract's own promises.

        Guarantees an admitted assembly carries the description its proposal gave
        (primitive audit F6) and an admitted tool the ``returns_schema`` it promised
        (F9), both on the objects the catalogue publishes and the checkpoint keeps.
        A proposal governance refused raises before either is touched.
        """
        super()._register(handle, prop, predicted_effect=predicted_effect)
        if isinstance(prop, AssemblyProposal) and prop.description:
            assembly = self.assemblies[prop.id]
            assembly.spec = replace(assembly.spec, description=prop.description)
        elif isinstance(prop, ToolProposal) and prop.returns_schema is not None:
            tool = replace(self.population_tools[prop.id],
                           returns_schema=dict(prop.returns_schema))
            self.population_tools[prop.id] = tool
            self.tool_specs[prop.id] = as_spec(tool, self.m.tools.population_tool_micro_per_call)
