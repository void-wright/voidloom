# PACKAGE_MANIFEST — the inventory of what ships

> Generated 2026-10-27T15:11:47Z from the checkout at `e9567c6`.
> **The git index is the declaration.** There is no second exclusion list to
> drift from it: a file that must not ship is untracked, so it cannot appear
> here, in the installer's copy set, or in a package. The previous generator
> kept a parallel EXCLUDE set and 8 of the 7 files it named shipped anyway.
> Where each file LANDS on an installed machine is `install/lay_manifest.json`,
> derived from the same index by the same tool (`--lay`).

| | |
|---|---|
| files tracked | 417 |
| files hashed | 417 |
| total bytes | 9949619 |
| tracked but ABSENT on disk | 0 |

## What is in the package, by subsystem

### `organs` — The organism — engine + L2-L5 organ suite  ·  72 files, 4433691 bytes

| file | bytes | sha256 |
|---|---:|---|
| `organs/_self_aware.py` | 13843 | `ab00d3aa6e9eeccc` |
| `organs/affect_lighthouse.py` | 30948 | `9a20023c7b3d80a4` |
| `organs/affect_probe_scan.py` | 33914 | `b9d164947f29a6b1` |
| `organs/anti_recency.py` | 52239 | `81014da3760af46c` |
| `organs/approvals_policy.py` | 14151 | `a5b2cffccd6877b7` |
| `organs/arcane_manifest.py` | 70881 | `c7433aa7cee72baa` |
| `organs/assayer.py` | 33829 | `d08c60f7a8cc53a2` |
| `organs/belief_cli.py` | 19007 | `356c2411a17e146e` |
| `organs/belief_engine.py` | 73377 | `b286bbdfc7116cd2` |
| `organs/calibration_memory.py` | 47193 | `00fea3a3d28f1ded` |
| `organs/claude_memory.py` | 196781 | `9e7bed400b4aa9d9` |
| `organs/claude_memory_lite.py` | 39513 | `73a4f8007fff6e60` |
| `organs/cognitive_map.py` | 40183 | `794e44f65a79e4e3` |
| `organs/communities.py` | 44809 | `fb4d4d8da43713e4` |
| `organs/council.py` | 50065 | `570feac66706d322` |
| `organs/counterfactual_memory.py` | 73874 | `a1a149e9049ffe07` |
| `organs/decision_units.py` | 120605 | `50d50e784f065112` |
| `organs/dream_ledger.py` | 60620 | `665c5d6158d7c184` |
| `organs/dream_pipeline.py` | 127443 | `7a097ab25415d113` |
| `organs/embed_backfill.py` | 11765 | `4a48cf8fc254d994` |
| `organs/embed_resolver.py` | 5581 | `da6bf11ab37f3e5a` |
| `organs/embedding_upgrade.py` | 54351 | `1efbdd285c133057` |
| `organs/gap_mapping.py` | 107155 | `9e659adec01af772` |
| `organs/governance.py` | 46295 | `ea70b57a2ff92acf` |
| `organs/gpu_guard.py` | 57313 | `412bd1d077eab846` |
| `organs/hybrid_retrieval.py` | 78734 | `05285e6f8681b1cd` |
| `organs/kind_taxonomy.py` | 30780 | `042ae8556f4600df` |
| `organs/least_privilege.py` | 6171 | `79a34a1ddd9ced50` |
| `organs/memory_dreaming.py` | 106512 | `198f5f75c6931c5b` |
| `organs/memory_write.py` | 47150 | `b6926d3059294f3a` |
| `organs/model_garden.py` | 40947 | `066b00408b186cf8` |
| `organs/monthly_self_eval.py` | 203955 | `ab6970a8dfcd4c5e` |
| `organs/organ_run_log.py` | 25697 | `406cae11096e9326` |
| `organs/organ_yield_sentinel.py` | 53105 | `b7c412b1971a34a9` |
| `organs/persona_lenses.py` | 39932 | `a76126c29dedc8cf` |
| `organs/ppr_recall.py` | 19953 | `c6c0f448cc86288a` |
| `organs/pre_cognitive_recall.py` | 56211 | `08e6995ac9106d52` |
| `organs/precognitive_cache_refresh.py` | 9601 | `e709b0b6e4623afc` |
| `organs/procedural_memory.py` | 39134 | `3a8103a356ee5a1a` |
| `organs/provenance_graph.py` | 37519 | `0a38b1d3d957ecad` |
| `organs/quickening.py` | 35181 | `3ecdc9f98dfa369a` |
| `organs/qwen_reranker.py` | 23431 | `6866dc3a4ef4ce98` |
| `organs/recall_channels.py` | 29263 | `a42ae7fdeb84e5ac` |
| `organs/recall_diversity.py` | 45638 | `aa336ba8a2eb68f7` |
| `organs/recall_explain.py` | 24994 | `7e470866c6f0c802` |
| `organs/schema_migrator.py` | 25230 | `fea20209240e0e9d` |
| `organs/skill_graduation.py` | 55320 | `39acab23e3f999ef` |
| `organs/sleep_loop.py` | 108895 | `3d9fff5ba8f90dc4` |
| `organs/temporal_edges.py` | 107549 | `982ce5bf3d4ef536` |
| `organs/temporal_rerank.py` | 22495 | `46da7426f9965325` |
| `organs/thinker.py` | 297015 | `2a024ad4fe640c9d` |
| `organs/thinker_get.py` | 13627 | `861dd7ea05bcbd8e` |
| `organs/thinkers.json` | 29573 | `9e7667bcc743ab87` |
| `organs/trust_tier.py` | 29857 | `63e5ceb90896745f` |
| `organs/trust_tier_cli.py` | 10084 | `dc33064ca979f61f` |
| `organs/void_render.py` | 37317 | `09187627e039a1e7` |
| `organs/voidloom_approvals.py` | 120545 | `49b44835dd162330` |
| `organs/voidloom_banner.py` | 104239 | `c7d9b35fc7ec1d0d` |
| `organs/voidloom_canary.py` | 114403 | `0f8a8005428a5c74` |
| `organs/voidloom_device.py` | 74175 | `e6534dd0f1ade94b` |
| `organs/voidloom_env.py` | 10400 | `f543d420b7a14b32` |
| `organs/voidloom_heartbeat.py` | 37464 | `7142ec7ccf30467b` |
| `organs/voidloom_hooks_status.py` | 421184 | `04933b0f28e7304c` |
| `organs/voidloom_instructions.py` | 71527 | `ffca380e1c52f4c3` |
| `organs/voidloom_manifest.py` | 22175 | `78dd44bf3c819e45` |
| `organs/voidloom_persona.py` | 31042 | `4edd49d4e5a4da86` |
| `organs/voidloom_preamble.py` | 163373 | `ce97d0f48c40d99b` |
| `organs/warden.py` | 17331 | `c6647d919efc8204` |
| `organs/website_memory.py` | 34611 | `b257b483b54eb756` |
| `organs/whisper_network.py` | 47038 | `9ff2aa8105cb866d` |
| `organs/wizard_config.py` | 42944 | `56205200da3f5ebc` |
| `organs/wizard_paths.py` | 4635 | `56f9a0a72042f0e9` |

