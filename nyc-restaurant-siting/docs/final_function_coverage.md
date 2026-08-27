# Final function coverage — 2026-08-27

Every function the product defines, and what is actually known about each
one. The classification is **measured**, not asserted: the execution set
comes from running the whole suite under a `sys.setprofile` hook that
records every function entered, and the reachability set comes from a
call-site scan over the repository. Where neither could answer, the row
says `NOT VERIFIED` and names what was missing.

## How this was measured

**Execution.** No coverage package is installed and this audit may not add
dependencies, so coverage was taken with `sys.setprofile` plus
`threading.setprofile`. The second one matters: Streamlit's `AppTest` runs
the app script in a **worker thread**, so a main-thread-only profiler sees
none of `app.py` and would have reported the entire UI as unreached. The
hook records `(file, function name, first line)` for every call whose code
object lives in `app.py`, `build_data.py`, or `nycsiting/`.

**Inventory.** The denominator is parsed from the source with `ast`, so it
counts nested functions, callbacks and methods separately — a callback that
is never entered is exactly the kind of gap this document exists to find.

**Reachability.** Every function the profiler did not see was then searched
for across the repository. A function whose only occurrence is its own
`def` line is unreachable; one with a live call site is reachable but
untested, which is a different fact and is recorded differently.

**Scope.** 378 functions across `app.py` (148), `build_data.py` (1) and the
29 `nycsiting/` modules (229). The three developer utilities in `scripts/`
(9 further functions) are not part of the shipped product and are excluded;
`tests/` is the instrument, not the subject.

## What the verdicts mean

| Verdict | Meaning |
|---|---|
| **PASS** | Entered during the 680-test suite, or driven through the live UI in this session's browser pass, with no failure attributable to it. |
| **PASS WITH LIMITATION** | Correct as far as it was exercised, but not on the default user path — build-time only, or behind the `ENABLE_SIMULATION` flag. The limitation is named in every row. |
| **NOT VERIFIED** | Reachable in the product, but this audit did not enter it. The row says which state was missing. Not a claim that it is broken, and not a claim that it works. |
| **NOT REACHABLE** | No call site anywhere in the product. Dead code. |
| **FAIL** | Entered and observed to misbehave. |

## Two things this document does not claim

**Coverage is not correctness.** "Entered at least once" is a weak
property. A `PASS` here means the function ran under test without failing,
not that its behaviour is fully specified. The analytical claims — cohort
survival, Wilson gating, the six scoring components, ACS and PLUTO joins —
are validated by the dedicated documents in this directory, not by this one.

**The suite is larger than the shipped surface.** 57 tests
(`test_financial_simulation.py` 20, `test_financial_v2.py` 28,
`test_sim_animation.py` 9) plus four cases in `test_app_integration.py`
exercise the financial-simulation subsystem, which `simulation_enabled()`
keeps behind a deployer flag. That code is real and tested; no user reaches
it in the default configuration. Read the 680-test total with that in mind.

## Summary

| Verdict | Functions | Share |
|---|---:|---:|
| PASS | 320 | 84.7% |
| PASS WITH LIMITATION | 36 | 9.5% |
| NOT VERIFIED | 11 | 2.9% |
| NOT REACHABLE | 11 | 2.9% |
| FAIL | 0 | 0.0% |
| **Total** | **378** | **100%** |

## Per-module counts

