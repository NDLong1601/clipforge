# M0 fixture catalog

All generated media, project stores, and render outputs belong under pytest's
`tmp_path` or the benchmark's temporary output directory. No user media or
rendered binary is checked into this repository.

| Fixture | What it represents | Used for |
| --- | --- | --- |
| `legacy_project_v1.json` | Project saved before newer optional fields/schema versioning | Open/read compatibility |
| `template_with_logo.json` | Reusable template with an image asset reference | Cross-project template regression |
| `manual_subtitle_edit.json` | Hand-edited cue while timing still says `estimated` | Replan subtitle regression |
| `corrupt_project.json` | Deliberately truncated project JSON | Recovery/list isolation scenarios |
| `two_tab_edit.json` | Two editor copies based on the same revision | Stale-write conflict regression |
| `voice_text_vi.txt` | Vietnamese speech text for an acceptance voice fixture | Real voice alignment acceptance |
| `factory.py` | Builders for 100/101 scenes, transparent PNG, 30 fps source, and voice/music tones | Deterministic tests and benchmarks |
| `reliability/job_legacy_interrupted.json` | Legacy job with no durable snapshot/payload | Legacy read and restart-only compatibility |
| `reliability/job_interrupted_render.json` | Versioned render job with one validated clip checkpoint and one incomplete clip | Resume plan and checkpoint validation |
| `reliability/job_provider_outcome_unknown.json` | TTS request may have reached provider but result was not persisted | No automatic retry; explicit user decision |
| `reliability/job_commit_recovery.json` | Project write completed before job receipt | Idempotent recovery without duplicate export/revision |

The tone WAVs exercise audio routing and gain reproducibly; they are not
substitutes for a natural Vietnamese voice. Add a locally permitted Vietnamese
voice recording to the real-media acceptance set when one is available. The
synthetic corpus generator also creates portrait/landscape, VFR, rotated MOV,
and 60-second stress media into its temporary output directory. Optional
`--real-video` and `--vietnamese-voice` arguments copy locally authorized samples
into that same temporary corpus and record their media metadata in the manifest.

Reliability job fixtures are contract examples for later M1/M6 work, not live
job-store records and not executable tests yet. See [`docs/reliability/M0.md`](../../docs/reliability/M0.md)
for the state, API, fingerprint, error, and fault-injection contract.

## R0 executable regression fixtures

`../conftest.py` isolates collection and each test from real application data.
`job_runtime.py` stops/joins the scheduler and all test-owned workers before
restoring ROOT and scheduler globals, including jobs removed from registries.
`../test_job_runtime_fixture.py` verifies this lifecycle with live threads.

F1-F8 desired-behavior regressions live in `../test_reliability_r0.py` and
`../test_reliability_r0_process.py`; F5/F9 component regressions use the frontend
harness. Known reds are explicit strict xfail/TODO assertions, with separate
raw-red runs and persistent logs. See [R0.md](../../docs/reliability/R0.md) for
the finding checklist, commands, baseline, and release boundaries.