### `voidloom` — The CLI facade  ·  159 files, 2584497 bytes

| file | bytes | sha256 |
|---|---:|---|
| `voidloom/README.md` | 11011 | `9caf4360ef99ad2e` |
| `voidloom/__init__.py` | 1835 | `9cd57391014c7c5c` |
| `voidloom/__main__.py` | 188 | `37d219722ff5765c` |
| `voidloom/_agent_context.py` | 19008 | `1f9bfca833c78057` |
| `voidloom/_approvals_faces.py` | 42385 | `ece04bc6d7c96606` |
| `voidloom/_batch.py` | 14033 | `7207e72ebeaba2ed` |
| `voidloom/_bootstrap.py` | 14572 | `08cf4eb3da660553` |
| `voidloom/_facade.py` | 38539 | `c51738a45510e0f4` |
| `voidloom/_guard.py` | 11713 | `11461a1e7bd1ef77` |
| `voidloom/_paths.py` | 6524 | `6822733e57600c1a` |
| `voidloom/cli.py` | 141063 | `7fc4fad70fca62fa` |
| `voidloom/tests/__init__.py` | 29 | `c00b7e2467584fad` |
| `voidloom/tests/_owned_brain.py` | 4509 | `e550d2b14985ecb6` |
| `voidloom/tests/_thinker_fakes.py` | 15135 | `d63b82c100143add` |
| `voidloom/tests/_thinker_gemini_fake.py` | 10414 | `f573c49ac6eaa4f5` |
| `voidloom/tests/conftest.py` | 8528 | `f863a810f6783e7a` |
| `voidloom/tests/fixtures/claude_2_1_287_tool_call_unparsed.json` | 3665 | `8fc8387ca64a6711` |
| `voidloom/tests/harness/README.md` | 3514 | `9d572f2f3792cb87` |
| `voidloom/tests/harness/c2_install_schema_suite.py` | 19152 | `48cf8c6741611e64` |
| `voidloom/tests/harness/c3_one_installer_suite.py` | 11688 | `ad6dd04d661309fd` |
| `voidloom/tests/harness/c4_planner_applier_suite.py` | 16657 | `33d6dc9ff931b22d` |
| `voidloom/tests/harness/c5_self_report_suite.py` | 9431 | `1948605ef36132da` |
| `voidloom/tests/harness/c6_anchor_suite.py` | 8908 | `e99194b9618e0829` |
| `voidloom/tests/harness/c7_writer_and_claim_suite.py` | 7534 | `9764d90d933990ae` |
| `voidloom/tests/harness/canary_r8_notice.py` | 13802 | `a37c716963247e4e` |
| `voidloom/tests/harness/cli_install_suite.py` | 103577 | `43b69a864bd0dafa` |
| `voidloom/tests/harness/mutation_matrix.py` | 81230 | `05b28d0f67c0fc64` |
| `voidloom/tests/harness/outside_target_diff.py` | 35895 | `e6f50e6d8b5a5577` |
| `voidloom/tests/harness/property_battery_16_install.py` | 31627 | `1067b6c358dc43d9` |
| `voidloom/tests/harness/run_all.py` | 5792 | `ecc5b27cfa5dc5e6` |
| `voidloom/tests/test_accelerator_table.py` | 49139 | `258409f57a9c38c9` |
| `voidloom/tests/test_affect_lighthouse_ddl_names_r2.py` | 1029 | `24af95d79420d7eb` |
| `voidloom/tests/test_approvals_faces.py` | 7516 | `c8c58ca5f1bf872a` |
| `voidloom/tests/test_approvals_policy_d26.py` | 24423 | `a45907a3d24bfd79` |
| `voidloom/tests/test_approvals_verdict_is_derived.py` | 4536 | `629774aa889e2bb3` |
| `voidloom/tests/test_arcane_manifest_r3.py` | 11989 | `3afd9bee28aa813e` |
| `voidloom/tests/test_attach_db.py` | 9395 | `cda60fd668e17faa` |
| `voidloom/tests/test_batch.py` | 24541 | `787ab8d29980937f` |
| `voidloom/tests/test_belief_authorship_d26.py` | 14106 | `35e1041b7feb788d` |
| `voidloom/tests/test_belief_pair_counts_r2.py` | 1474 | `303248c5329ebf9e` |
| `voidloom/tests/test_belief_quarantine_r1.py` | 17284 | `bd357e9bae2d9433` |
| `voidloom/tests/test_brain_busy_wait_r3.py` | 12632 | `5f206fb97833533e` |
| `voidloom/tests/test_brain_keyed_locks_r3g3.py` | 22536 | `9e0a08835c195e68` |
| `voidloom/tests/test_codex_hook_fires_on_resume_d26.py` | 10567 | `486166b342a0a461` |
| `voidloom/tests/test_cognitive_map_degree_cost_d26.py` | 4187 | `5875111fc486ec70` |
| `voidloom/tests/test_counterfactual_busy_rN.py` | 3868 | `73db1eba9964349c` |
| `voidloom/tests/test_counterfactual_reply_r3.py` | 6482 | `df9b54c38ad7c631` |
| `voidloom/tests/test_cron_roster.py` | 28735 | `ef47c386be9014b3` |
| `voidloom/tests/test_cross_tier.py` | 10741 | `a3cd65e348dd4577` |
| `voidloom/tests/test_cuda_no_device_guard.py` | 9966 | `7246ef7054b8f963` |
| `voidloom/tests/test_device_ambient_empty.py` | 6594 | `74362adaa3787108` |
| `voidloom/tests/test_docs_name_real_paths.py` | 13642 | `e0aafc370c98f39d` |
| `voidloom/tests/test_doctor_cpu_only_truth_r2.py` | 1789 | `3e963d8c1954d319` |
| `voidloom/tests/test_dream_lane_r3.py` | 10718 | `f8ca59901150588f` |
| `voidloom/tests/test_dream_llm_failure_visible.py` | 15775 | `7a8a2e916a1b7277` |
| `voidloom/tests/test_dream_reply_echo_rN.py` | 4705 | `3cebd7d9a1a64b27` |
| `voidloom/tests/test_dream_reply_shape_r3.py` | 12184 | `b8e755651a0f7ae8` |
| `voidloom/tests/test_dream_units_do_not_race.py` | 4433 | `87671a0bae28d468` |
| `voidloom/tests/test_dream_watch_unconfigured_r2.py` | 1498 | `bce1e4d75dce79ed` |
| `voidloom/tests/test_dreamfix_r2w2a.py` | 38506 | `d1fc63957206abb3` |
| `voidloom/tests/test_dreamfix_tranche_a.py` | 45850 | `860f29459691dcf9` |
| `voidloom/tests/test_dreaming_starved_r1.py` | 7060 | `888848a8cc53e96a` |
| `voidloom/tests/test_edge_decay_idempotent_r2.py` | 6853 | `0856a5cd52e0e2c0` |
| `voidloom/tests/test_edge_decay_repair_r2d.py` | 12087 | `7bba65e209c0263e` |
| `voidloom/tests/test_edge_decay_retires_r2d.py` | 8536 | `e02347b289a12a4c` |
| `voidloom/tests/test_edge_retire_readers_writers_r2d.py` | 12353 | `d6752d4b2e2d7720` |
| `voidloom/tests/test_embedding_provenance.py` | 27290 | `e0295425e2bf8fa6` |
| `voidloom/tests/test_encoder_tier_is_bijective.py` | 9285 | `e08d4e89b254ee18` |
| `voidloom/tests/test_env_file_mode_follows_symlink.py` | 5084 | `d5df32f5ac8290f7` |
| `voidloom/tests/test_env_file_path_is_curated.py` | 4651 | `f7d4f5258ebd5cf6` |
| `voidloom/tests/test_env_file_reader.py` | 111079 | `45c8b59c36f86a8c` |
| `voidloom/tests/test_first_night_is_derived.py` | 10458 | `2b9b097840fdfcaf` |
| `voidloom/tests/test_fts_lone_punctuation_r2.py` | 962 | `f07d0702b995c511` |
| `voidloom/tests/test_gap_research_non_answer_r1.py` | 7580 | `d13acb6dcd505e6c` |
| `voidloom/tests/test_generic_naming_r1.py` | 24267 | `82246f9f4c18361b` |
| `voidloom/tests/test_generic_personas.py` | 9644 | `441cc0f36796410d` |
| `voidloom/tests/test_gpu_display_declared.py` | 6998 | `4788f84581ed97bf` |
| `voidloom/tests/test_heartbeat_headless_d26.py` | 7546 | `b4301eda25d08068` |
| `voidloom/tests/test_hidden_cuda_probe_r1.py` | 3921 | `c2a70343a42ff847` |
| `voidloom/tests/test_installer_accelerator.py` | 40526 | `9a64dfb7b1d3d16b` |
| `voidloom/tests/test_installer_literals.py` | 13974 | `f3497e7a1d314520` |
| `voidloom/tests/test_installer_questions.py` | 37609 | `764af8ede1695d23` |
| `voidloom/tests/test_installer_refusal_cause_r3.py` | 5461 | `c48aaf571d81485e` |
| `voidloom/tests/test_installer_residue.py` | 8996 | `be6def9e1a41f20b` |
| `voidloom/tests/test_installer_seed_choice_r3.py` | 9950 | `ef007d74d4c96acd` |
| `voidloom/tests/test_instructions_refresh_r3.py` | 24687 | `0356e53afb1ae379` |
| `voidloom/tests/test_launchd_prefix_agrees.py` | 2910 | `dc805052bb4c7596` |
| `voidloom/tests/test_lay_manifest.py` | 32508 | `46ef4c6bbcb13cce` |
| `voidloom/tests/test_leak_clean.py` | 11453 | `360ba48a2b90d5f9` |
| `voidloom/tests/test_leak_gate.py` | 9858 | `85558a868799daee` |
| `voidloom/tests/test_migration_041_plain_sql_release_r2.py` | 4209 | `fc4edf7319aaea4a` |
| `voidloom/tests/test_no_test_file_is_gitignored.py` | 1809 | `7bd5fc023edd020e` |
| `voidloom/tests/test_notice_claim_holds.py` | 6688 | `2d70862f629b569a` |
| `voidloom/tests/test_nvidia_smi_off_path.py` | 11289 | `ced91e9e5364c2fa` |
| `voidloom/tests/test_organ_posture.py` | 6220 | `921573bfde2081b3` |
| `voidloom/tests/test_organ_selfchecks.py` | 10952 | `d3a0babc96bc753f` |
| `voidloom/tests/test_organ_yield_exit_codes.py` | 14391 | `82d92c7335c026a2` |
| `voidloom/tests/test_outside_target_diff.py` | 54029 | `dd54d9313e19c1ba` |
| `voidloom/tests/test_persona_forge.py` | 23899 | `38208ba2c18f0d9c` |
| `voidloom/tests/test_preflight_model_cache_r2.py` | 5491 | `390226dfe6b1c487` |
| `voidloom/tests/test_preflight_upgrades_pip_r2.py` | 1738 | `5d0369a1088c1ebe` |
| `voidloom/tests/test_preflight_verb.py` | 2694 | `fab053d8ac241306` |
| `voidloom/tests/test_qwen_revision_pinned.py` | 3887 | `ad2c57e0c5c3551a` |
| `voidloom/tests/test_recall_topk_after_placement.py` | 3824 | `b697d7b65e0fd52a` |
| `voidloom/tests/test_release_gates_execute.py` | 3803 | `e8ae84f6b38ab25e` |
| `voidloom/tests/test_reranker_gate.py` | 27923 | `0f272e2e01596bcb` |
| `voidloom/tests/test_schema_migrator.py` | 8188 | `6aa0410c65f897ea` |
| `voidloom/tests/test_seed_anchor_delta.py` | 19453 | `6b97c55bff94c3ad` |
| `voidloom/tests/test_self_check_seed_scope_r3.py` | 15061 | `fa7f001f0c98be46` |
| `voidloom/tests/test_self_eval_counts_its_own_fires_d26.py` | 2182 | `21f022651716a9c2` |
| `voidloom/tests/test_sentinel_live_run_r2.py` | 7816 | `1846f74567a4003a` |
| `voidloom/tests/test_sentinel_sleep_runs_r1.py` | 12467 | `76e8e3c731a4ad7d` |
| `voidloom/tests/test_skip_is_not_success_r3g3.py` | 14612 | `712de358722adec4` |
| `voidloom/tests/test_sleep_catch_up_verb.py` | 3143 | `72a16ab29090aae0` |
| `voidloom/tests/test_sleep_loop_brain_lock_r3.py` | 12800 | `60d19e34fd22831c` |
| `voidloom/tests/test_sleep_loop_unit_tests_r3g3.py` | 2379 | `f9917dcdc6a9b6ac` |
| `voidloom/tests/test_sleep_loop_unstallable_r1.py` | 31281 | `bf063b29abea1a41` |
| `voidloom/tests/test_stranger_tier1_upgrade.py` | 88786 | `28342573f4607159` |
| `voidloom/tests/test_suite_children_have_no_terminal.py` | 2046 | `5ca230c2de180a5d` |
| `voidloom/tests/test_suite_is_portable.py` | 5091 | `5925f8ad9985a8f9` |
| `voidloom/tests/test_thinker_bounded_capture.py` | 15199 | `66430b5e316262a7` |
| `voidloom/tests/test_thinker_canary_findings.py` | 7713 | `57db3cddbb1a64b2` |
| `voidloom/tests/test_thinker_command_template.py` | 17563 | `055ea278355330a4` |
| `voidloom/tests/test_thinker_credential_resolver.py` | 8950 | `c81d050cd2245f4f` |
| `voidloom/tests/test_thinker_gemini_login.py` | 9590 | `89f6cba85d8d42cb` |
| `voidloom/tests/test_thinker_gemini_r1fix.py` | 12644 | `6c0bfab35c2c6f94` |
| `voidloom/tests/test_thinker_gemini_seam.py` | 27117 | `5b3e15ed23d3cc45` |
| `voidloom/tests/test_thinker_get_catalog.py` | 8728 | `43fec79c4b0992a9` |
| `voidloom/tests/test_thinker_get_cleanroom.py` | 13650 | `07d80d592f90575d` |
| `voidloom/tests/test_thinker_get_gemini.py` | 5376 | `448137b1571ae419` |
| `voidloom/tests/test_thinker_instruction_free.py` | 7990 | `847e46da3e881577` |
| `voidloom/tests/test_thinker_iteration2.py` | 9714 | `47a33449ad5b4d41` |
| `voidloom/tests/test_thinker_login.py` | 8268 | `79f635a9485f9a27` |
| `voidloom/tests/test_thinker_m13_host_units.py` | 11078 | `5bf31391bbad5915` |
| `voidloom/tests/test_thinker_off_unconfigured_r2.py` | 7308 | `04f59ff75839bd40` |
| `voidloom/tests/test_thinker_organ_parsers.py` | 14223 | `9c4a6df4f5f9aa0d` |
| `voidloom/tests/test_thinker_posture.py` | 10015 | `cb2f40f0c0ae6471` |
| `voidloom/tests/test_thinker_probe_wording.py` | 6774 | `9e2d11a288b64bcc` |
| `voidloom/tests/test_thinker_r3_first_try.py` | 17186 | `38dafe32f82eae6f` |
| `voidloom/tests/test_thinker_r3_http_deadline.py` | 6299 | `3b439148af3ba19a` |
| `voidloom/tests/test_thinker_r3_screen_v7.py` | 11191 | `1b38c5e7a49e15e1` |
| `voidloom/tests/test_thinker_r3_unit_readers.py` | 7587 | `2ddc04384e1e2c71` |
| `voidloom/tests/test_thinker_record_and_set.py` | 14419 | `9aef7b2286d77a90` |
| `voidloom/tests/test_thinker_reply_screen_r2.py` | 18640 | `9759e38eb995f67b` |
| `voidloom/tests/test_thinker_retirements.py` | 14755 | `e3ac0efe6e211397` |
| `voidloom/tests/test_thinker_round2_low_items.py` | 18263 | `d4339cf3c29eeb17` |
| `voidloom/tests/test_thinker_seam_gate.py` | 10470 | `425d70e90f7d4f91` |
| `voidloom/tests/test_thinker_text_posture_max_turns_rN.py` | 8768 | `db67f4527eee0cd6` |
| `voidloom/tests/test_thinker_tool_attempt_rP.py` | 11379 | `2d5d57e5e897f062` |
| `voidloom/tests/test_thinker_typed_errors.py` | 13273 | `e833158af8e4a57a` |
| `voidloom/tests/test_tier0_alone_is_a_brain.py` | 9770 | `2949d715ea300084` |
| `voidloom/tests/test_tier0_context.py` | 6213 | `a4d85105badc200f` |
| `voidloom/tests/test_tier0_degrade_on_model_absent.py` | 8394 | `5cc3315ecb4036ef` |
| `voidloom/tests/test_tier0_only_binds_the_runtime.py` | 3394 | `287f82c8bf876711` |
| `voidloom/tests/test_tier1_ambient.py` | 151502 | `4b3d0b7b8cd8c596` |
| `voidloom/tests/test_voidloom.py` | 37476 | `3672c97213bdef80` |
| `voidloom/tests/test_windows_scripts_encoding.py` | 3585 | `7407c0787dd07272` |
| `voidloom/voidloom` | 3353 | `3c657f4401579380` |
| `voidloom/voidloom.cmd` | 2145 | `cbb49bf1a325035a` |

