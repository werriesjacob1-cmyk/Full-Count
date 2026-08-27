#!/usr/bin/env python3
"""Interruption-recovery proof for canonical durability.

This is the test that had to exist before restarting the multi-hour backfill.
It reproduces the exact failure of 2026-08-27 -- a container reclaimed
mid-run, taking the entire filesystem including .git with it -- and proves the
run can be resumed from the remote alone.

The simulated container death is total and deliberate: the run directory AND
the whole local repository are deleted, then a FRESH CLONE is made from the
remote. Nothing local survives to rescue the run. That is the only interruption
model worth testing, because it is the one that actually happened.

Rows here are synthetic. What is under test is the durability and recovery
machinery -- push, checksum, identity, restore, resume -- not the scoring
engine, which has its own suite. Using synthetic rows keeps this deterministic
and offline; a network-dependent test could not be trusted as a gate.
"""

import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest.canonical_durability as cd
import backtest.canonical_run as cr

PASS = FAIL = 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ok   {msg}")


def bad(msg):
    global FAIL
    FAIL += 1
    print(f"  FAIL {msg}")


def check(msg, got, want):
    (ok if got == want else bad)(msg if got == want else f"{msg} (want {want!r}, got {got!r})")


def git(args, cwd, env=None, check_rc=True):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       env=env, timeout=120)
    if check_rc and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {p.stderr[:300]}")
    return p.stdout.strip()


# Set the identity into os.environ, not merely into a dict handed to this
# file's own git helper. push_durable_checkpoint() spawns git from os.environ,
# so an identity that lives only in a local dict never reaches it: commit-tree
# then fails for want of a committer, the push never happens, and the test
# fails with the confusing downstream symptom "Not a valid object name
# canonical-durable-checkpoints".
#
# This passed locally and failed in CI for ten minutes of confusion, because
# this container has a global git identity (Claude <noreply@anthropic.com>)
# that silently supplied the missing value, while a GitHub runner has none. A
# test whose result depends on ambient machine configuration is not a test.
for _k, _v in (("GIT_AUTHOR_NAME", "fc-test"), ("GIT_AUTHOR_EMAIL", "fc-test@example.invalid"),
               ("GIT_COMMITTER_NAME", "fc-test"), ("GIT_COMMITTER_EMAIL", "fc-test@example.invalid")):
    os.environ[_k] = _v

GIT_ENV = dict(os.environ)


def synth_rows(date, n):
    """Deterministic synthetic rows, distinguishable per date so a duplicate or
    a cross-date mixup is detectable rather than merely improbable."""
    return [{"date": date, "game_pk": 700000 + i, "player_id": 500000 + i,
             "prop_type": "hits", "line": 0.5, "prob": round(0.5 + i / 1000, 4)}
            for i in range(n)]


def make_manifest(run_id, start, end, sha="a" * 40, **over):
    m = {"run_id": run_id, "schema_version": 1, "sport": "mlb",
         "evidence_regime": "canonical_historical_model_data",
         "requested_start_date": start, "requested_end_date": end,
         "command": "test", "weather_mode": "no_weather", "config": {},
         "code_git_sha": sha,
         "repository_identity": "werriesjacob1-cmyk/Full-Count",
         "model_artifact_versions": {"model_version": "m1", "selection_policy_version": "s1",
                                     "calibration_version": "c1", "feature_version": "f1"},
         "source_provider": "mlb_statsapi", "output_target": "x",
         "created_at": "2026-08-27T00:00:00+00:00",
         "candidate_identity_fields": list(cr.CANDIDATE_IDENTITY_FIELDS)}
    m.update(over)
    return m