| Module | Total | PASS | PASS W/ LIMIT | NOT VERIFIED | NOT REACHABLE |
|---|---:|---:|---:|---:|---:|
| `app.py` | 148 | 131 | 3 | 8 | 6 |
| `build_data.py` | 1 | 0 | 1 | 0 | 0 |
| `nycsiting/acs.py` | 11 | 10 | 1 | 0 | 0 |
| `nycsiting/analysis.py` | 4 | 4 | 0 | 0 | 0 |
| `nycsiting/areas.py` | 12 | 12 | 0 | 0 | 0 |
| `nycsiting/branding.py` | 11 | 11 | 0 | 0 | 0 |
| `nycsiting/comparison.py` | 6 | 6 | 0 | 0 | 0 |
| `nycsiting/context.py` | 8 | 5 | 1 | 2 | 0 |
| `nycsiting/cuisines.py` | 3 | 3 | 0 | 0 | 0 |
| `nycsiting/financial_simulation.py` | 19 | 0 | 19 | 0 | 0 |
| `nycsiting/geo.py` | 2 | 2 | 0 | 0 | 0 |
| `nycsiting/geocode.py` | 5 | 5 | 0 | 0 | 0 |
| `nycsiting/geometry.py` | 9 | 9 | 0 | 0 | 0 |
| `nycsiting/google_places.py` | 14 | 13 | 0 | 1 | 0 |
| `nycsiting/locations.py` | 3 | 1 | 1 | 0 | 1 |
| `nycsiting/mapview.py` | 8 | 8 | 0 | 0 | 0 |
| `nycsiting/narrative.py` | 13 | 13 | 0 | 0 | 0 |
| `nycsiting/normalize.py` | 7 | 7 | 0 | 0 | 0 |
| `nycsiting/nta.py` | 7 | 7 | 0 | 0 | 0 |
| `nycsiting/panel.py` | 7 | 1 | 6 | 0 | 0 |
| `nycsiting/pedestrian_dot.py` | 13 | 13 | 0 | 0 | 0 |
| `nycsiting/plan_parser.py` | 14 | 14 | 0 | 0 | 0 |
| `nycsiting/report_pdf.py` | 7 | 7 | 0 | 0 | 0 |
| `nycsiting/report_writer.py` | 2 | 2 | 0 | 0 | 0 |
| `nycsiting/scoring.py` | 6 | 6 | 0 | 0 | 0 |
| `nycsiting/sim_animation.py` | 4 | 0 | 4 | 0 | 0 |
| `nycsiting/stats.py` | 2 | 2 | 0 | 0 | 0 |
| `nycsiting/ui.py` | 16 | 14 | 0 | 0 | 2 |
| `nycsiting/workspace_map.py` | 16 | 14 | 0 | 0 | 2 |

## Every function that is not a plain PASS

Listed first because these are the ones a release decision turns on.

| Function | Line | Verdict | Evidence / why |
|---|---:|---|---|
| `app.py` · `_change_concept` | 1157 | NOT VERIFIED | needs the landing-page concept editor |
| `app.py` · `render_trace` | 1181 | NOT VERIFIED | developer trace checkbox only |
| `app.py` · `_tercile_band` | 3032 | NOT VERIFIED | ACS income/density banding branch |
| `app.py` · `_pick_search_choice` | 3654 | NOT VERIFIED | needs an ambiguous geocode result |
| `app.py` · `_abandon_address` | 3702 | NOT VERIFIED | needs a geocode failure |
| `app.py` · `render_address_failure` | 3710 | NOT VERIFIED | needs a geocode failure |
| `app.py` · `_view_containing_area` | 4358 | NOT VERIFIED | site-mode 'View full area analysis' |
| `app.py` · `_to_workspace` | 4772 | NOT VERIFIED | landing-page Workspace shortcut |
| `nycsiting/context.py` · `load_pedestrian` | 92 | NOT VERIFIED | DOT pedestrian loader |
| `nycsiting/context.py` · `load_pedestrian > sort_key` | 111 | NOT VERIFIED | nested in load_pedestrian |
| `nycsiting/google_places.py` · `class CompetitorLandscape > moderate` | 147 | NOT VERIFIED | CompetitorLandscape property |
| `app.py` · `render_competition` | 629 | NOT REACHABLE | no call site in the product |
| `app.py` · `_an` | 925 | NOT REACHABLE | no call site in the product |
| `app.py` · `render_context_bar` | 930 | NOT REACHABLE | no call site in the product |
| `app.py` · `render_hero` | 948 | NOT REACHABLE | no call site in the product |
| `app.py` · `panel_for_compare` | 1132 | NOT REACHABLE | no call site in the product |
| `app.py` · `neighborhood_to_nta` | 2042 | NOT REACHABLE | no call site in the product |
| `nycsiting/locations.py` · `occupancy_history` | 110 | NOT REACHABLE | no call site in the product |
| `nycsiting/ui.py` · `query_context` | 140 | NOT REACHABLE | no call site in the product |
| `nycsiting/ui.py` · `decision_hero` | 149 | NOT REACHABLE | no call site in the product |
| `nycsiting/workspace_map.py` · `add_nta_boundaries` | 186 | NOT REACHABLE | no call site in the product |
| `nycsiting/workspace_map.py` · `legend_for` | 497 | NOT REACHABLE | no call site in the product |
| `app.py` · `render_sim_inputs` | 1288 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `app.py` · `render_sim_results` | 1438 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `app.py` · `simulate_page` | 1770 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `build_data.py` · `main` | 20 | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `nycsiting/acs.py` · `save_cache` | 161 | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `nycsiting/context.py` · `load_pluto_lots` | 40 | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `nycsiting/financial_simulation.py` · `validate_simulation_inputs` | 116 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `calculate_daily_capacity` | 180 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `calculate_monthly_operations` | 188 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `build_simulation_dataframe` | 213 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `calculate_break_even` | 286 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `calculate_roi` | 292 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `summarise_scenario` | 300 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `calculate_all_scenarios` | 316 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `calculate_sensitivity` | 326 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `total_startup_investment` | 400 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `owner_equity` | 408 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `validate_financing` | 415 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `amortization_schedule` | 435 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `extend_with_financing` | 461 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `calculate_payback_month` | 497 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `operating_break_even` | 520 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `roi_summary` | 544 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `footfall_scenario_covers` | 603 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/financial_simulation.py` · `calculate_all_scenarios_v2` | 624 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/locations.py` · `build_locations` | 56 | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `nycsiting/panel.py` · `_read` | 65 | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `nycsiting/panel.py` · `_aggregate` | 73 | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `nycsiting/panel.py` · `_identity` | 87 | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `nycsiting/panel.py` · `build_restaurants` | 96 | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `nycsiting/panel.py` · `build_location_index` | 189 | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `nycsiting/panel.py` · `attach_coordinates` | 243 | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `nycsiting/sim_animation.py` · `table_layout` | 27 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/sim_animation.py` · `occupied_count` | 46 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/sim_animation.py` · `frame_payload` | 58 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `nycsiting/sim_animation.py` · `build_animation_html` | 77 | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |

## Full inventory


### `app.py` — 148 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `load_panel` | 45 | function | PASS | suite |
| `load_locations` | 53 | function | PASS | suite |
| `load_lots` | 58 | function | PASS | suite |
| `load_pedestrian` | 63 | function | PASS | suite |
| `cuisine_options` | 68 | function | PASS | suite |
| `geocode_cached` | 74 | function | PASS | suite |
| `score_cached` | 79 | function | PASS | suite |
| `get_anthropic_api_key` | 89 | function | PASS | suite |
| `parse_plan_cached` | 104 | function | PASS | suite |
| `simulation_enabled` | 118 | function | PASS | suite |
| `google_api_key` | 133 | function | PASS | suite |
| `competitors_cached` | 148 | function | PASS | suite |
| `nyc_token` | 160 | function | PASS | suite |
| `pedestrian_cached` | 170 | function | PASS | suite |
| `load_acs` | 176 | function | PASS | suite |
| `load_tract_nta` | 182 | function | PASS | suite |
| `site_tract_cached` | 187 | function | PASS | suite |
| `nta_index` | 194 | function | PASS | suite |
| `nta_geojson` | 199 | function | PASS | suite |
| `nta_display_geometry` | 213 | function | PASS | suite |
| `nta_names` | 261 | function | PASS | suite |
| `nta_assignment` | 266 | function | PASS | suite |
| `area_features_cached` | 271 | function | PASS | suite |
| `panel_with_nta_cached` | 276 | function | PASS | suite |
| `concept_fit_cached` | 298 | function | PASS | suite |
| `conceptfree_fit_cached` | 307 | function | PASS | suite |
| `density_cached` | 339 | function | PASS | suite |
| `turnover_cached` | 347 | function | PASS | suite |
| `acs_by_nta_cached` | 352 | function | PASS | suite |
| `evidence_cached` | 363 | function | PASS | suite |
| `concept_candidates_cached` | 369 | function | PASS | suite |
| `concept_ranking_cached` | 377 | function | PASS | suite |
| `area_name_lexicon` | 404 | function | PASS | suite |
| `resolve_area_candidates` | 421 | function | PASS | suite |
| `ped_sites_by_nta_cached` | 439 | function | PASS | suite |
| `area_ped_context` | 450 | function | PASS | suite |
| `active_theme` | 465 | function | PASS | suite |
| `resolve_location_key` | 491 | function | PASS | suite |
| `_occupancy_gantt` | 502 | function | PASS | suite |
| `render_history` | 549 | function | PASS | suite |
| `render_cuisine` | 594 | function | PASS | suite |
| `render_competition` | 629 | function | NOT REACHABLE | no call site in the product |
| `render_context` | 675 | function | PASS | suite |
| `render_google` | 713 | function | PASS | suite |
| `landing_page` | 856 | function | PASS | suite |
| `_an` | 925 | function | NOT REACHABLE | no call site in the product |
| `render_context_bar` | 930 | function | NOT REACHABLE | no call site in the product |
| `render_hero` | 948 | function | NOT REACHABLE | no call site in the product |
| `_short_stat` | 957 | function | PASS | suite |
| `render_why` | 998 | function | PASS | suite |
| `_fmt_compact` | 1028 | function | PASS | suite |
| `render_market` | 1040 | function | PASS | suite |
| `render_market > value_of` | 1046 | nested | PASS | suite |
| `render_recommendation` | 1096 | function | PASS | suite |
| `render_limitations` | 1110 | function | PASS | suite |
| `panel_for_compare` | 1132 | function | NOT REACHABLE | no call site in the product |
| `_compare_another` | 1136 | function | PASS | suite |
| `_change_concept` | 1157 | function | NOT VERIFIED | needs the landing-page concept editor |
| `render_next` | 1164 | function | PASS | suite |
| `render_trace` | 1181 | function | NOT VERIFIED | developer trace checkbox only |
| `render_methodology` | 1221 | function | PASS | suite |
| `render_sim_inputs` | 1288 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `_money` | 1428 | function | PASS | suite |
| `_be_text` | 1432 | function | PASS | suite |
| `render_sim_results` | 1438 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `render_sim_results > fmt_side` | 1643 | nested | PASS | suite |
| `simulate_page` | 1770 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `record_filter_explanation` | 1925 | function | PASS | suite |
| `render_filter_explanation` | 1954 | function | PASS | suite |
| `filter_caption` | 1964 | function | PASS | suite |
| `layer_choices_for` | 1989 | function | PASS | suite |
| `concept_token` | 2013 | function | PASS | suite |
| `_hover_frame` | 2024 | function | PASS | suite |
| `zip_to_nta` | 2030 | function | PASS | suite |
| `neighborhood_to_nta` | 2042 | function | NOT REACHABLE | no call site in the product |
| `polygon_bounds` | 2052 | function | PASS | suite |
| `zoom_for_bounds` | 2100 | function | PASS | suite |
| `request_loading` | 2128 | function | PASS | suite |
| `site_zoom` | 2163 | function | PASS | suite |
| `select_area` | 2212 | function | PASS | suite |
| `area_tiers_cached` | 2255 | function | PASS | suite |
| `area_restaurant_tiers` | 2264 | function | PASS | suite |
| `site_tiers_cached` | 2275 | function | PASS | suite |
| `site_restaurant_tiers` | 2289 | function | PASS | suite |
| `restaurant_tiers` | 2311 | function | PASS | suite |
| `area_restaurants` | 2389 | function | PASS | suite |
| `_apply_map_selection` | 2397 | function | PASS | suite |
| `render_workspace_toolbar` | 2434 | function | PASS | suite |
| `render_map_workspace` | 2519 | function | PASS | suite |
| `_layer_figure` | 2733 | function | PASS | suite |
| `site_area_context` | 2818 | function | PASS | suite |
| `plan_chip_values` | 2894 | function | PASS | suite |
| `plan_chip_values > add` | 2909 | nested | PASS | suite |
| `plan_chip_labels` | 2945 | function | PASS | suite |
| `remove_plan_constraint` | 2952 | function | PASS | suite |
| `route_plan` | 3001 | function | PASS | suite |
| `_tercile_band` | 3032 | function | NOT VERIFIED | ACS income/density banding branch |
| `income_percentile_cached` | 3040 | function | PASS | suite |
| `preference_alignment` | 3052 | function | PASS | suite |
| `preference_alignment > level_vs_band` | 3065 | nested | PASS | suite |
| `render_priorities` | 3131 | function | PASS | suite |
| `render_plan_usage` | 3161 | function | PASS | suite |
| `_label_artifact` | 3182 | function | PASS | suite |
| `concept_reason` | 3195 | function | PASS | suite |
| `render_concept_rows` | 3212 | function | PASS | suite |
| `google_concept_query` | 3287 | function | PASS | suite |
| `area_bundle_cached` | 3295 | function | PASS | suite |
| `build_comparison_payload` | 3364 | function | PASS | suite |
| `narrative_cached` | 3440 | function | PASS | live browser pass |
| `comparison_entries` | 3476 | function | PASS | suite |
| `comparison_area_codes` | 3485 | function | PASS | suite |
| `make_area_entry` | 3491 | function | PASS | suite |
| `make_site_entry` | 3500 | function | PASS | suite |
| `add_to_comparison` | 3518 | function | PASS | suite |
| `remove_from_comparison` | 3552 | function | PASS | live browser pass |
| `clear_comparison` | 3558 | function | PASS | live browser pass |
| `invalidate_comparison_report` | 3565 | function | PASS | suite |
| `_open_comparison` | 3571 | function | PASS | live browser pass |
| `render_compare_tray` | 3576 | function | PASS | suite |
| `_submit_workspace_search` | 3611 | function | PASS | live browser pass |
| `_pick_search_choice` | 3654 | function | NOT VERIFIED | needs an ambiguous geocode result |
| `render_workspace_search` | 3662 | function | PASS | suite |
| `_abandon_address` | 3702 | function | NOT VERIFIED | needs a geocode failure |
| `render_address_failure` | 3710 | function | NOT VERIFIED | needs a geocode failure |
| `area_is_subject` | 3733 | function | PASS | suite |
| `current_comparison_subject` | 3747 | function | PASS | suite |
| `_handle_add` | 3771 | function | PASS | suite |
| `render_add_to_comparison` | 3780 | function | PASS | suite |
| `render_compare_view` | 3817 | function | PASS | suite |
| `data_version` | 3931 | function | PASS | suite |
| `report_signature` | 3937 | function | PASS | suite |
| `_request_report` | 3947 | function | PASS | live browser pass |
| `render_report_control` | 3955 | function | PASS | suite |
| `confirm_page` | 4005 | function | PASS | suite |
| `render_method_page` | 4223 | function | PASS | suite |
| `_view_containing_area` | 4358 | function | NOT VERIFIED | site-mode 'View full area analysis' |
| `render_area_context` | 4371 | function | PASS | suite |
| `render_site_panel` | 4424 | function | PASS | suite |
| `render_analyze_site_cta` | 4552 | function | PASS | suite |
| `render_area_explorer` | 4577 | function | PASS | suite |
| `render_restaurant_card` | 4714 | function | PASS | live browser pass |
| `_set_view` | 4755 | function | PASS | suite |
| `_reset_search` | 4759 | function | PASS | suite |
| `_to_workspace` | 4772 | function | NOT VERIFIED | landing-page Workspace shortcut |
| `render_top_header` | 4777 | function | PASS | suite |
| `render_workspace_nav` | 4804 | function | PASS | suite |
| `main` | 4837 | function | PASS | suite |
| `_main_body` | 4857 | function | PASS | suite |