### `migrations` — Schema + additive migrations  ·  35 files, 145566 bytes

| file | bytes | sha256 |
|---|---:|---|
| `migrations/migration_001_fts5.sql` | 6083 | `b68aac91ded15661` |
| `migrations/migration_002_kind_taxonomy.sql` | 4472 | `2b74e2b261d61b8b` |
| `migrations/migration_003_temporal_edges.sql` | 2956 | `5675b4ba6fd309ff` |
| `migrations/migration_004_procedural_memory.sql` | 1634 | `a7bf2fbeda38b496` |
| `migrations/migration_005_sleep_loop.sql` | 1790 | `7e8f432665601914` |
| `migrations/migration_006_persona_lenses.sql` | 1604 | `eb63464fcd55dd12` |
| `migrations/migration_007_skill_graduation.sql` | 2537 | `f523ca6e21330c7c` |
| `migrations/migration_008_anti_recency.sql` | 2246 | `8da1942d6907e262` |
| `migrations/migration_009_whisper_network.sql` | 2667 | `750eaeb5b29b1f09` |
| `migrations/migration_010_provenance_graph.sql` | 3060 | `dc482061ddf4d69c` |
| `migrations/migration_011_cognitive_map.sql` | 1065 | `a38fe7b0ce0b6530` |
| `migrations/migration_012_cross_tier_bridges.sql` | 2201 | `69174d1ef4c374ad` |
| `migrations/migration_013_embedding_upgrade.sql` | 1532 | `dcb755845acaf3e5` |
| `migrations/migration_014_calibration_memory.sql` | 3386 | `961e40c9e7b42e8e` |
| `migrations/migration_015_gap_mapping.sql` | 4401 | `4e2242e95bbe1161` |
| `migrations/migration_016_pre_cognitive_recall.sql` | 1407 | `12dc7a6222fdcf44` |
| `migrations/migration_017_dreaming.sql` | 3650 | `a1226943cbb9df72` |
| `migrations/migration_018_council.sql` | 3589 | `c372e6728cae7645` |
| `migrations/migration_019_counterfactual.sql` | 4967 | `0a50fe4b9faa4189` |
| `migrations/migration_020_arcane_manifest.sql` | 3136 | `9398382734373b88` |
| `migrations/migration_021_self_eval.sql` | 3358 | `dc09c70fd47ec59c` |
| `migrations/migration_022_bridge_tiers_and_scope.sql` | 2751 | `0c5f89fb075a8ee9` |
| `migrations/migration_023_belief_engine.sql` | 8227 | `9b5b9f99030b8772` |
| `migrations/migration_024_diversity.sql` | 2675 | `440d54771b1d5be4` |
| `migrations/migration_027_dream_pipeline.sql` | 10131 | `e2b05e5f10da0718` |
| `migrations/migration_028_communities.sql` | 3872 | `7a73b1db49945774` |
| `migrations/migration_029_warden_untrusted_source.sql` | 3825 | `fbad88fd3bda6d2d` |
| `migrations/migration_030_decision_units.sql` | 17133 | `ab89cc5dfda02ae1` |
| `migrations/migration_031_qwen_tier.sql` | 1125 | `20b58f3b4ac5ef13` |
| `migrations/migration_032_governance_proposals.sql` | 2990 | `a0a28a8301826fee` |
| `migrations/migration_033_embedding_provenance.sql` | 4316 | `f9630e0be519044a` |
| `migrations/migration_039_gap_research_log_thinker.sql` | 7945 | `8825283265f6daf5` |
| `migrations/migration_040_sleep_run_lifecycle.sql` | 8130 | `7110f654e5ec69c1` |
| `migrations/migration_041_belief_quarantine.sql` | 8065 | `c09d085857957b88` |
| `migrations/schema.sql` | 2640 | `1413cd5027588589` |

