# Legacy preregistration port (2026-09-25): provenance manifest

These pre-registration records come from draft PRs #74, #77, #78 and #84. Those PRs were opened on a repository history that no longer shares a merge base with `main` (it was re-rooted), so they cannot merge directly.

**What this port is:**
- **Verbatim, byte-for-byte.** Every file is unchanged from its source; the SHA-256 below verifies it.
- **Authoritative timestamps:** the authoritative authorship and pre-registration times are the source commits listed here, which remain on their original branches. The commit that adds these files to `main` is only a preservation copy, and its timestamp does not re-date the pre-registrations.
- **No claims:** nothing here was executed, validated or promoted by this port, and no code from those PRs is ported.
- **Branches untouched:** the source branches and PRs are left intact. Closing them needs Jacob's authorization.

**Variants and duplicates:**
- `PREREG_HR_EXECUTION_V2.md` is byte-identical in #77, #80 and #83 (the canonical V2).
- #84 carries a later amendment of it: the conditional Arm-E continuation added in commit `7078a07957`. That version is preserved separately under `legacy_prereg_amendments/`.

| Source PR | Source branch (head) | Source path | Ported to | First added (commit, author date) | Last changed (commit, author date, author) | Bytes | SHA-256 |
|---|---|---|---|---|---|---|---|
| #74 | `accuracy/hr-execution-prereg-01` (`0ae4535d5a`) | `engineering/PREREG_HR_EXECUTION_V1.md` | `engineering/PREREG_HR_EXECUTION_V1.md` | `f17cebea9a` 2026-08-29T01:41:44+00:00 | `0ae4535d5a` 2026-09-01T19:19:50+00:00 (Jacob Werries) | 16879 | `b03a83756159370217b0a57e5327bfa9833608d17ea79990b950385da113c5fc` |
| #74 | `accuracy/hr-execution-prereg-01` (`0ae4535d5a`) | `engineering/PREREG_HR_EXECUTION_V1_REDTEAM.md` | `engineering/PREREG_HR_EXECUTION_V1_REDTEAM.md` | `0ae4535d5a` 2026-09-01T19:19:50+00:00 | `0ae4535d5a` 2026-09-01T19:19:50+00:00 (Jacob Werries) | 7458 | `2fdb801e5a1001a105f315e65877db5d7763ca759598d121afbce1bc9e5d58de` |
| #77 | `superchad/hr-execution-prereg-v2-01` (`5869216a73`) | `engineering/PREREG_HR_EXECUTION_V2.md` | `engineering/PREREG_HR_EXECUTION_V2.md` | `ce4a70729c` 2026-08-29T09:11:26-05:00 | `5869216a73` 2026-08-29T10:06:04-05:00 (werriesjacob1-cmyk) | 21002 | `79969db3e7b3a34c6ebc4c8d2ec667eea951b8fddccfe13d3f590f85c5043917` |
| #78 | `superchad/pa-opportunity-decisive-prereg-01` (`b019e49981`) | `engineering/PREREG_PA_OPPORTUNITY_DECISIVE_V1.md` | `engineering/PREREG_PA_OPPORTUNITY_DECISIVE_V1.md` | `ae11fe9061` 2026-08-29T09:22:14-05:00 | `b019e49981` 2026-08-29T09:44:13-05:00 (werriesjacob1-cmyk) | 11186 | `56a9dab974079089de9d8d478c20b2a176918bf161f1a3f2b20c130b4b75a15c` |
| #84 | `superchad/hr-contact-state-integration-01` (`d9d40fa175`) | `engineering/PREREG_HR_EXECUTION_V2.md` | `engineering/legacy_prereg_amendments/pr84_7078a07957/PREREG_HR_EXECUTION_V2.md` | `97554d7920` 2026-08-29T10:20:48-05:00 | `7078a07957` 2026-08-29T10:41:53-05:00 (werriesjacob1-cmyk) | 24446 | `5ec5cc902c0e1fe17962c68703211b27749facf54698adde77c7280d37d70225` |

**Verify:**

```
echo 'b03a83756159370217b0a57e5327bfa9833608d17ea79990b950385da113c5fc  engineering/PREREG_HR_EXECUTION_V1.md' | sha256sum -c
echo '2fdb801e5a1001a105f315e65877db5d7763ca759598d121afbce1bc9e5d58de  engineering/PREREG_HR_EXECUTION_V1_REDTEAM.md' | sha256sum -c
echo '79969db3e7b3a34c6ebc4c8d2ec667eea951b8fddccfe13d3f590f85c5043917  engineering/PREREG_HR_EXECUTION_V2.md' | sha256sum -c
echo '56a9dab974079089de9d8d478c20b2a176918bf161f1a3f2b20c130b4b75a15c  engineering/PREREG_PA_OPPORTUNITY_DECISIVE_V1.md' | sha256sum -c
echo '5ec5cc902c0e1fe17962c68703211b27749facf54698adde77c7280d37d70225  engineering/legacy_prereg_amendments/pr84_7078a07957/PREREG_HR_EXECUTION_V2.md' | sha256sum -c
```

Alligator.