### `build_data.py` — 1 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `main` | 20 | function | PASS WITH LIMITATION | build-time only (`python build_data.py`) |

### `nycsiting/acs.py` — 11 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `class CensusKeyMissing > __init__` | 84 | method | PASS | suite |
| `fetch_county` | 96 | function | PASS | suite |
| `parse_payload` | 115 | function | PASS | suite |
| `fetch_all_nyc` | 149 | function | PASS | suite |
| `save_cache` | 161 | function | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `load_cache` | 168 | function | PASS | suite |
| `normalize_tract_code` | 179 | function | PASS | suite |
| `tract_geoid_for` | 210 | function | PASS | suite |
| `tract_geoid_from_point` | 222 | function | PASS | suite |
| `site_tract_geoid` | 244 | function | PASS | suite |
| `tract_percentiles` | 282 | function | PASS | suite |

### `nycsiting/analysis.py` — 4 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `cohort_survival` | 17 | function | PASS | suite |
| `_rate` | 29 | function | PASS | suite |
| `site_report` | 33 | function | PASS | suite |
| `_comparisons` | 120 | function | PASS | suite |

### `nycsiting/areas.py` — 12 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `area_features` | 56 | function | PASS | suite |
| `cuisine_area_table` | 80 | function | PASS | suite |
| `_city_baseline` | 96 | function | PASS | suite |
| `area_concept_fit` | 105 | function | PASS | suite |
| `restaurant_density_by_cuisine` | 146 | function | PASS | suite |
| `competitor_saturation` | 162 | function | PASS | suite |
| `opportunity_gap` | 189 | function | PASS | suite |
| `area_turnover_context` | 216 | function | PASS | suite |
| `evidence_quality_by_area` | 241 | function | PASS | suite |
| `rank_concepts_for_area` | 264 | function | PASS | suite |
| `compare_concepts` | 290 | function | PASS | suite |
| `compare_locations` | 318 | function | PASS | suite |