### `install` — Installers, wiring, acceptance gate, pinned requirements  ·  23 files, 929868 bytes

| file | bytes | sha256 |
|---|---:|---|
| `install/CLAUDE.md.template` | 6780 | `a44033e0a1480fcb` |
| `install/accelerator_plan.py` | 29033 | `85eda129380b09cd` |
| `install/cron_roster.json` | 14435 | `e6b6364295050eb8` |
| `install/cron_roster.py` | 16531 | `1f34cca9a2d7b089` |
| `install/env_file.py` | 100805 | `a5b0e76dc6fdfea5` |
| `install/fixtures/golden_vectors_nomic.json` | 144295 | `7ca287b27b7b7b91` |
| `install/fixtures/golden_vectors_qwen.json` | 191567 | `4e9f3a72527ad86f` |
| `install/golden_vectors.py` | 10872 | `fc268ca66da450f9` |
| `install/install.ps1` | 56293 | `c5f516b888771cd6` |
| `install/install_council.sh` | 9233 | `7efe5c588226bf00` |
| `install/install_crons.sh` | 29886 | `083aef2869e64648` |
| `install/install_persona_forge.sh` | 6387 | `fe9730cd877a6a74` |
| `install/lay.py` | 17458 | `90cf7eb13d48e731` |
| `install/lay_manifest.json` | 121514 | `15b85d7fe7e91308` |
| `install/merge_config.py` | 4601 | `8fbef13d93358b0f` |
| `install/preflight.py` | 15439 | `87142e524d44dceb` |
| `install/requirements.hashes.txt` | 1742 | `ca1b6241e2a83b32` |
| `install/requirements.txt` | 9268 | `7b4fd5182fbf243a` |
| `install/self_check.py` | 49306 | `1bf69ed41047c7a1` |
| `install/tier1_probe.py` | 42737 | `01f45138ccadea67` |
| `install/wire_persona.ps1` | 8385 | `6c760a0105833234` |
| `install/wire_persona.sh` | 10855 | `0896d140f773a1bc` |
| `install/write_set.json` | 32446 | `e755bc0e1f473d06` |

