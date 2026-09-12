"""Replayable normalization of archived NFL source artifacts.

Raw capture remains byte-exact under nfl/archive. Modules here may interpret
those preserved bytes, but they must not rewrite the raw archive.
"""