### `nycsiting/branding.py` — 11 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `logo_data_uri` | 35 | function | PASS | suite |
| `logo_img` | 59 | function | PASS | suite |
| `overlay_css` | 102 | function | PASS | suite |
| `overlay_html` | 167 | function | PASS | suite |
| `global_loader` | 176 | function | PASS | suite |
| `global_loader > dismiss` | 201 | nested | PASS | suite |
| `spinner_css` | 229 | function | PASS | suite |
| `map_ground_css` | 277 | function | PASS | suite |
| `is_cold` | 322 | function | PASS | suite |
| `chair_spinner` | 338 | function | PASS | suite |
| `loader_html` | 359 | function | PASS | suite |

### `nycsiting/comparison.py` — 6 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `derive_area_pros` | 100 | function | PASS | suite |
| `derive_area_cons` | 141 | function | PASS | suite |
| `_fmt_fit` | 182 | function | PASS | suite |
| `derive_risk_matrix` | 189 | function | PASS | suite |
| `comparison_summary` | 264 | function | PASS | suite |
| `deterministic_summary` | 317 | function | PASS | suite |

### `nycsiting/context.py` — 8 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `load_pluto_lots` | 40 | function | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `lot_context` | 51 | function | PASS | suite |
| `_int` | 74 | function | PASS | suite |
| `_num` | 81 | function | PASS | suite |
| `load_pedestrian` | 92 | function | NOT VERIFIED | DOT pedestrian loader |
| `load_pedestrian > sort_key` | 111 | nested | NOT VERIFIED | nested in load_pedestrian |
| `pretty_period` | 136 | function | PASS | suite |
| `nearest_pedestrian` | 145 | function | PASS | suite |