### `seed` — Starter memories + schema-only blank DB  ·  2 files, 729955 bytes

| file | bytes | sha256 |
|---|---:|---|
| `seed/claude_memory_blank.db` | 573440 | `f1e15db427fa7a56` |
| `seed/seed_pack.json` | 156515 | `49c30cd2cd813e3c` |

### `hooks` — Host session bindings  ·  4 files, 52635 bytes

| file | bytes | sha256 |
|---|---:|---|
| `hooks/session_start_inject.py` | 24706 | `d722ced39a187195` |
| `hooks/voidloom_notify.py` | 19424 | `610f1b134d66565e` |
| `hooks/voidloom_precompact.py` | 7027 | `da67266abbf2ac5e` |
| `hooks/voidloom_signoff.py` | 1478 | `f5b48e7f42b92483` |

### `cron` — systemd units + timers  ·  48 files, 81247 bytes

| file | bytes | sha256 |
|---|---:|---|
| `cron/_config_dir.sh` | 854 | `d33909719f942d80` |
| `cron/platform-detection.sh` | 28052 | `ce48eac3990a28c1` |
| `cron/setup-anti-recency-cron.sh` | 1690 | `aa51980cb6059f36` |
| `cron/setup-arcane-manifest-cron.sh` | 1723 | `008a4eefc372cd4b` |
| `cron/setup-counterfactual-revisit-cron.sh` | 3658 | `77ea7ba6d4de1b4f` |
| `cron/setup-dreaming-cron.sh` | 4223 | `d0d35fd599eccae7` |
| `cron/setup-gap-mapping-cron.sh` | 2901 | `f5ed0f2df611150e` |
| `cron/setup-self-eval-cron.sh` | 2915 | `0da99a2c285dc37c` |
| `cron/setup-skill-graduation-cron.sh` | 2823 | `4effa975c263dd73` |
| `cron/setup-sleep-loop-cron.sh` | 3429 | `b9b7093a4e123eef` |
| `cron/setup-whisper-digest-cron.sh` | 2066 | `f716c2167a67b024` |
| `cron/sleep-loop-config.json` | 467 | `c4631abd4b7d6b0c` |
| `cron/wizard-library-affect-probe.service` | 916 | `ff69d7435b6ab6a7` |
| `cron/wizard-library-affect-probe.timer` | 834 | `56ed179930f9380f` |
| `cron/wizard-library-anti-recency.service` | 642 | `5f059b406e671aa6` |
| `cron/wizard-library-anti-recency.timer` | 544 | `485057fd217bafb9` |
| `cron/wizard-library-arcane-manifest.service` | 835 | `307514ee6c4a1a05` |
| `cron/wizard-library-arcane-manifest.timer` | 652 | `c3b4fc99e4ee6dec` |
| `cron/wizard-library-belief-scan.service` | 674 | `552c758e010afc58` |
| `cron/wizard-library-belief-scan.timer` | 941 | `ee0bae2dcbd95752` |
| `cron/wizard-library-cognitive-map.service` | 531 | `00a6801d29a9ec67` |
| `cron/wizard-library-cognitive-map.timer` | 477 | `aa27f1e2cc6dbd2f` |
| `cron/wizard-library-counterfactual-revisit.service` | 543 | `f4ddbdf8fa4f0975` |
| `cron/wizard-library-counterfactual-revisit.timer` | 658 | `98792cf8d1171920` |
| `cron/wizard-library-dream-daytime.service` | 1389 | `64b6eac0654915fa` |
| `cron/wizard-library-dream-daytime.timer` | 292 | `19c35eac409e2384` |
| `cron/wizard-library-dreaming.service` | 1838 | `b161c4195d66700d` |
| `cron/wizard-library-dreaming.timer` | 422 | `a97b9976bb527526` |
| `cron/wizard-library-embed-backfill.service` | 1501 | `39a91aff18a1cf61` |
| `cron/wizard-library-embed-backfill.timer` | 578 | `28ce043da7f1fe97` |
| `cron/wizard-library-gap-mapping.service` | 455 | `b8dfbea75a39f75e` |
| `cron/wizard-library-gap-mapping.timer` | 379 | `dfe50d13827946cd` |
| `cron/wizard-library-gap-research.service` | 575 | `7e995bea81520db4` |
| `cron/wizard-library-gap-research.timer` | 490 | `15ad54633c52eae1` |
| `cron/wizard-library-organ-run-log.service` | 396 | `01bd61258691baea` |
| `cron/wizard-library-organ-run-log.timer` | 706 | `f4d490dea452eef3` |
| `cron/wizard-library-organ-yield.service` | 2259 | `d5ec980823aabef9` |
| `cron/wizard-library-organ-yield.timer` | 549 | `40d1ee611ffca70c` |
| `cron/wizard-library-pre-cognitive.service` | 1068 | `29139876c0595cc2` |
| `cron/wizard-library-pre-cognitive.timer` | 905 | `3fda103c3d6ba3d8` |
| `cron/wizard-library-self-eval.service` | 594 | `134ffb07d8f87b03` |
| `cron/wizard-library-self-eval.timer` | 386 | `82bc2f78cf6dbefb` |
| `cron/wizard-library-skill-graduation.service` | 672 | `d64fe7ec0f2fe9b9` |
| `cron/wizard-library-skill-graduation.timer` | 387 | `c73f555d2f56ca5a` |
| `cron/wizard-library-sleep-loop.service` | 956 | `d34aa8f67a2b2fa5` |
| `cron/wizard-library-sleep-loop.timer` | 371 | `cd4b3032c7e98185` |
| `cron/wizard-library-whisper-digest.service` | 656 | `464dc44376c93acf` |
| `cron/wizard-library-whisper-digest.timer` | 375 | `8c13e65d6e5b42ff` |

