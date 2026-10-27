# The release gates

`pytest voidloom/tests/` binds the **fast** surface: the three organ self-checks
and the platform-isolation / instructions round-trip regressions
(`test_organ_selfchecks.py`, ~16 s). It is the gate that must stay runnable, so
it stays short.

These harnesses are the **slow** gates. Each runs real installs and takes
minutes; folding them into `pytest` would make the default suite unrunnable,
which is how a gate stops being run at all. Drive them together with:

```bash
python3 voidloom/tests/harness/run_all.py            # all of them, sequential
python3 voidloom/tests/harness/run_all.py --list     # what exists and what it grades
```

| harness | grades | typical |
|---|---|---|
| `cli_install_suite.py` | the 89-check CLI/install contract | ~20 min |
| `property_battery_16_install.py` | 33 filesystem shapes, install + uninstall + heartbeat writers | ~2 min |
| `c2_install_schema_suite.py` | C2 — the installer builds a brain its package can read | ~10 min |
| `c3_one_installer_suite.py` | C3 — one installer, and the Tier-0 path | ~5 min |
| `c4_planner_applier_suite.py` | C4 — structural acceptance, rollback arms, strict JSON | ~3 min |
| `c5_self_report_suite.py` | C5 — the verdict matches the row beside it | ~4 min |
| `c6_anchor_suite.py` | C6 — window anchors and persisted counts | ~1 min |
| `c7_writer_and_claim_suite.py` | C7 — the doctrine writer and the scoped claim | ~1 min |

Each takes `<package_dir> <scratch_root>` and exits non-zero on any violation.

**Not driven by `run_all.py`, each with its reason** (Crucible-3 D01-F8: these two were
tabled here as gates and named by no runner — the UNBOUND class, in the README of the
very thing that exists to bind them):

| harness | why it is not a standing gate |
|---|---|
| `mutation_matrix.py` | a mutation **battery** — it deliberately breaks the tree to check the checks, so it runs by hand against a copy, never in a sweep that must leave the tree as it found it |
| `canary_r8_notice.py` | a one-shot canary for a specific historical round; kept as evidence, not as a standing gate |
| `outside_target_diff.py` | an **instrument**, not a gate: F4's snapshot-diff. `run --home H --target-root T --declared D -- <cmd>` snapshots a `$HOME` before and after the command and refuses on any difference the declared write-set (`install.sh --print-write-set`) does not name — the named members (`settings.json`, `CLAUDE.md`, the env file) from their own records, the rest from a wide walk; `verbs --declared D --log L` grades a recorder shim's log of scheduler calls (`systemctl --user enable --now …`) against the declared `verbs`. `run` hands its command an environment in which `HOME` and the four XDG base dirs are pinned under `--home`, the model/wheel cache redirectors are dropped, and a `WIZARD_*` path override outside `--home` is refused (rc 4, named) — as is any NON-absolute value (relative, `~/x`, `$HOME/x`) of a path-bearing `WIZARD_*` variable, because the child would resolve it against the caller's cwd; the boundary is every base-directory variable, not one. `run`'s exit code is the diff verdict, not the command's: assert `command_rc` from the report. Driven by the cold gate and by `voidloom/tests/test_outside_target_diff.py`; it takes a home and a declaration, not `<package_dir> <scratch_root>` |

`run_all.py --coverage` DERIVES this list from the directory and exits non-zero on any
harness that is neither driven nor excused, so a new one cannot be born unbound.
