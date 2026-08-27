# Locked disagreement experiment -- file hashes at canonical-rebuild-mission start

Recorded 2026-08-27, before any canonical-rebuild-and-accuracy-foundation
work touched this repository, per Phase 6 of the governing mission spec.
These files are NOT to be modified, tuned, or adjusted after seeing
results. Re-run `sha256sum` on all three before executing the locked
experiment (Phase 20) and confirm an exact match against the values below.

```
55fc0e0e7bfcb323d4ade4767a05b16f853feff7c7799f8c267074351738101f  backtest/disagreement_experiment_protocol.md
8b47c221c9c5cf22d790277263a7dc33109d6d7aedaa8ed349ec17dee1ebab07  backtest/disagreement_experiment_runner.py
33fef3c1aece84c85fb808c30d03cddcd5a513c7f68c4ec87ea20570315e9411  backtest/disagreement_challenger_model.py
```

Verify with:

```
sha256sum -c - <<'EOF'
55fc0e0e7bfcb323d4ade4767a05b16f853feff7c7799f8c267074351738101f  backtest/disagreement_experiment_protocol.md
8b47c221c9c5cf22d790277263a7dc33109d6d7aedaa8ed349ec17dee1ebab07  backtest/disagreement_experiment_runner.py
33fef3c1aece84c85fb808c30d03cddcd5a513c7f68c4ec87ea20570315e9411  backtest/disagreement_challenger_model.py
EOF
```