### `capability` — Host capability detection  ·  2 files, 41262 bytes

| file | bytes | sha256 |
|---|---:|---|
| `capability/detect.py` | 37712 | `7873ed4b7d86b8bd` |
| `capability/detect_embed_model.sh` | 3550 | `9f93d53f9500d1b1` |

### `persona` — Persona skills, council voices, slash commands  ·  20 files, 88002 bytes

| file | bytes | sha256 |
|---|---:|---|
| `persona/commands/council.md` | 3936 | `5415b94897c74ba3` |
| `persona/commands/persona-forge.md` | 1029 | `172c4a3f627da2d5` |
| `persona/council_skills/council-architect/SKILL.md` | 5447 | `7440717a582ae803` |
| `persona/council_skills/council-dreamer/SKILL.md` | 5677 | `a406750a2569c8e8` |
| `persona/council_skills/council-seer/SKILL.md` | 4442 | `eda148cc8a16d258` |
| `persona/council_skills/council-skeptic/SKILL.md` | 5108 | `b5e2566ce8b78764` |
| `persona/council_skills/council-synthesizer/SKILL.md` | 6174 | `7a974ec29665188e` |
| `persona/skills/memory-operator/IDENTITY_OUTLINE.md` | 2120 | `d5ab527b2499bca6` |
| `persona/skills/memory-operator/RECALL_MAP_OUTLINE.md` | 2123 | `5c994596e93e076c` |
| `persona/skills/persona-forge/BASELINE.md` | 5268 | `9fd9653dd94caafc` |
| `persona/skills/persona-forge/QUESTIONNAIRE.md` | 12786 | `f31084bb89331c6b` |
| `persona/skills/persona-forge/SKILL.md` | 12345 | `6feeb9128e58f399` |
| `persona/skills/persona-forge/TEMPLATES/SKILL.md.tmpl` | 1439 | `d0c3d696ed07cbc9` |
| `persona/skills/persona-forge/TEMPLATES/adaptive-recall-map.md.tmpl` | 442 | `b91b134c3892366c` |
| `persona/skills/persona-forge/TEMPLATES/growth_log.md.tmpl` | 593 | `c9704a72515ababd` |
| `persona/skills/persona-forge/TEMPLATES/operations-guide.md.tmpl` | 1116 | `b2439ed719af3bd9` |
| `persona/skills/persona-forge/TEMPLATES/persona-foundation.md.tmpl` | 1717 | `b97df3af46c71ccf` |
| `persona/skills/persona-forge/TEMPLATES/personality.json.tmpl` | 3196 | `72d7cda425b32b43` |
| `persona/skills/persona-forge/TEMPLATES/voice.md.tmpl` | 533 | `d66076ed2dea66a0` |
| `persona/skills/persona-forge/validate_persona.py` | 12511 | `c2621de5c61e2740` |