def main():
    sandbox = tempfile.mkdtemp(prefix="fc-durability-test-")
    try:
        # ══ Setup: a bare "remote" and a working repo that plays the part of
        #    the doomed container.
        origin = os.path.join(sandbox, "origin.git")
        git(["init", "-q", "--bare", origin], cwd=sandbox, env=GIT_ENV)
        repo1 = os.path.join(sandbox, "container-1")
        git(["init", "-q", "-b", "main", repo1], cwd=sandbox, env=GIT_ENV)
        git(["remote", "add", "origin", origin], cwd=repo1, env=GIT_ENV)
        open(os.path.join(repo1, "README.md"), "w").write("x\n")
        git(["add", "-A"], cwd=repo1, env=GIT_ENV)
        git(["commit", "-q", "-m", "base"], cwd=repo1, env=GIT_ENV)
        git(["push", "-q", "origin", "main"], cwd=repo1, env=GIT_ENV)

        RUN = "canonical-TEST0001-abcd1234"
        START, END = "2024-04-01", "2024-04-10"
        ALL_DATES = cr.date_range(START, END)
        run_dir = os.path.join(repo1, "backtest", "canonical_runs", RUN)
        os.makedirs(os.path.join(run_dir, "checkpoints"), exist_ok=True)
        manifest = make_manifest(RUN, START, END)
        cr._atomic_write_json(cr.manifest_path(run_dir), manifest)

        # ══ 1. First run: complete 6 of 10 dates, then "die".
        print("== 1. first run produces checkpoints ==")
        done = ALL_DATES[:6]
        expected_sha = {}
        for i, d in enumerate(done):
            rows = [] if i == 3 else synth_rows(d, 20 + i)   # one legitimate no_games day
            status = "no_games" if i == 3 else "ok"
            meta = cr.write_checkpoint(run_dir, d, rows, status, source_code_git_sha="a" * 40)
            expected_sha[d] = meta["sha256"]
        state = cr.load_run_state(run_dir, ALL_DATES)
        check("6 dates resolved before interruption",
              sum(1 for s in state.values() if s["resolved"] in ("ok", "no_games")), 6)
        check("4 dates still remaining", len(cr.plan_remaining(state)), 4)

        # ══ 2. Durable push.
        print("== 2. durable checkpoint pushed to the remote ==")
        env_id = cd.environment_identity()
        lineage = [cd.source_lineage_record(
            "statcast", request_identity=f"{START}..{END}", library="pybaseball",
            library_version="2.2.7", row_count=1234, schema_columns=["game_date", "launch_speed"],
            content_sha256="deadbeef", date_coverage=f"{START}..{END}",
            cache_mode=cd.CACHE_MODE_FROZEN)]
        res = cd.push_durable_checkpoint(
            run_dir, manifest, environment=env_id, lineage=lineage,
            cache_mode=cd.CACHE_MODE_FROZEN, repo_root=repo1,
            state_summary={"ok": 5, "no_games": 1, "error": 0})
        check("push reported success", res["pushed"], True)
        check("6 dates written to the durable branch", res["dates_written"], 6)
        ok(f"gzipped row payload for 6 dates: {res['bytes_written']} bytes")
        remote_files = git(["ls-tree", "-r", "--name-only", cd.DURABLE_BRANCH],
                           cwd=origin, env=GIT_ENV).split("\n")
        check("index.json on remote", f"canonical/{RUN}/index.json" in remote_files, True)
        check("manifest.json on remote", f"canonical/{RUN}/manifest.json" in remote_files, True)
        check("6 gzipped row blobs on remote",
              sum(1 for f in remote_files if f.endswith(".jsonl.gz")), 6)

        # ══ 3. TOTAL CONTAINER LOSS. Not just the run dir -- the whole repo.
        print("== 3. simulated container reclamation (repo AND run dir destroyed) ==")
        shutil.rmtree(repo1)
        check("local repository is gone", os.path.exists(repo1), False)
        check("run directory is gone", os.path.exists(run_dir), False)

        # ══ 4. Fresh container: clone from the remote, nothing else.
        print("== 4. fresh container clones from the remote ==")
        repo2 = os.path.join(sandbox, "container-2")
        git(["clone", "-q", origin, repo2], cwd=sandbox, env=GIT_ENV)
        fetched = cd.fetch_durable_branch(repo_root=repo2)
        check("durable branch fetched into the fresh clone", fetched["ok"], True)
        found = cd.discover_durable_runs(repo_root=repo2)
        check("exactly one durable run discovered", len(found), 1)
        check("discovered the right run id", found[0]["run_id"] if found else None, RUN)
        check("index reports 6 dates", found[0]["dates"] if found else None, 6)

        # ══ 5. Restore. No manifest locally -- it must come from the remote.
        print("== 5. restore from remote alone ==")
        run_dir2 = os.path.join(repo2, "backtest", "canonical_runs", RUN)
        rep = cd.restore_from_durable(run_dir2, RUN, repo_root=repo2)
        check("manifest restored from remote", rep["manifest_restored"], True)
        check("6 dates restored", len(rep["restored"]), 6)
        check("no restore failures", rep["failed"], [])

        # ══ 6. Integrity: recomputed checksums equal the originals.
        print("== 6. restored bytes match the originals ==")
        mismatches = [d for d in done
                      if cd._sha256_file(cr.checkpoint_data_path(run_dir2, d)) != expected_sha[d]]
        check("every restored date's sha256 matches the pre-loss value", mismatches, [])
        for d in done:
            okk, _ = cr.validate_checkpoint(run_dir2, d)
            if not okk:
                bad(f"validate_checkpoint failed for {d}")
                break
        else:
            ok("canonical_run.validate_checkpoint accepts every restored checkpoint")

        # ══ 7. Resume: only the missing dates are planned.
        print("== 7. resume plans only the missing dates ==")
        state2 = cr.load_run_state(run_dir2, ALL_DATES)
        remaining = cr.plan_remaining(state2)
        check("4 dates remaining after recovery", len(remaining), 4)
        check("resume starts at the correct next date", remaining[0], ALL_DATES[6])
        check("completed dates are NOT replanned",
              [d for d in done if d in remaining], [])

        # ══ 8. No duplicates, no losses.
        print("== 8. row-level integrity ==")
        total = 0
        seen = set()
        dupes = []
        for d in done:
            p = cr.checkpoint_data_path(run_dir2, d)
            with open(p, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    total += 1
                    row = json.loads(line)
                    key = tuple(row.get(k) for k in cr.CANDIDATE_IDENTITY_FIELDS)
                    if key in seen:
                        dupes.append(key)
                    seen.add(key)
        expected_total = sum(len(synth_rows(d, 20 + i)) for i, d in enumerate(done) if i != 3)
        check("row count preserved exactly", total, expected_total)
        check("no duplicate candidate identities", dupes, [])

        # ══ 9. Run identity unchanged across the loss.
        print("== 9. identity survives the loss ==")
        m2 = cr.load_manifest(run_dir2)
        check("run_id identical", m2["run_id"], manifest["run_id"])
        check("code_git_sha identical", m2["code_git_sha"], manifest["code_git_sha"])
        idx = cd.load_durable_index(RUN, repo_root=repo2)
        check("identity fingerprint verifies", cd.assert_identity_compatible(idx, m2)["compatible"], True)
        check("environment recorded on the durable index",
              (idx.get("environment") or {}).get("environment_fingerprint"),
              env_id["environment_fingerprint"])
        check("source lineage recorded", len(idx.get("source_lineage") or []), 1)
        check("cache mode recorded", idx.get("cache_mode"), cd.CACHE_MODE_FROZEN)

        # ══ 10. Adversarial: every incompatibility must FAIL CLOSED.
        print("== 10. adversarial fail-closed checks ==")
        for label, bad_manifest in (
                ("different code SHA", make_manifest(RUN, START, END, sha="b" * 40)),
                ("different schema version", make_manifest(RUN, START, END, schema_version=2)),
                ("different date range", make_manifest(RUN, START, "2024-04-20")),
                ("different weather mode", make_manifest(RUN, START, END, weather_mode="with_weather")),
                ("different repository identity",
                 make_manifest(RUN, START, END, repository_identity="someone/else")),
                ("different evidence regime",
                 make_manifest(RUN, START, END, evidence_regime="prospective_full_candidate_data")),
                ("different candidate identity fields",
                 make_manifest(RUN, START, END,
                               candidate_identity_fields=["date", "game_pk", "player_id"])),
                ("different model version",
                 make_manifest(RUN, START, END,
                               model_artifact_versions={"model_version": "m2",
                                                        "selection_policy_version": "s1",
                                                        "calibration_version": "c1",
                                                        "feature_version": "f1"})),
                ("different run id", make_manifest("canonical-OTHER-0000", START, END)),
        ):
            try:
                cd.assert_identity_compatible(idx, bad_manifest)
                bad(f"{label}: accepted an incompatible checkpoint")
            except cd.IdentityMismatch:
                ok(f"{label}: IdentityMismatch (fail closed)")

        # A remote manifest that disagrees with the durable index must be
        # rejected BEFORE restore creates any local run directory or manifest.
        # The pre-fix path wrote manifest.json first and only then checked
        # identity, contradicting restore_from_durable()'s own recovery contract.
        poisoned = json.loads(json.dumps(idx))
        poisoned["identity"]["code_git_sha"] = "b" * 40
        no_write_dir = os.path.join(repo2, "backtest", "canonical_runs", "identity-reject")
        _real_load = cd.load_durable_index
        cd.load_durable_index = lambda *a, **k: poisoned
        try:
            cd.restore_from_durable(no_write_dir, RUN, repo_root=repo2)
            bad("incompatible remote manifest/index: accepted")
        except cd.IdentityMismatch:
            ok("incompatible remote manifest/index rejected before restore writes")
        finally:
            cd.load_durable_index = _real_load
        check("identity rejection created no local run directory",
              os.path.exists(no_write_dir), False)

        # A locally present date is reusable only when BOTH the row bytes
        # and metadata bytes match the durable ledger. Corrupting only the meta
        # file must force a verified restore rather than a silent skip.
        meta_victim = done[1]
        meta_path = cr.checkpoint_meta_path(run_dir2, meta_victim)
        with open(meta_path, "a", encoding="utf-8") as f:
            f.write("\n")
        rep_meta = cd.restore_from_durable(run_dir2, RUN, repo_root=repo2)
        check("corrupt local meta is restored rather than skipped",
              meta_victim in rep_meta["restored"], True)
        check("repaired local meta matches durable ledger",
              cd._sha256_file(meta_path),
              idx["dates"][meta_victim]["meta_sha256"])

        # Existing durable paths are not enough to skip a date. If the
        # local checkpoint bytes conflict with the durable ledger, push must
        # fail closed rather than overwrite or silently accept the conflict.
        conflict_victim = done[2]
        conflict_data = cr.checkpoint_data_path(run_dir2, conflict_victim)
        with open(conflict_data, "a", encoding="utf-8") as f:
            f.write(json.dumps({"tampered": True}) + "\n")
        conflict_res = cd.push_durable_checkpoint(
            run_dir2, m2, dates=[conflict_victim], repo_root=repo2)
        check("conflicting existing durable date does not push",
              conflict_res["pushed"], False)
        check("existing durable conflict is reported explicitly",
              "does not match local checkpoint" in (conflict_res["reason"] or ""), True)
        repair = cd.restore_from_durable(run_dir2, RUN, repo_root=repo2)
        check("conflicted local row is repaired from durable source",
              conflict_victim in repair["restored"], True)
        check("repaired row checksum returns to durable ledger",
              cd._sha256_file(conflict_data),
              idx["dates"][conflict_victim]["data_sha256"])

        # A required git staging failure must abort before commit/push. The
        # previous implementation ignored stage_blob()'s None return and could
        # continue building a partial durable commit.
        STAGE_RUN = "canonical-STAGEFAIL-abcd1234"
        stage_date = "2024-05-01"
        stage_dir = os.path.join(repo2, "backtest", "canonical_runs", STAGE_RUN)
        os.makedirs(os.path.join(stage_dir, "checkpoints"), exist_ok=True)
        stage_manifest = make_manifest(STAGE_RUN, stage_date, stage_date)
        cr._atomic_write_json(cr.manifest_path(stage_dir), stage_manifest)
        cr.write_checkpoint(
            stage_dir, stage_date, synth_rows(stage_date, 2), "ok",
            source_code_git_sha="a" * 40)

        _real_git = cd._git
        def _fail_update_index(args, **kwargs):
            if args and args[0] == "update-index":
                return subprocess.CompletedProcess(
                    args=args, returncode=1, stdout="", stderr="simulated stage failure")
            return _real_git(args, **kwargs)

        cd._git = _fail_update_index
        try:
            stage_res = cd.push_durable_checkpoint(
                stage_dir, stage_manifest, dates=[stage_date], repo_root=repo2)
        finally:
            cd._git = _real_git
        check("required stage failure does not report a push",
              stage_res["pushed"], False)
        check("required stage failure is explicit",
              "failed to stage required durable blob" in (stage_res["reason"] or ""), True)

        # Corrupted checksum in the ledger must be caught on restore.
        repo3 = os.path.join(sandbox, "container-3")
        git(["clone", "-q", origin, repo3], cwd=sandbox, env=GIT_ENV)
        cd.fetch_durable_branch(repo_root=repo3)
        corrupt = json.loads(json.dumps(idx))
        victim = done[0]
        corrupt["dates"][victim]["data_sha256"] = "0" * 64
        run_dir3 = os.path.join(repo3, "backtest", "canonical_runs", RUN)
        os.makedirs(os.path.join(run_dir3, "checkpoints"), exist_ok=True)
        cr._atomic_write_json(cr.manifest_path(run_dir3), manifest)
        _real_load = cd.load_durable_index
        cd.load_durable_index = lambda *a, **k: corrupt
        try:
            cd.restore_from_durable(run_dir3, RUN, repo_root=repo3)
            bad("corrupted checkpoint checksum: accepted")
        except cd.DurableIntegrityError:
            ok("corrupted checkpoint checksum: DurableIntegrityError (fail closed)")
        finally:
            cd.load_durable_index = _real_load
        check("nothing written for the corrupt date",
              os.path.exists(cr.checkpoint_data_path(run_dir3, victim)), False)

        # Statcast cache with a required column missing must fail closed.
        try:
            import pandas as pd
            p = os.path.join(sandbox, "bad_cache.parquet")
            pd.DataFrame({"game_date": ["2024-04-01"], "batter": [1]}).to_parquet(p)
            try:
                cd.validate_statcast_cache(p, required_columns=cd.REQUIRED_STATCAST_COLUMNS)
                bad("statcast cache missing launch_speed: accepted")
            except cd.CacheIntegrityError as exc:
                ok(f"statcast cache missing required columns: CacheIntegrityError ({str(exc)[:48]}...)")
            rep_ns = cd.validate_statcast_cache(p, strict=False)
            check("non-strict report marks it unusable", rep_ns["usable"], False)
            ok(f"cache report carries checksum {rep_ns['content_sha256'][:12]} and {rep_ns['row_count']} rows")
        except ImportError:
            print("  --   pandas unavailable; statcast cache checks skipped")

        # A truncated gzip blob must not silently yield partial rows.
        raw = cd._read_durable_blob(cd.durable_paths(RUN, done[0])["rows_gz"], repo_root=repo2)
        try:
            gzip.decompress(raw[: len(raw) // 2])
            bad("truncated gzip: decompressed without error")
        except Exception:
            ok("truncated gzip blob raises rather than yielding partial rows")

        # ══ 11. The cache gate is actually WIRED IN, not merely available.
        print("== 11. StatcastStore rejects a bad cache instead of trusting the filename ==")
        try:
            import pandas as pd
            from backtest import engine as _eng

            cache_dir = os.path.join(sandbox, "statcast_cache")
            os.makedirs(cache_dir, exist_ok=True)
            # Filename claims full coverage; contents are missing launch_speed
            # and every other column scoring depends on. The old code path
            # accepted exactly this.
            bad = os.path.join(cache_dir, "statcast_2024_through_2024-10-01.parquet")
            pd.DataFrame({"game_date": ["2024-04-01"], "batter": [1]}).to_parquet(bad)

            pulled = {"called": False}

            def fake_statcast(*a, **k):
                pulled["called"] = True
                return pd.DataFrame()

            real = _eng.m.pyb.statcast
            _eng.m.pyb.statcast = fake_statcast
            try:
                store = _eng.StatcastStore(2024, "2024-05-01", cache_dir=cache_dir, verbose=False)
                try:
                    store.load()
                except RuntimeError:
                    pass          # expected: fell through to a pull that returned nothing
                check("bad cache rejected -- store repulled instead of trusting it",
                      pulled["called"], True)
                check("bad cache data was not adopted", store._df, None)
            finally:
                _eng.m.pyb.statcast = real

            # And a GOOD cache is still accepted, so the gate is not just
            # "always reject". Coverage must actually span the request: an
            # earlier version of this fixture used 2024 dates for a 2025 store,
            # the gate correctly rejected it for insufficient coverage, and the
            # test then hit the real network. The stub below makes that
            # impossible to miss again.
            good_cols = {c: [0, 0] for c in _eng.STATCAST_COLUMNS}
            good_cols["game_date"] = ["2025-04-01", "2025-06-01"]
            good = os.path.join(cache_dir, "statcast_2025_through_2025-10-01.parquet")
            pd.DataFrame(good_cols).to_parquet(good)

            _eng.m.pyb.statcast = fake_statcast     # any pull here is a failure
            pulled["called"] = False
            try:
                store2 = _eng.StatcastStore(2025, "2025-05-01", cache_dir=cache_dir, verbose=False)
                df = store2.load()
                check("valid cache still accepted", len(df), 2)
                check("valid cache did NOT trigger a repull", pulled["called"], False)
                check("accepted cache carries a validation report",
                      bool(getattr(store2, "cache_report", None)), True)
            finally:
                _eng.m.pyb.statcast = real
        except ImportError:
            print("  --   pandas/engine unavailable; cache wiring check skipped")

        print()
        print(f"passed: {PASS}   failed: {FAIL}")
        return 0 if FAIL == 0 else 1
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
