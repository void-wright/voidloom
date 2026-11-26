# PACKAGE_MANIFEST — the inventory of what ships

> Generated 2026-11-26T02:26:56Z from the working tree whose HEAD was `b9f41c0`
> (the shipped sha is the commit that CARRIES this file -- HEAD's successor, by construction;
> `install/lay_manifest.json`, emitted from the same index, is the file `lay.py` verifies against).
> **The git index is the declaration.** There is no second exclusion list to
> drift from it: a file that must not ship is untracked, so it cannot appear
> here, in the installer's copy set, or in a package. The previous generator
> kept a parallel EXCLUDE set and 8 of the 7 files it named shipped anyway.
> Where each file LANDS on an installed machine is `install/lay_manifest.json`,
> derived from the same index by the same tool (`--lay`).

| | |
|---|---|
| files tracked | 576 |
| files hashed | 576 |
| total bytes | 12250688 |
| tracked but ABSENT on disk | 0 |

## What is in the package, by subsystem

### `organs` — The organism — engine + L2-L5 organ suite  ·  83 files, 5005012 bytes

| file | bytes | sha256 |
|---|---:|---|
| `organs/_self_aware.py` | 13843 | `ab00d3aa6e9eeccc` |
| `organs/access_event.py` | 2418 | `4b60f3a03e5048ae` |
| `organs/affect_lighthouse.py` | 30948 | `9a20023c7b3d80a4` |
| `organs/affect_probe_scan.py` | 34389 | `445156d34066b10c` |
| `organs/anti_recency.py` | 53126 | `f9dd51d2f2abf479` |
| `organs/approvals_policy.py` | 19760 | `24c40935d5ad4a73` |
| `organs/arcane_manifest.py` | 73047 | `36b72c7960887f20` |
| `organs/assayer.py` | 34460 | `5be30586333f215e` |
| `organs/belief_cli.py` | 19439 | `b9cd462cb7127977` |
| `organs/belief_engine.py` | 74746 | `184081621bdb0191` |
| `organs/brain_clock.py` | 1944 | `62be8c0542fae70f` |
| `organs/calibration_memory.py` | 47528 | `c619e8c1ad72f77c` |
| `organs/claude_memory.py` | 234806 | `a27f4cd105be3627` |
| `organs/claude_memory_lite.py` | 70074 | `658288280a602fcd` |
| `organs/cognitive_map.py` | 41154 | `408555005a815e09` |
| `organs/communities.py` | 50878 | `31a89f75a2b56403` |
| `organs/council.py` | 50336 | `9a6758b48c1b6826` |
| `organs/counterfactual_memory.py` | 74520 | `c9b131c65d629c0e` |
| `organs/decision_units.py` | 121199 | `c133b3d99fc4de0f` |
| `organs/dream_ledger.py` | 64023 | `79a2c6c60585f1b7` |
| `organs/dream_pipeline.py` | 130426 | `0df9bc047ebfb67e` |
| `organs/dry_plan.py` | 1995 | `d6e1274adcc77603` |
| `organs/embed_backfill.py` | 20330 | `7f83d78d92914736` |
| `organs/embed_resolver.py` | 10765 | `41961c7562f8cee4` |
| `organs/embedder_lock.py` | 77149 | `2a93d619968760da` |
| `organs/embedders.lock.json` | 2557 | `6247a9cbb5988828` |
| `organs/embedding_upgrade.py` | 74170 | `8f2fa268d5bd46a2` |
| `organs/engine_pin.py` | 3601 | `6bca803d0791f986` |
| `organs/gap_mapping.py` | 110757 | `26227fca1f357642` |
| `organs/governance.py` | 46816 | `dce3f94be09b81b6` |
| `organs/gpu_guard.py` | 61054 | `cf4f73e60743669d` |
| `organs/hybrid_retrieval.py` | 88234 | `861a735be09fa5f8` |
| `organs/kind_taxonomy.py` | 31197 | `b8383f161da3487b` |
| `organs/least_privilege.py` | 6171 | `79a34a1ddd9ced50` |
| `organs/mark_admin.py` | 21668 | `e19dee0327ab9345` |
| `organs/marks.py` | 56641 | `7b5820682b0b8934` |
| `organs/memory_dreaming.py` | 107928 | `916036661478226c` |
| `organs/memory_write.py` | 145911 | `d3eb30da3d420b73` |
| `organs/model_fetch.py` | 8995 | `faf76bcf1a2b2418` |
| `organs/model_garden.py` | 42154 | `a1278c6297f38640` |
| `organs/monthly_self_eval.py` | 205996 | `6b5b7dab4db82e1a` |
| `organs/organ_run_log.py` | 32360 | `2e233ec6bcf133f4` |
| `organs/organ_yield_sentinel.py` | 53311 | `8e2ab17718c0ef40` |
| `organs/persona_lenses.py` | 40348 | `67f41a305c7dce12` |
| `organs/ppr_recall.py` | 19953 | `c6c0f448cc86288a` |
| `organs/pre_cognitive_recall.py` | 55897 | `6e153606128afd65` |
| `organs/precognitive_cache_refresh.py` | 9977 | `194e5de402673738` |
| `organs/procedural_memory.py` | 39766 | `8051d607795e3a29` |
| `organs/provenance_graph.py` | 38100 | `757572be18c42f9f` |
| `organs/quickening.py` | 36152 | `327a1512161e83ee` |
| `organs/qwen_reranker.py` | 27221 | `657583633d1f1691` |
| `organs/recall_channels.py` | 31168 | `4dd9624f5a835cfa` |
| `organs/recall_diversity.py` | 46776 | `734e8e98cc0b784f` |
| `organs/recall_explain.py` | 24993 | `5233e64ff0e86183` |
| `organs/schema_migrator.py` | 28854 | `34f0507377a12431` |
| `organs/seed_loader.py` | 21280 | `4c10283eb0dd2765` |
| `organs/skill_graduation.py` | 55980 | `6055189b366df4ab` |
| `organs/sleep_loop.py` | 116078 | `f2746c3f2bc7fc09` |
| `organs/temporal_edges.py` | 107944 | `b946bcb472bc3dee` |
| `organs/temporal_rerank.py` | 22495 | `46da7426f9965325` |
| `organs/thinker.py` | 297015 | `2a024ad4fe640c9d` |
| `organs/thinker_get.py` | 13627 | `861dd7ea05bcbd8e` |
| `organs/thinkers.json` | 29573 | `9e7667bcc743ab87` |
| `organs/tier_set.py` | 67313 | `74123a0c7053ec36` |
| `organs/trust_tier.py` | 29910 | `479bd98b62e1731f` |
| `organs/trust_tier_cli.py` | 10084 | `dc33064ca979f61f` |
| `organs/void_render.py` | 37317 | `09187627e039a1e7` |
| `organs/voidloom_approvals.py` | 120989 | `c87431d899d1fb83` |
| `organs/voidloom_banner.py` | 104239 | `c7d9b35fc7ec1d0d` |
| `organs/voidloom_canary.py` | 114403 | `0f8a8005428a5c74` |
| `organs/voidloom_device.py` | 82954 | `c8ffb2a57440fca2` |
| `organs/voidloom_env.py` | 24582 | `6fe9129d71ba317c` |
| `organs/voidloom_heartbeat.py` | 37464 | `7142ec7ccf30467b` |
| `organs/voidloom_hooks_status.py` | 421184 | `feb039892ed6f5f8` |
| `organs/voidloom_instructions.py` | 71527 | `ffca380e1c52f4c3` |
| `organs/voidloom_manifest.py` | 22175 | `78dd44bf3c819e45` |
| `organs/voidloom_persona.py` | 31042 | `4edd49d4e5a4da86` |
| `organs/voidloom_preamble.py` | 163876 | `5ef140869e0c2a0f` |
| `organs/warden.py` | 16653 | `7f95236e04497abc` |
| `organs/website_memory.py` | 39846 | `a6d76e501f36e348` |
| `organs/whisper_network.py` | 45655 | `2df4f873681f4662` |
| `organs/wizard_config.py` | 43143 | `b41f3696865a214a` |
| `organs/wizard_paths.py` | 4635 | `56f9a0a72042f0e9` |