### `tools` — Maintainer-only: package gates + recall harness  ·  21 files, 222399 bytes

| file | bytes | sha256 |
|---|---:|---|
| `tools/build_manifest.py` | 21081 | `7e727f80b735d02a` |
| `tools/cross_platform_audit.py` | 3990 | `a9a9306b7de01aa6` |
| `tools/gen_cron_audit.py` | 4849 | `b7cf8031759ef55b` |
| `tools/git-hooks/install.sh` | 1636 | `8f082db0063b91c6` |
| `tools/git-hooks/pre-commit` | 444 | `a7f0a54ab05bbcc4` |
| `tools/git-hooks/pre-push` | 1480 | `003a4705fe84f598` |
| `tools/gpu_pin_gate.py` | 44906 | `c329794bee05d7f9` |
| `tools/harness/assertions.sh` | 6580 | `de450faa0d9e070a` |
| `tools/harness/check_must_retrieve.py` | 3545 | `f226112171ca8ed3` |
| `tools/harness/compare_recall.py` | 11022 | `67cc4326085289f2` |
| `tools/harness/corpus_presence.py` | 14697 | `378f9b94bfb12f27` |
| `tools/harness/eval_queries.txt` | 1642 | `0bfaf4689322185c` |
| `tools/harness/eval_recall.sh` | 13312 | `1b51bde720aede09` |
| `tools/harness/must_retrieve.json` | 495 | `a7bba254e1b6aebb` |
| `tools/harness/no_regression_analysis.md` | 6918 | `e100f1bbd4e2c9a2` |
| `tools/harness/run_gate.sh` | 6971 | `e857bb0e5b919ead` |
| `tools/harness/test_harness.py` | 17074 | `bb171866b5a5f1ff` |
| `tools/leak_gate.py` | 15091 | `c5aae0965ffa23e7` |
| `tools/pii_gate.py` | 27217 | `e36321c4c7ee2874` |
| `tools/release_check.py` | 8671 | `378de4e7722bcc77` |
| `tools/token_doctor.py` | 10778 | `4db1a76ccd8ae406` |

