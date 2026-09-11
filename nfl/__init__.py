"""NFL side of Full Count.

Sport-specific. Nothing under `nfl/` may be imported by root-level MLB
production modules -- the dependency direction is NFL -> verified
sport-neutral infrastructure, never MLB -> NFL. nfl/tests/test_import_direction.py
enforces that.

NFL-01 scope: raw prospective world-state archival and research artifacts.
There is deliberately no NFL scorer, no NFL probability, and no NFL pick.
"""