### `voidloom` — The CLI facade  ·  294 files, 4019424 bytes

| file | bytes | sha256 |
|---|---:|---|
| `voidloom/README.md` | 16490 | `89b14c5e5495c16e` |
| `voidloom/__init__.py` | 1835 | `518b2ea0b3c8904f` |
| `voidloom/__main__.py` | 188 | `37d219722ff5765c` |
| `voidloom/_agent_context.py` | 26980 | `a57bf0d38c5e2758` |
| `voidloom/_approvals_faces.py` | 43572 | `be442b62098db58a` |
| `voidloom/_batch.py` | 17563 | `06edbe4bc58e3209` |
| `voidloom/_bootstrap.py` | 22957 | `05a066e5115d8c5a` |
| `voidloom/_facade.py` | 50526 | `07d28c9b70ac4227` |
| `voidloom/_guard.py` | 11714 | `1ce5fd6ba82d8da7` |
| `voidloom/_paths.py` | 6956 | `5e32ae0a9515cb3c` |
| `voidloom/cli.py` | 210738 | `d7a1417928263250` |
| `voidloom/tests/__init__.py` | 29 | `c00b7e2467584fad` |
| `voidloom/tests/_owned_brain.py` | 4509 | `e550d2b14985ecb6` |
| `voidloom/tests/_thinker_fakes.py` | 15135 | `d63b82c100143add` |
| `voidloom/tests/_thinker_gemini_fake.py` | 10414 | `f573c49ac6eaa4f5` |
| `voidloom/tests/_wall_brain.py` | 12087 | `5dd4a5e29f630008` |
| `voidloom/tests/conftest.py` | 10411 | `37a08850c815bed6` |
| `voidloom/tests/fixtures/claude_2_1_287_tool_call_unparsed.json` | 3665 | `8fc8387ca64a6711` |
| `voidloom/tests/fixtures/fakesmi_card_dropped/nvidia-smi` | 752 | `517fe6d7900c1d3d` |
| `voidloom/tests/fixtures/fakesmi_one_card/nvidia-smi` | 649 | `ed339e740adfc7d9` |
| `voidloom/tests/fixtures/fakesmi_segfault/nvidia-smi` | 129 | `563ac827abae59de` |
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
| `voidloom/tests/harness/outside_target_diff.py` | 36226 | `88c6b0259aeb7a4e` |
| `voidloom/tests/harness/property_battery_16_install.py` | 31627 | `1067b6c358dc43d9` |
| `voidloom/tests/harness/run_all.py` | 5792 | `ecc5b27cfa5dc5e6` |
| `voidloom/tests/test_a_centroid_is_not_embedding_work.py` | 3458 | `6b755695078e137d` |
| `voidloom/tests/test_a_damaged_row_stops_nothing.py` | 2884 | `012ee5647ea4b895` |
| `voidloom/tests/test_a_stamp_is_not_a_lock.py` | 18151 | `1731132406a31c22` |
| `voidloom/tests/test_accelerator_table.py` | 49518 | `e0d1594d990b4ec3` |
| `voidloom/tests/test_affect_lighthouse_ddl_names_r2.py` | 1029 | `24af95d79420d7eb` |
| `voidloom/tests/test_approvals_faces.py` | 7516 | `c8c58ca5f1bf872a` |
| `voidloom/tests/test_approvals_policy_d26.py` | 24423 | `a45907a3d24bfd79` |
| `voidloom/tests/test_approvals_verdict_is_derived.py` | 4536 | `629774aa889e2bb3` |
| `voidloom/tests/test_arcane_manifest_r3.py` | 11989 | `3afd9bee28aa813e` |
| `voidloom/tests/test_attach_db.py` | 21508 | `2ef4995572bb2d0c` |
| `voidloom/tests/test_attached_and_offline_installs.py` | 6577 | `97437ff0db233eb3` |
| `voidloom/tests/test_backfill_counts_stamps.py` | 7042 | `bf0bc1dcffb4bd19` |
| `voidloom/tests/test_batch.py` | 24541 | `787ab8d29980937f` |
| `voidloom/tests/test_belief_authorship_d26.py` | 14168 | `f200244d800ce308` |
| `voidloom/tests/test_belief_pair_counts_r2.py` | 1474 | `303248c5329ebf9e` |
| `voidloom/tests/test_belief_quarantine_r1.py` | 17346 | `6733b724413fec53` |
| `voidloom/tests/test_brain_busy_wait_r3.py` | 12632 | `5f206fb97833533e` |
| `voidloom/tests/test_brain_file_mode.py` | 2802 | `2c856469605db161` |
| `voidloom/tests/test_brain_keyed_locks_r3g3.py` | 22851 | `a9cbe8d647f1094e` |
| `voidloom/tests/test_capability_names_the_shipped_tier.py` | 3663 | `1de8b791afd3c1d9` |
| `voidloom/tests/test_centroid_stamp.py` | 15892 | `fcfa240497ded3aa` |
| `voidloom/tests/test_centroid_survives_a_change_of_vectors.py` | 7835 | `8e22ce55ca9c2e1a` |
| `voidloom/tests/test_channels_read_the_search_set.py` | 3423 | `40121be92eb2cc6e` |
| `voidloom/tests/test_codex_hook_fires_on_resume_d26.py` | 10567 | `486166b342a0a461` |
| `voidloom/tests/test_cognitive_map_degree_cost_d26.py` | 4187 | `5875111fc486ec70` |
| `voidloom/tests/test_community_epochs_are_pruned.py` | 4948 | `e8e52b80269bb7ff` |
| `voidloom/tests/test_compat_and_version_json.py` | 10842 | `2079947275c90250` |
| `voidloom/tests/test_config_default_tier.py` | 2799 | `620770094c825271` |
| `voidloom/tests/test_consumers_read_the_search_set.py` | 10068 | `5deb699d212fa508` |
| `voidloom/tests/test_counterfactual_busy_rN.py` | 3868 | `73db1eba9964349c` |
| `voidloom/tests/test_counterfactual_reply_r3.py` | 6482 | `df9b54c38ad7c631` |
| `voidloom/tests/test_cpu_answer_hides_every_card.py` | 7499 | `29e588f3c60a8788` |
| `voidloom/tests/test_cron_follows_xdg.py` | 1691 | `2bc9f8ab0f146633` |
| `voidloom/tests/test_cron_roster.py` | 28735 | `ef47c386be9014b3` |
| `voidloom/tests/test_cross_tier.py` | 10741 | `a3cd65e348dd4577` |
| `voidloom/tests/test_cuda_no_device_guard.py` | 9966 | `cbf77207426df874` |
| `voidloom/tests/test_derived_layer.py` | 21203 | `aff463b0000b26b7` |
| `voidloom/tests/test_derived_layer_surfaces.py` | 4586 | `32ace4b5f2f308ec` |
| `voidloom/tests/test_device_ambient_empty.py` | 7014 | `ae1a617c127529d8` |
| `voidloom/tests/test_digest_has_one_encoder.py` | 2211 | `a5a3e06dc7cc8f7d` |
| `voidloom/tests/test_display_card_never_the_fallback.py` | 13333 | `82f23745f6af1350` |
| `voidloom/tests/test_docs_name_real_paths.py` | 13642 | `e0aafc370c98f39d` |
| `voidloom/tests/test_docs_name_the_keyword_verdict.py` | 1631 | `4a34ec4d4f850f30` |
| `voidloom/tests/test_docs_state_the_lock_rules.py` | 6577 | `b90d1a79a4dbf4e2` |
| `voidloom/tests/test_doctor_brain_verdict.py` | 21763 | `5fe2c89f7076ddd8` |
| `voidloom/tests/test_doctor_cpu_only_truth_r2.py` | 1789 | `3e963d8c1954d319` |
| `voidloom/tests/test_doctor_prints_the_peft_pin.py` | 1177 | `db4cec867d98158d` |
| `voidloom/tests/test_doctor_reads_what_recall_reads.py` | 6252 | `74356844036b7db2` |
| `voidloom/tests/test_dream_lane_r3.py` | 10718 | `f8ca59901150588f` |
| `voidloom/tests/test_dream_llm_failure_visible.py` | 15794 | `7440124ff17966ea` |
| `voidloom/tests/test_dream_night_names_a_failed_phase.py` | 4635 | `2cb86bc5d89a620b` |
| `voidloom/tests/test_dream_reply_echo_rN.py` | 4705 | `3cebd7d9a1a64b27` |
| `voidloom/tests/test_dream_reply_shape_r3.py` | 12184 | `b8e755651a0f7ae8` |
| `voidloom/tests/test_dream_units_do_not_race.py` | 4433 | `87671a0bae28d468` |
| `voidloom/tests/test_dream_watch_unconfigured_r2.py` | 1498 | `bce1e4d75dce79ed` |
| `voidloom/tests/test_dream_windows_are_utc.py` | 4412 | `f8268a27a30ee5e9` |
| `voidloom/tests/test_dreamfix_r2w2a.py` | 38506 | `d1fc63957206abb3` |
| `voidloom/tests/test_dreamfix_tranche_a.py` | 49592 | `ac828b36b748b240` |
| `voidloom/tests/test_dreaming_starved_r1.py` | 7060 | `888848a8cc53e96a` |
| `voidloom/tests/test_edge_decay_idempotent_r2.py` | 6853 | `0856a5cd52e0e2c0` |
| `voidloom/tests/test_edge_decay_repair_r2d.py` | 12088 | `f11bcc716ead2301` |
| `voidloom/tests/test_edge_decay_retires_r2d.py` | 8536 | `e02347b289a12a4c` |
| `voidloom/tests/test_edge_retire_readers_writers_r2d.py` | 12353 | `d6752d4b2e2d7720` |
| `voidloom/tests/test_embedder_lock.py` | 41439 | `fa6a7bb43aadaf12` |
| `voidloom/tests/test_embedding_provenance.py` | 47949 | `bafb18b3076b0f44` |
| `voidloom/tests/test_embedding_upgrade_verbs.py` | 10779 | `a8e73b1c50408940` |
| `voidloom/tests/test_enacted_proposals_await_work.py` | 4978 | `38cf657cbda471ab` |
| `voidloom/tests/test_encoder_absent_every_surface.py` | 19034 | `a5a55e06f1ce6a4a` |
| `voidloom/tests/test_encoder_follows_the_connection.py` | 4626 | `d0c37c2aa8f347dc` |
| `voidloom/tests/test_encoder_tier_is_bijective.py` | 11497 | `535f0e1c0e57a6ee` |
| `voidloom/tests/test_engine_reads_the_search_set.py` | 32916 | `fb200b19a5044add` |
| `voidloom/tests/test_engine_strips_like_5_1_2.py` | 7871 | `069d1950b6e7be7e` |
| `voidloom/tests/test_env_file_mode_follows_symlink.py` | 5084 | `d5df32f5ac8290f7` |
| `voidloom/tests/test_env_file_path_is_curated.py` | 4651 | `f7d4f5258ebd5cf6` |
| `voidloom/tests/test_env_file_reader.py` | 112024 | `b594afd253b5038a` |
| `voidloom/tests/test_env_sidecar_discovery.py` | 8371 | `86acb3159b1d2b62` |
| `voidloom/tests/test_every_armed_unit_has_a_dry_run.py` | 5001 | `e49840549c10e78e` |
| `voidloom/tests/test_export_names_unverified_text.py` | 8421 | `b637547ecac6f18a` |
| `voidloom/tests/test_first_night_is_derived.py` | 10458 | `2b9b097840fdfcaf` |
| `voidloom/tests/test_front_door_refusals.py` | 4916 | `1c2cf686f6e3efda` |
| `voidloom/tests/test_fts_any_word.py` | 9287 | `7c6676405f55cef5` |
| `voidloom/tests/test_fts_lone_punctuation_r2.py` | 962 | `f07d0702b995c511` |
| `voidloom/tests/test_gap_research_non_answer_r1.py` | 7752 | `6eaaa464c8a7cb58` |
| `voidloom/tests/test_garden_loads_from_the_local_cache.py` | 2332 | `8888ec9ef8cc1f98` |
| `voidloom/tests/test_generic_naming_r1.py` | 24267 | `82246f9f4c18361b` |
| `voidloom/tests/test_generic_personas.py` | 9644 | `441cc0f36796410d` |
| `voidloom/tests/test_gpu_display_declared.py` | 6998 | `4788f84581ed97bf` |
| `voidloom/tests/test_gpu_guard_single_card_is_never_the_display.py` | 6820 | `1473331c071d0280` |
| `voidloom/tests/test_hard_delete_finishes.py` | 14868 | `1ebee4590eb415a7` |
| `voidloom/tests/test_hard_delete_leaves_nothing.py` | 8248 | `8e8b7fc8c2849701` |
| `voidloom/tests/test_hard_delete_touches_only_copies.py` | 15130 | `906aff96d2fbf9d2` |
| `voidloom/tests/test_heartbeat_headless_d26.py` | 7546 | `b4301eda25d08068` |
| `voidloom/tests/test_help_is_lazy.py` | 2965 | `43a182c8d0f64b29` |
| `voidloom/tests/test_hf_cache_one_resolver.py` | 5613 | `e539204c11016996` |
| `voidloom/tests/test_hidden_cuda_probe_r1.py` | 3921 | `c2a70343a42ff847` |
| `voidloom/tests/test_hook_counts_tokens_offline.py` | 3918 | `f531c3a77bb5a384` |
| `voidloom/tests/test_init_applies_skill_graduation.py` | 3693 | `67fe70dd8f43b0f3` |
| `voidloom/tests/test_init_prelock_snapshot.py` | 5715 | `20bbad9132d47a79` |
| `voidloom/tests/test_install_stops_and_says_why.py` | 23227 | `474ccf96583a5fbf` |
| `voidloom/tests/test_installer_accelerator.py` | 40526 | `9a64dfb7b1d3d16b` |
| `voidloom/tests/test_installer_counts_an_unreadable_card.py` | 5518 | `9d2ac671b1affb40` |
| `voidloom/tests/test_installer_literals.py` | 13974 | `f3497e7a1d314520` |
| `voidloom/tests/test_installer_marks_what_it_creates.py` | 8490 | `d7f73eb5782cc532` |
| `voidloom/tests/test_installer_questions.py` | 37609 | `764af8ede1695d23` |
| `voidloom/tests/test_installer_reads_lock_first.py` | 15174 | `b36578d73e2cc780` |
| `voidloom/tests/test_installer_refusal_cause_r3.py` | 5461 | `c48aaf571d81485e` |
| `voidloom/tests/test_installer_residue.py` | 8996 | `be6def9e1a41f20b` |
| `voidloom/tests/test_installer_seed_choice_r3.py` | 9950 | `ef007d74d4c96acd` |
| `voidloom/tests/test_instructions_refresh_r3.py` | 24687 | `0356e53afb1ae379` |
| `voidloom/tests/test_launchd_prefix_agrees.py` | 2910 | `dc805052bb4c7596` |
| `voidloom/tests/test_lay_manifest.py` | 32640 | `7ead8a9e95d0041c` |
| `voidloom/tests/test_leak_clean.py` | 11453 | `360ba48a2b90d5f9` |
| `voidloom/tests/test_leak_gate.py` | 9858 | `85558a868799daee` |
| `voidloom/tests/test_legacy_hint_words.py` | 1373 | `b9a8d618e216e958` |
| `voidloom/tests/test_lite_and_fetch_behaviours.py` | 8846 | `454377498ccfeba7` |
| `voidloom/tests/test_lock_refusals_name_the_fault.py` | 3013 | `c728636f6e615b0a` |
| `voidloom/tests/test_lock_table_checked_once.py` | 7210 | `49e62fa66d81eb92` |
| `voidloom/tests/test_lock_unmarked_is_typed.py` | 5429 | `050edb0a33e0a8a5` |
| `voidloom/tests/test_locked_reads_stay_cheap.py` | 3991 | `d40cddc8b2ee26ef` |
| `voidloom/tests/test_lockless_coverage_counts_reach.py` | 3522 | `f09973179c0334df` |
| `voidloom/tests/test_lost_lock.py` | 7458 | `e0aa8ccc55845c62` |
| `voidloom/tests/test_lost_lock_every_reader.py` | 18153 | `2c57fad02d75dbff` |
| `voidloom/tests/test_lost_lock_install_readers.py` | 7824 | `a7f152939d1f2500` |
| `voidloom/tests/test_manifest_lays_no_tests.py` | 5030 | `9ab8c613bb1645a3` |
| `voidloom/tests/test_marks_answers.py` | 15843 | `dfc4be2c34e0fede` |
| `voidloom/tests/test_marks_dedup_seed.py` | 5455 | `8f2bb21f96474555` |
| `voidloom/tests/test_marks_disk_full.py` | 7233 | `f946efa6baf9aae6` |
| `voidloom/tests/test_marks_dispatch.py` | 7564 | `d57428c9eb3fc979` |
| `voidloom/tests/test_marks_every_reader_is_classified.py` | 24904 | `8e2f0b1356c8710d` |
| `voidloom/tests/test_marks_every_writer_is_classified.py` | 10811 | `ca75f92965daa821` |
| `voidloom/tests/test_marks_numbers.py` | 5654 | `fb73975b4861285a` |
| `voidloom/tests/test_marks_readers.py` | 15154 | `057f5bd748ed2660` |
| `voidloom/tests/test_marks_repair.py` | 22498 | `25330afae1bf323c` |
| `voidloom/tests/test_marks_schema.py` | 17668 | `0085fb6f93e5a4ea` |
| `voidloom/tests/test_marks_surfaces.py` | 24046 | `dbc3fd31ba290521` |
| `voidloom/tests/test_migration_041_plain_sql_release_r2.py` | 4209 | `fc4edf7319aaea4a` |
| `voidloom/tests/test_migrations_042_045.py` | 12721 | `427326fce122e1d4` |
| `voidloom/tests/test_missing_compute_card_reads_degraded.py` | 6975 | `cb9da9004849e328` |
| `voidloom/tests/test_models_present_is_this_brains.py` | 9433 | `6b0e351cadd27c2b` |
| `voidloom/tests/test_names_confined.py` | 23352 | `b07e843f119382cb` |
| `voidloom/tests/test_no_test_file_is_gitignored.py` | 1809 | `7bd5fc023edd020e` |
| `voidloom/tests/test_nothing_private_left_open.py` | 2630 | `d327239c96c22cfd` |
| `voidloom/tests/test_nothing_re_points_a_brain.py` | 7747 | `3c1186c776e089f6` |
| `voidloom/tests/test_notice_claim_holds.py` | 6688 | `2d70862f629b569a` |
| `voidloom/tests/test_nvidia_smi_off_path.py` | 11289 | `ced91e9e5364c2fa` |
| `voidloom/tests/test_organ_posture.py` | 6220 | `921573bfde2081b3` |
| `voidloom/tests/test_organ_selfchecks.py` | 10952 | `d3a0babc96bc753f` |
| `voidloom/tests/test_organ_yield_exit_codes.py` | 14391 | `82d92c7335c026a2` |
| `voidloom/tests/test_outside_target_diff.py` | 54049 | `1619158bb90391f8` |
| `voidloom/tests/test_persona_forge.py` | 23899 | `38208ba2c18f0d9c` |
| `voidloom/tests/test_policy_decisions_are_shown.py` | 4545 | `cc0370b428d2a210` |
| `voidloom/tests/test_preflight_model_cache_r2.py` | 6695 | `902caf53d37a49df` |
| `voidloom/tests/test_preflight_upgrades_pip_r2.py` | 1738 | `5d0369a1088c1ebe` |
| `voidloom/tests/test_preflight_verb.py` | 2694 | `fab053d8ac241306` |
| `voidloom/tests/test_ps1_lays_a_brain.py` | 5776 | `11f77b5c23f3df5c` |
| `voidloom/tests/test_ps1_precache_by_pin.py` | 6735 | `168f613a0cb0a548` |
| `voidloom/tests/test_ps1_reembed_stamped.py` | 4460 | `571264a55d17f455` |
| `voidloom/tests/test_ps1_refused_brain.py` | 6860 | `eee475850933d762` |
| `voidloom/tests/test_ps1_stops_and_says_why.py` | 9876 | `2c6be7468e2559cf` |
| `voidloom/tests/test_qwen_revision_pinned.py` | 3887 | `ad2c57e0c5c3551a` |
| `voidloom/tests/test_raptor_one_node_per_group.py` | 5604 | `37b166735e6aed33` |
| `voidloom/tests/test_re_embed_prelock.py` | 16147 | `61a6585956c3fe6f` |
| `voidloom/tests/test_read_never_fetches.py` | 7232 | `74534555cb0f5a40` |
| `voidloom/tests/test_readers_age_on_the_brain_clock.py` | 6969 | `18162279373715ad` |
| `voidloom/tests/test_readers_count_the_brains_table.py` | 7747 | `1bec17ce2bc31b16` |
| `voidloom/tests/test_readers_lock_first.py` | 8699 | `2c622570e4347fd4` |
| `voidloom/tests/test_recall_carries_coverage.py` | 11053 | `24d4f4ba01deb761` |
| `voidloom/tests/test_recall_model_refusal.py` | 16383 | `f500425e134b3860` |
| `voidloom/tests/test_recall_topk_after_placement.py` | 3824 | `b697d7b65e0fd52a` |
| `voidloom/tests/test_refusals_name_the_way_out.py` | 37968 | `31098697867fea88` |
| `voidloom/tests/test_release_check_reads_its_own_row.py` | 2414 | `8586fe740f8700fa` |
| `voidloom/tests/test_release_gates_execute.py` | 3803 | `e8ae84f6b38ab25e` |
| `voidloom/tests/test_reranker_gate.py` | 27921 | `77c41f4d1b428239` |
| `voidloom/tests/test_reranker_pin_at_load.py` | 13502 | `1c88778b7c175dc4` |
| `voidloom/tests/test_run_ledger_settles_in_flight_runs.py` | 10364 | `0e4486be39b0300a` |
| `voidloom/tests/test_run_log_follows_the_writer.py` | 4515 | `68ae69676126998d` |
| `voidloom/tests/test_schema_migrator.py` | 8188 | `6aa0410c65f897ea` |
| `voidloom/tests/test_search_set_foreign_stamps.py` | 9430 | `56e9d71dc5da5e88` |
| `voidloom/tests/test_seed_anchor_delta.py` | 19453 | `6b97c55bff94c3ad` |
| `voidloom/tests/test_seed_delete_and_lock_read.py` | 4790 | `ff332b527c29a64e` |
| `voidloom/tests/test_seed_delete_stays_deleted.py` | 2406 | `2b4d6431006cae5d` |
| `voidloom/tests/test_seed_doctrine_and_denylist.py` | 12192 | `aee753312feb6a74` |
| `voidloom/tests/test_seed_load_is_one_transaction.py` | 11452 | `88b9ec8c64b8a500` |
| `voidloom/tests/test_self_check_lockless_never_loads.py` | 4522 | `2bb70a66fa7664af` |
| `voidloom/tests/test_self_check_models_lock.py` | 4278 | `40ce1e1805941fda` |
| `voidloom/tests/test_self_check_recall_gate.py` | 19142 | `fe8da6376a20c5cb` |
| `voidloom/tests/test_self_check_scale.py` | 7970 | `1f90a2e72b949c63` |
| `voidloom/tests/test_self_check_seed_scope_r3.py` | 15061 | `fa7f001f0c98be46` |
| `voidloom/tests/test_self_eval_counts_its_own_fires_d26.py` | 2182 | `21f022651716a9c2` |
| `voidloom/tests/test_sentinel_live_run_r2.py` | 7816 | `1846f74567a4003a` |
| `voidloom/tests/test_sentinel_sleep_runs_r1.py` | 12467 | `76e8e3c731a4ad7d` |
| `voidloom/tests/test_side_stores_load_by_pin.py` | 5211 | `a0463b7abe996243` |
| `voidloom/tests/test_skills_conform.py` | 3810 | `9540370ed866c0a7` |
| `voidloom/tests/test_skip_is_not_success_r3g3.py` | 14612 | `712de358722adec4` |
| `voidloom/tests/test_sleep_catch_up_verb.py` | 3143 | `72a16ab29090aae0` |
| `voidloom/tests/test_sleep_loop_brain_lock_r3.py` | 12800 | `60d19e34fd22831c` |
| `voidloom/tests/test_sleep_loop_unit_tests_r3g3.py` | 2379 | `f9917dcdc6a9b6ac` |
| `voidloom/tests/test_sleep_loop_unstallable_r1.py` | 32083 | `15f584b671fdd571` |
| `voidloom/tests/test_sleep_self_test_offline.py` | 6080 | `cac413a6355b3a7e` |
| `voidloom/tests/test_stamp_weights_hash.py` | 13172 | `ed8e4af2dbfde798` |
| `voidloom/tests/test_status_inspect_export.py` | 22468 | `275efc45aae1efb4` |
| `voidloom/tests/test_stranger_tier1_upgrade.py` | 89205 | `0e0c94faef9a76a9` |
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
| `voidloom/tests/test_thinker_retirements.py` | 15082 | `b0e1129c3dec6856` |
| `voidloom/tests/test_thinker_round2_low_items.py` | 18263 | `d4339cf3c29eeb17` |
| `voidloom/tests/test_thinker_seam_gate.py` | 10470 | `425d70e90f7d4f91` |
| `voidloom/tests/test_thinker_text_posture_max_turns_rN.py` | 8768 | `db67f4527eee0cd6` |
| `voidloom/tests/test_thinker_tool_attempt_rP.py` | 11379 | `2d5d57e5e897f062` |
| `voidloom/tests/test_thinker_typed_errors.py` | 13273 | `e833158af8e4a57a` |
| `voidloom/tests/test_tier0_alone_is_a_brain.py` | 10878 | `3518d31676e81652` |
| `voidloom/tests/test_tier0_carries_coverage.py` | 11804 | `30ffd521f5e17d6a` |
| `voidloom/tests/test_tier0_context.py` | 6213 | `a4d85105badc200f` |
| `voidloom/tests/test_tier0_degrade_on_model_absent.py` | 8394 | `5cc3315ecb4036ef` |
| `voidloom/tests/test_tier0_only_binds_the_runtime.py` | 3394 | `287f82c8bf876711` |
| `voidloom/tests/test_tier1_ambient.py` | 151524 | `7ed8bee5e5ab60d0` |
| `voidloom/tests/test_tier1_route_for_a_brain_with_memories.py` | 15366 | `bed6b6f213abb086` |
| `voidloom/tests/test_tier_set.py` | 30383 | `ac51437602f5b332` |
| `voidloom/tests/test_tier_switch_owns_the_env.py` | 8738 | `2e55055fd5db0f9e` |
| `voidloom/tests/test_tier_verbs_tell_the_whole_story.py` | 9345 | `bac97880aad95ab9` |
| `voidloom/tests/test_unreadable_lockless_brain_says_tier_unresolved.py` | 4305 | `fdf502d7c7bd9cb0` |
| `voidloom/tests/test_upgrade_embeds_nothing_unasked.py` | 5578 | `f4c7665cc113f5f2` |
| `voidloom/tests/test_voidloom.py` | 38004 | `608d9b53b234fc5e` |
| `voidloom/tests/test_wall_dedup_unreadable.py` | 6863 | `6dad234ff493669a` |
| `voidloom/tests/test_wall_derived_inherit.py` | 17563 | `2499082dd5355978` |
| `voidloom/tests/test_wall_dispatch.py` | 9907 | `5045b99286db7444` |
| `voidloom/tests/test_wall_forget.py` | 4522 | `d431cbb3f03d5970` |
| `voidloom/tests/test_wall_framing.py` | 11758 | `a61ae539b4ddf326` |
| `voidloom/tests/test_wall_no_query_text.py` | 10515 | `552587dcb1d99419` |
| `voidloom/tests/test_wall_race.py` | 4796 | `e476f5f3b6674721` |
| `voidloom/tests/test_wall_store_marks.py` | 16169 | `7b4a40472ef650ee` |
| `voidloom/tests/test_website_store_stamps.py` | 8758 | `177dee58bdbab780` |
| `voidloom/tests/test_windows_scripts_encoding.py` | 3585 | `7407c0787dd07272` |
| `voidloom/tests/test_writers_stamp_utc.py` | 6404 | `7c096f5dd2e0ed3a` |
| `voidloom/voidloom` | 3353 | `3c657f4401579380` |
| `voidloom/voidloom.cmd` | 2145 | `cbb49bf1a325035a` |

