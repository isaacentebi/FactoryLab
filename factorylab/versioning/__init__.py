"""Behavioural versioning: live while a world runs, and forensic over a dead world's diary.

Owns the window discretisation, the transition operator and its spectral-gap
bound, the live version tracker (``live``), the pathology predicates
(``versions.diagnose``) and the report. The runtime's immune organ runs ``live``
and ``diagnose`` at every closed window (essay II.II: "Versioning the superdark
factory is an active process"); ``factorylab versions`` replays the same code
over a diary (``report``). Nothing here writes: the organ ledgers what it reads,
and every threshold comes from the genesis manifest, never a default of its own.

Imports ``kernel`` and ``charter``.
"""

from factorylab.versioning.report import render, summary

__all__ = ("summary", "render")