### `nycsiting/cuisines.py` — 3 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `clean_label` | 116 | function | PASS | suite |
| `competitive_set` | 128 | function | PASS | suite |
| `resolve` | 138 | function | PASS | suite |

### `nycsiting/financial_simulation.py` — 19 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `validate_simulation_inputs` | 116 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `calculate_daily_capacity` | 180 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `calculate_monthly_operations` | 188 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `build_simulation_dataframe` | 213 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `calculate_break_even` | 286 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `calculate_roi` | 292 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `summarise_scenario` | 300 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `calculate_all_scenarios` | 316 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `calculate_sensitivity` | 326 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `total_startup_investment` | 400 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `owner_equity` | 408 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `validate_financing` | 415 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `amortization_schedule` | 435 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `extend_with_financing` | 461 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `calculate_payback_month` | 497 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `operating_break_even` | 520 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `roi_summary` | 544 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `footfall_scenario_covers` | 603 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `calculate_all_scenarios_v2` | 624 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |

### `nycsiting/geo.py` — 2 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `haversine_m` | 10 | function | PASS | suite |
| `within_radius` | 18 | function | PASS | suite |

### `nycsiting/geocode.py` — 5 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `has_house_number` | 19 | function | PASS | suite |
| `split_query` | 23 | function | PASS | suite |
| `_street_name_tokens` | 36 | function | PASS | suite |
| `_mismatch` | 55 | function | PASS | suite |
| `geocode` | 90 | function | PASS | suite |

### `nycsiting/geometry.py` — 9 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `parse_wkt_multipolygon` | 30 | function | PASS | suite |
| `_split_level` | 56 | function | PASS | suite |
| `point_in_ring` | 72 | function | PASS | suite |
| `point_in_multipolygon` | 88 | function | PASS | suite |
| `simplify_ring` | 103 | function | PASS | suite |
| `class NTAIndex > __init__` | 126 | method | PASS | suite |
| `class NTAIndex > locate` | 138 | method | PASS | suite |
| `class NTAIndex > to_geojson` | 147 | method | PASS | suite |
| `assign_restaurants` | 179 | function | PASS | suite |

### `nycsiting/google_places.py` — 14 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `price_label` | 83 | function | PASS | suite |
| `price_mix` | 88 | function | PASS | suite |
| `class PlacesError > __init__` | 117 | method | PASS | suite |
| `class CompetitorLandscape > total` | 139 | method | PASS | suite |
| `class CompetitorLandscape > strong` | 143 | method | PASS | suite |
| `class CompetitorLandscape > moderate` | 147 | method | NOT VERIFIED | CompetitorLandscape property |
| `class CompetitorLandscape > mean_rating` | 151 | method | PASS | suite |
| `class CompetitorLandscape > strongest` | 158 | method | PASS | suite |
| `search_places` | 167 | function | PASS | suite |
| `_is_subject_site` | 248 | function | PASS | suite |
| `to_dataframe` | 274 | function | PASS | suite |
| `add_competitor_strength` | 329 | function | PASS | suite |
| `classify_pressure` | 394 | function | PASS | suite |
| `fetch_landscape` | 415 | function | PASS | suite |