### `migrations` — Schema + additive migrations  ·  41 files, 190968 bytes

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
| `migrations/migration_013_embedding_upgrade.sql` | 1485 | `b824a6857f13c19d` |
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
| `migrations/migration_042_embedder_lock.sql` | 6491 | `8605e858ef34af93` |
| `migrations/migration_043_retire_encumbered_default.sql` | 6161 | `28dcb0834effc4aa` |
| `migrations/migration_044_seed_origin.sql` | 1506 | `0bc5ba92fcff4392` |
| `migrations/migration_045_derived_layer_stamp.sql` | 1317 | `e3c2a801859f297d` |
| `migrations/migration_046_marks.sql` | 28124 | `92ca1c466d0e4488` |
| `migrations/migration_047_brain_label.sql` | 1850 | `acead59c0af7f4ac` |
| `migrations/schema.sql` | 2640 | `1413cd5027588589` |

### `install` — Installers, wiring, acceptance gate, pinned requirements  ·  24 files, 1020697 bytes

| file | bytes | sha256 |
|---|---:|---|
| `install/CLAUDE.md.template` | 6780 | `a44033e0a1480fcb` |
| `install/accelerator_plan.py` | 29139 | `5aed96c2e335eb03` |
| `install/brain_is_new.py` | 6154 | `c455dcaa9283c525` |
| `install/cron_roster.json` | 14591 | `212dbb7c34d14485` |
| `install/cron_roster.py` | 16531 | `1f34cca9a2d7b089` |
| `install/env_file.py` | 102683 | `cb0fe029b977aa64` |
| `install/fixtures/golden_vectors_nomic.json` | 144295 | `7ca287b27b7b7b91` |
| `install/fixtures/golden_vectors_qwen.json` | 191567 | `4e9f3a72527ad86f` |
| `install/golden_vectors.py` | 11126 | `e7ac4a6bfc379846` |
| `install/install.ps1` | 74957 | `38dcffc2f722eb43` |
| `install/install_council.sh` | 9233 | `7efe5c588226bf00` |
| `install/install_crons.sh` | 29886 | `083aef2869e64648` |
| `install/install_persona_forge.sh` | 6387 | `fe9730cd877a6a74` |
| `install/lay.py` | 17458 | `90cf7eb13d48e731` |
| `install/lay_manifest.json` | 157951 | `4c092556925f08d1` |
| `install/merge_config.py` | 4601 | `8fbef13d93358b0f` |
| `install/preflight.py` | 15888 | `878d5abbb01219e3` |
| `install/requirements.hashes.txt` | 1742 | `ca1b6241e2a83b32` |
| `install/requirements.txt` | 9850 | `17aa573d4ee42fa1` |
| `install/self_check.py` | 70003 | `f483c051efda2fec` |
| `install/tier1_probe.py` | 46112 | `19ace7b49506b4f8` |
| `install/wire_persona.ps1` | 8385 | `6c760a0105833234` |
| `install/wire_persona.sh` | 10855 | `0896d140f773a1bc` |
| `install/write_set.json` | 34523 | `c43f58b72163f466` |

