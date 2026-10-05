# Environment-B runs (GitHub Actions, `.github/workflows/v3-a1-drill-replay.yml`)

Each run is a fresh GitHub-hosted VM with a shallow checkout, a fresh venv from the lock, and a read-only token. Results are in the run's job summary, in check-run annotations (`A1-B …`), and in the artifact `a1-drill-replay-b-<sha>` (retained 90 days).

| run | commit | result |
|---|---|---|
| [37352202211](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/37352202211) | `fc3a59d52f` | job success: tests + replay step green. The result JSON was not machine-readable from the builder's API client (blob-hosted logs), so annotations were added. |
