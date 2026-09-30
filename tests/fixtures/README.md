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

The tone WAVs exercise audio routing and gain reproducibly; they are not
substitutes for a natural Vietnamese voice. Add a locally permitted Vietnamese
voice recording to the real-media acceptance set when one is available. The
synthetic corpus generator also creates portrait/landscape, VFR, rotated MOV,
and 60-second stress media into its temporary output directory. Optional
`--real-video` and `--vietnamese-voice` arguments copy locally authorized samples
into that same temporary corpus and record their media metadata in the manifest.