### `seed` — Starter memories + schema-only blank DB  ·  4 files, 742004 bytes

| file | bytes | sha256 |
|---|---:|---|
| `seed/claude_memory_blank.db` | 573440 | `f1e15db427fa7a56` |
| `seed/seed_denylist.json` | 2086 | `df402528c4e6477b` |
| `seed/seed_doctrine_queries.json` | 9963 | `fd82ba55ad934f7c` |
| `seed/seed_pack.json` | 156515 | `49c30cd2cd813e3c` |

### `hooks` — Host session bindings  ·  4 files, 52858 bytes

| file | bytes | sha256 |
|---|---:|---|
| `hooks/session_start_inject.py` | 24929 | `18c5c41e34d952d6` |
| `hooks/voidloom_notify.py` | 19424 | `610f1b134d66565e` |
| `hooks/voidloom_precompact.py` | 7027 | `da67266abbf2ac5e` |
| `hooks/voidloom_signoff.py` | 1478 | `f5b48e7f42b92483` |

### `cron` — systemd units + timers  ·  48 files, 81456 bytes

| file | bytes | sha256 |
|---|---:|---|
| `cron/_config_dir.sh` | 854 | `d33909719f942d80` |
| `cron/platform-detection.sh` | 28070 | `1b642e53ab039a3b` |
| `cron/setup-anti-recency-cron.sh` | 1708 | `bf84f24f3816c4ac` |
| `cron/setup-arcane-manifest-cron.sh` | 1741 | `2b1958443374cecc` |
| `cron/setup-counterfactual-revisit-cron.sh` | 3676 | `f5474b3c5d528a93` |
| `cron/setup-dreaming-cron.sh` | 4241 | `a8b8ffb4eaa5d94d` |
| `cron/setup-gap-mapping-cron.sh` | 2919 | `1ff7cbd4c5ca5d95` |
| `cron/setup-self-eval-cron.sh` | 2933 | `d5c001bfcc97f37a` |
| `cron/setup-skill-graduation-cron.sh` | 2841 | `5472320f0fc870e4` |
| `cron/setup-sleep-loop-cron.sh` | 3447 | `61e56e682c75508e` |
| `cron/setup-whisper-digest-cron.sh` | 2084 | `db1de528d05a4dfd` |
| `cron/sleep-loop-config.json` | 467 | `c4631abd4b7d6b0c` |
| `cron/wizard-library-affect-probe.service` | 921 | `ca258075060ecb36` |
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
| `cron/wizard-library-dream-daytime.service` | 1393 | `829d86e0e0eb2534` |
| `cron/wizard-library-dream-daytime.timer` | 292 | `19c35eac409e2384` |
| `cron/wizard-library-dreaming.service` | 1838 | `b161c4195d66700d` |
| `cron/wizard-library-dreaming.timer` | 422 | `a97b9976bb527526` |
| `cron/wizard-library-embed-backfill.service` | 1516 | `471787c3dfcccf69` |
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
| `cron/wizard-library-pre-cognitive.timer` | 910 | `19cd056236209fb4` |
| `cron/wizard-library-self-eval.service` | 594 | `134ffb07d8f87b03` |
| `cron/wizard-library-self-eval.timer` | 386 | `82bc2f78cf6dbefb` |
| `cron/wizard-library-skill-graduation.service` | 672 | `d64fe7ec0f2fe9b9` |
| `cron/wizard-library-skill-graduation.timer` | 387 | `c73f555d2f56ca5a` |
| `cron/wizard-library-sleep-loop.service` | 956 | `d34aa8f67a2b2fa5` |
| `cron/wizard-library-sleep-loop.timer` | 371 | `cd4b3032c7e98185` |
| `cron/wizard-library-whisper-digest.service` | 656 | `464dc44376c93acf` |
| `cron/wizard-library-whisper-digest.timer` | 375 | `8c13e65d6e5b42ff` |