### `nycsiting/locations.py` — 3 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `_max_concurrent` | 24 | function | PASS | suite |
| `build_locations` | 56 | function | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `occupancy_history` | 110 | function | NOT REACHABLE | no call site in the product |

### `nycsiting/mapview.py` — 8 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `theme_for` | 66 | function | PASS | suite |
| `_circle` | 70 | function | PASS | suite |
| `_hover` | 78 | function | PASS | suite |
| `_hover > fmt` | 80 | nested | PASS | suite |
| `_assign_groups` | 99 | function | PASS | suite |
| `build_map` | 143 | function | PASS | suite |
| `_zoom_for` | 211 | function | PASS | suite |
| `map_table` | 216 | function | PASS | suite |

### `nycsiting/narrative.py` — 13 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `_an` | 80 | function | PASS | suite |
| `fit_score` | 85 | function | PASS | suite |
| `fit_band` | 96 | function | PASS | suite |
| `tone` | 102 | function | PASS | suite |
| `component_verdicts` | 113 | function | PASS | suite |
| `_ranked` | 145 | function | PASS | suite |
| `headline` | 155 | function | PASS | suite |
| `headline > phrase` | 167 | nested | PASS | suite |
| `reason_to_proceed` | 187 | function | PASS | suite |
| `reason_for_caution` | 197 | function | PASS | suite |
| `assessment_label` | 222 | function | PASS | suite |
| `comparison_row` | 236 | function | PASS | suite |
| `evidence_quality` | 256 | function | PASS | suite |

### `nycsiting/normalize.py` — 7 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `normalize_borough` | 65 | function | PASS | suite |
| `normalize_street` | 71 | function | PASS | suite |
| `normalize_building` | 109 | function | PASS | suite |
| `building_variants` | 115 | function | PASS | suite |
| `location_key` | 136 | function | PASS | suite |
| `location_key_variants` | 151 | function | PASS | suite |
| `pretty_address` | 161 | function | PASS | suite |

### `nycsiting/nta.py` — 7 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `_norm` | 30 | function | PASS | suite |
| `name_segments` | 36 | function | PASS | suite |
| `resolve_area_name` | 46 | function | PASS | suite |
| `load_equivalency` | 84 | function | PASS | suite |
| `load_polygons` | 100 | function | PASS | suite |
| `nta_demographics` | 113 | function | PASS | suite |
| `nta_demographics > weighted_indicator` | 126 | nested | PASS | suite |

### `nycsiting/panel.py` — 7 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `_parse_dates` | 56 | function | PASS | suite |
| `_read` | 65 | function | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `_aggregate` | 73 | function | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `_identity` | 87 | function | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `build_restaurants` | 96 | function | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `build_location_index` | 189 | function | PASS WITH LIMITATION | build-time only (`python build_data.py`) |
| `attach_coordinates` | 243 | function | PASS WITH LIMITATION | build-time only (`python build_data.py`) |

### `nycsiting/pedestrian_dot.py` — 13 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `_post` | 90 | function | PASS | suite |
| `fetch_pedestrian_sensors` | 105 | function | PASS | suite |
| `classify_distance` | 127 | function | PASS | suite |
| `nearest_pedestrian_sensor` | 135 | function | PASS | suite |
| `fetch_counts` | 145 | function | PASS | suite |
| `daily_series` | 173 | function | PASS | suite |
| `service_period_series` | 189 | function | PASS | suite |
| `_quantiles` | 202 | function | PASS | suite |
| `footfall_metrics` | 210 | function | PASS | suite |
| `measurement_window` | 248 | function | PASS | suite |
| `measure_location` | 260 | function | PASS | suite |
| `required_capture_rate` | 306 | function | PASS | suite |
| `footfall_covers` | 314 | function | PASS | suite |

