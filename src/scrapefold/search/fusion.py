"""Compatibility imports for Enrichfold result fusion."""

from enrichfold.search import fusion as _fusion

fuse = _fusion.fuse
reciprocal_rank_fusion = _fusion.reciprocal_rank_fusion
_normalize_url = _fusion._normalize_url

__all__ = ["fuse", "reciprocal_rank_fusion"]