### `capability` — Host capability detection  ·  2 files, 41461 bytes

| file | bytes | sha256 |
|---|---:|---|
| `capability/detect.py` | 37930 | `2ae313a71674c0b3` |
| `capability/detect_embed_model.sh` | 3531 | `280d2751714b3675` |

### `persona` — Persona skills, council voices, slash commands  ·  21 files, 90651 bytes

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
| `persona/skills/memory-operator/SKILL.md` | 2649 | `13bf697b3e52f8e4` |
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

### `tools` — Maintainer-only: package gates + recall harness  ·  23 files, 247840 bytes

| file | bytes | sha256 |
|---|---:|---|
| `tools/build_compat.py` | 13035 | `2865e1f90180ce8a` |
| `tools/build_embedder_catalogue.py` | 9845 | `ea7b807681211a86` |
| `tools/build_manifest.py` | 23302 | `ecb59dc20cd105f3` |
| `tools/cross_platform_audit.py` | 3990 | `a9a9306b7de01aa6` |
| `tools/gen_cron_audit.py` | 4882 | `87ffc4f49af6183b` |
| `tools/git-hooks/install.sh` | 1636 | `8f082db0063b91c6` |
| `tools/git-hooks/pre-commit` | 444 | `a7f0a54ab05bbcc4` |
| `tools/git-hooks/pre-push` | 1480 | `003a4705fe84f598` |
| `tools/gpu_pin_gate.py` | 44906 | `c329794bee05d7f9` |
| `tools/harness/assertions.sh` | 6580 | `de450faa0d9e070a` |
| `tools/harness/check_must_retrieve.py` | 3545 | `f226112171ca8ed3` |
| `tools/harness/compare_recall.py` | 11022 | `67cc4326085289f2` |
| `tools/harness/corpus_presence.py` | 14697 | `378f9b94bfb12f27` |
| `tools/harness/eval_queries.txt` | 1642 | `0bfaf4689322185c` |
| `tools/harness/eval_recall.sh` | 13367 | `999e25c5c8f665b3` |
| `tools/harness/must_retrieve.json` | 495 | `a7bba254e1b6aebb` |
| `tools/harness/no_regression_analysis.md` | 6918 | `e100f1bbd4e2c9a2` |
| `tools/harness/run_gate.sh` | 6971 | `e857bb0e5b919ead` |
| `tools/harness/test_harness.py` | 17074 | `bb171866b5a5f1ff` |
| `tools/leak_gate.py` | 15091 | `c5aae0965ffa23e7` |
| `tools/pii_gate.py` | 27217 | `e36321c4c7ee2874` |
| `tools/release_check.py` | 8923 | `c100a51e03cfd409` |
| `tools/token_doctor.py` | 10778 | `4db1a76ccd8ae406` |

