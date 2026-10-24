# FC-ACC-009: inactive dashboard champion decision export

This is a research-only, default-off record of the **uncapped dashboard**
decision boundary. Set `FC_ACC_009_EXPORT_DIR` to a separate local research
directory only in an explicitly authorized diagnostic. No workflow sets it.
Nothing from this directory is uploaded to Pages. Do not enable it in a live
workflow or call its output prospective evidence without separate review and
authorization.

`dashboard/prepare_pages_artifact.prepare` already builds the exact
pre-withholding and final publication manifests. When enabled, it copies the
normalized dashboard payload and live overlay before deployment-safety
withholding. After writing the three staged customer files, it records those
copies, the validated registry, both manifests' binding, and their hashes in
one write-once file named for the final manifest artifact ID. A failed export
warns but leaves the staged files and return value unchanged.

`dashboard/champion_decision_export.verify_export` independently rebuilds
both publication decisions from the saved pre-withholding state and supplied
final staged files/manifest. It checks the canonical identities, manifest
candidate list, row status, price, line and run/version metadata through exact
replay. `new_publication_policy_N` is the **new, publishable dashboard-policy
candidate count before any research offer exclusion**. The broader uncapped
classifier count is separately preserved as `dashboard_top_picks`. Already
public registry IDs are separate. A prepared candidate is only an intended
exposure: the deployment-proven registry remains the authority on what was
actually public.

The source dashboard may contain prices updated by its live overlay, and
those are saved as observed, without fabricating an earlier quote. The export
does **not** archive raw FanDuel responses, a per-offer observation clock,
authenticated MLB schedule/lineup bytes, or customer execution receipts.
The timestamp and `source_commit` are inherited from the product and Pages
preparation; they are not independently authenticated by this module. The
record has an internal SHA-256 and exclusive/atomic local creation, but no
external time anchor or durable remote object store. It cannot by itself
certify a 2027 prospective unit.

Every export's coverage state is `UNVERIFIED` because today's dashboard
payload is not an authenticated full-slate discovery record. The pure
`coverage_status` contract distinguishes MISSING, PARTIAL, COMPLETE and
ZERO_PICK when a **separately authenticated expected-game set** and complete
discovery are supplied. It never infers zero opportunities from an empty
dashboard. Those inputs and the DAY/NIGHT cutoff commitments remain work for
the inactive PR #226 integration and future, separately authorized capture.

The static `generate_picks.select_main_board` caps its output at ten and
filters on `price_clears`. The dashboard computes uncapped per-family
recommendation statuses; `dashboard/build_dashboard.py` explicitly labels
its `rank` display-only. This export binds to `build_publication_manifest`,
not `board_freeze` or static top-10 identity. A 12-Top-Pick adversarial test
replays all 12, proving the ten-row static board cannot silently stand in for
the public-policy champion. Historical freezes remain observational only.

The proposed 2027 matched-volume comparison in Issue #91 comment 6235593925
must still solve independent source capture, complete game discovery, early
DAY/NIGHT commitment, durable write-once storage, independent anchoring,
actual deployment exposure reconciliation, and reproducible settlement.