### `docs` — Documentation  ·  12 files, 215531 bytes

| file | bytes | sha256 |
|---|---:|---|
| `docs/COUNCIL_README.md` | 5392 | `58e0cca8f3bbeb74` |
| `docs/FIRST_NIGHT.md` | 16123 | `c35368333c8d2beb` |
| `docs/GETTING_STARTED.md` | 19332 | `6af8d4be38a85697` |
| `docs/INSTRUCTIONS.md` | 4513 | `dc86c655f4b97c83` |
| `docs/LAYOUT.md` | 8185 | `991e78bee98bcc95` |
| `docs/ONBOARDING_GUIDE.md` | 57844 | `c57b1155f68322e4` |
| `docs/OVERNIGHT_THINKER_SETUP.md` | 32164 | `520a6cb49c7aa880` |
| `docs/PACKAGE_MANIFEST.md` | 31687 | `3f392393e8d01975` |
| `docs/PERSONA_FORGE.md` | 12359 | `ddff9923016d7145` |
| `docs/RESTORE_MANIFEST.md` | 8534 | `5876ed9b624409b4` |
| `docs/gates/CORPUS_INDEX.md` | 7253 | `593555d97bc3cf5a` |
| `docs/gates/GATE_v1.md` | 12145 | `502a3f183d8e0022` |

### `(root)` — Front door: README, licence, installer, manifest  ·  10 files, 403986 bytes

| file | bytes | sha256 |
|---|---:|---|
| `.gitignore` | 6351 | `921af78d4a45fc62` |
| `CHANGELOG.md` | 7902 | `df3be6a10bed80cf` |
| `CODE_OF_CONDUCT.md` | 837 | `d8a208bd8c0f35ff` |
| `CONTRIBUTING.md` | 6709 | `475e8fbc09ff909e` |
| `LICENSE` | 11362 | `73c162a8c72b5ed4` |
| `NOTICE` | 4391 | `6608b4df230ab445` |
| `README.md` | 26899 | `6cadeec2a804a2c3` |
| `SECURITY.md` | 1930 | `384e3060f9d1866a` |
| `install.sh` | 328910 | `7352afaab4e910b2` |
| `uninstall.sh` | 8695 | `da81d6b412856c4b` |

### `.github` —   ·  9 files, 20980 bytes

| file | bytes | sha256 |
|---|---:|---|
| `.github/ISSUE_TEMPLATE/bug_report.yml` | 1636 | `2ee1f8c34d146bf7` |
| `.github/ISSUE_TEMPLATE/config.yml` | 223 | `bb3603653cf81185` |
| `.github/ISSUE_TEMPLATE/feature_request.yml` | 493 | `70a7deb422eededc` |
| `.github/pull_request_template.md` | 537 | `8ad5d52d0d783f1c` |
| `.github/workflows/ci.yml` | 2210 | `d4ba874c2f59de4f` |
| `.github/workflows/clean-room.yml` | 3812 | `3be9fdc9ff93fd61` |
| `.github/workflows/leak-gate.yml` | 2864 | `af80abebedf099f0` |
| `.github/workflows/mac-cpu-route.yml` | 6296 | `8b8b7319fc8d4ab2` |
| `.github/workflows/mac-mps-probe.yml` | 2909 | `f935a6dd397646a5` |