### `docs` — Documentation  ·  12 files, 230703 bytes

| file | bytes | sha256 |
|---|---:|---|
| `docs/COUNCIL_README.md` | 5392 | `58e0cca8f3bbeb74` |
| `docs/FIRST_NIGHT.md` | 17175 | `7c84a368b69f5980` |
| `docs/GETTING_STARTED.md` | 19765 | `f22116939a3af066` |
| `docs/INSTRUCTIONS.md` | 4513 | `dc86c655f4b97c83` |
| `docs/LAYOUT.md` | 8372 | `8ad33cc9a9c1a824` |
| `docs/ONBOARDING_GUIDE.md` | 57944 | `1d874da2fc948b57` |
| `docs/OVERNIGHT_THINKER_SETUP.md` | 32164 | `520a6cb49c7aa880` |
| `docs/PACKAGE_MANIFEST.md` | 44404 | (self -- not pinnable) |
| `docs/PERSONA_FORGE.md` | 12359 | `ddff9923016d7145` |
| `docs/RESTORE_MANIFEST.md` | 9212 | `af520a9df938cc56` |
| `docs/gates/CORPUS_INDEX.md` | 7253 | `593555d97bc3cf5a` |
| `docs/gates/GATE_v1.md` | 12150 | `729190c119096cd6` |

### `(root)` — Front door: README, licence, installer, manifest  ·  11 files, 506634 bytes

| file | bytes | sha256 |
|---|---:|---|
| `.gitignore` | 6351 | `921af78d4a45fc62` |
| `CHANGELOG.md` | 64214 | `5976de35a313b1cd` |
| `CODE_OF_CONDUCT.md` | 837 | `d8a208bd8c0f35ff` |
| `COMPAT.json` | 3122 | `eeea66a770f66396` |
| `CONTRIBUTING.md` | 7188 | `1afdadec1fd60f91` |
| `LICENSE` | 11362 | `73c162a8c72b5ed4` |
| `NOTICE` | 4988 | `7e9ae36efd316871` |
| `README.md` | 31704 | `14c1ff0bef32e20d` |
| `SECURITY.md` | 3320 | `e5ee56ddf42bce19` |
| `install.sh` | 363017 | `3e6816b94ee6869f` |
| `uninstall.sh` | 10531 | `2b6b2ab4e02a4c49` |

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
