"""Read-only analysis of a dead world's diary: versions, pathologies, early warnings.

Owns the window discretisation, the transition operator and its spectral-gap
bound, the version spans, the pathology predicates and the report itself.
Nothing here runs while a world is alive and nothing here can write; every
threshold comes from the genesis manifest the diary carries, never from a
default of its own.

Imports ``kernel`` and ``charter``.
"""

from factorylab.versioning.report import render, summary

__all__ = ("summary", "render")
