"""Coordinator orchestration facade for Jackery SolarVault.

The Home Assistant lifecycle-facing coordinator remains import-stable here while
protocol handling, statistics, and setter logic live in domain modules.  The
current extraction keeps the already-characterized implementation class intact
in ``_coordinator_legacy`` and exposes thin facades from the target packages so
subsequent PRs can move method bodies without changing entity contracts.
"""

from ._coordinator_legacy import JackerySolarVaultCoordinator, RejectionMetrics

__all__ = ["JackerySolarVaultCoordinator", "RejectionMetrics"]