### `nycsiting/plan_parser.py` — 14 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `class RestaurantPlan > _zip_shape` | 82 | method | PASS | suite |
| `class RestaurantPlan > _borough_known` | 88 | method | PASS | suite |
| `class RestaurantPlan > _spend_positive` | 97 | method | PASS | suite |
| `class RestaurantPlan > _seats_positive` | 103 | method | PASS | suite |
| `class RestaurantPlan > has_restaurant_plan` | 108 | method | PASS | suite |
| `class RestaurantPlan > location_kind` | 114 | method | PASS | suite |
| `resolve_api_key` | 226 | function | PASS | suite |
| `class PlanParseResult > diagnostics` | 260 | method | PASS | suite |
| `_extract_json` | 279 | function | PASS | suite |
| `parse_with_claude` | 284 | function | PASS | suite |
| `parse_fallback` | 343 | function | PASS | suite |
| `normalize_cuisine` | 395 | function | PASS | suite |
| `_classify_failure` | 407 | function | PASS | suite |
| `parse_plan` | 428 | function | PASS | suite |

### `nycsiting/report_pdf.py` — 7 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `_styles` | 38 | function | PASS | suite |
| `_table` | 61 | function | PASS | suite |
| `_fmt` | 77 | function | PASS | suite |
| `esc` | 81 | function | PASS | suite |
| `report_filename` | 91 | function | PASS | suite |
| `report_filename > slug` | 93 | nested | PASS | suite |
| `render_pdf` | 102 | function | PASS | suite |

### `nycsiting/report_writer.py` — 2 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `_validate` | 76 | function | PASS | suite |
| `narrate` | 88 | function | PASS | suite |

### `nycsiting/scoring.py` — 6 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `class Component > __init__` | 45 | method | PASS | suite |
| `_unavailable` | 50 | function | PASS | suite |
| `competitor_reference` | 60 | function | PASS | suite |
| `score_site` | 109 | function | PASS | suite |
| `combine` | 275 | function | PASS | suite |
| `_headline` | 310 | function | PASS | suite |

### `nycsiting/sim_animation.py` — 4 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `table_layout` | 27 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `occupied_count` | 46 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `frame_payload` | 58 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |
| `build_animation_html` | 77 | function | PASS WITH LIMITATION | suite; UI behind `ENABLE_SIMULATION` |

### `nycsiting/stats.py` — 2 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `wilson_interval` | 7 | function | PASS | suite |
| `rate_differs` | 26 | function | PASS | suite |

### `nycsiting/ui.py` — 16 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `inject_styles` | 19 | function | PASS | suite |
| `esc` | 34 | function | PASS | suite |
| `button_row` | 39 | function | PASS | suite |
| `eyebrow` | 59 | function | PASS | suite |
| `display` | 65 | function | PASS | suite |
| `section` | 69 | function | PASS | suite |
| `plan_chips` | 77 | function | PASS | suite |
| `query_context` | 140 | function | NOT REACHABLE | no call site in the product |
| `decision_hero` | 149 | function | NOT REACHABLE | no call site in the product |
| `status_label` | 180 | function | PASS | suite |
| `evidence_rows` | 185 | function | PASS | suite |
| `signal_strip` | 207 | function | PASS | suite |
| `stat_strip` | 222 | function | PASS | suite |
| `bench_rows` | 231 | function | PASS | suite |
| `competitor_rows` | 239 | function | PASS | suite |
| `recommendation_panel` | 255 | function | PASS | suite |

### `nycsiting/workspace_map.py` — 16 functions

| Function | Line | Kind | Verdict | Evidence |
|---|---:|---|---|---|
| `get_carto_api_key` | 39 | function | PASS | suite |
| `basemap_style` | 63 | function | PASS | suite |
| `as_geometry` | 148 | function | PASS | suite |
| `attach_geojson` | 167 | function | PASS | suite |
| `add_nta_boundaries` | 186 | function | NOT REACHABLE | no call site in the product |
| `_base_layout` | 208 | function | PASS | suite |
| `_legend_annotation` | 231 | function | PASS | suite |
| `band_choropleth` | 250 | function | PASS | suite |
| `continuous_choropleth` | 338 | function | PASS | suite |
| `add_site_marker` | 378 | function | PASS | suite |
| `_marker_hover` | 389 | function | PASS | suite |
| `_marker_trace` | 399 | function | PASS | suite |
| `add_radius_ring` | 420 | function | PASS | suite |
| `add_restaurant_markers` | 449 | function | PASS | suite |
| `competitor_markers` | 469 | function | PASS | suite |
| `legend_for` | 497 | function | NOT REACHABLE | no call site in the product |
