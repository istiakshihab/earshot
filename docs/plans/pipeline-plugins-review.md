# Review of `pipeline-plugins.md`

Two independent reviews of [pipeline-plugins.md](pipeline-plugins.md), read-only, against `feat/engine` at 44b5377. Nothing was run.

- **Review A:** correctness. Citations, execution model, protocol, plugin contract, migration.
- **Review B:** scope, user experience, phasing and testing.

**Spot-checks.** Their central code claims were checked against the source before this was written, and all held:

- The app already blocks a second filing: `PipelineModel.swift:348`, `PipelinePanel.swift:138`.
- Transcription resets speaker names (`jobs.py:225`), and renaming rejects labels that aren't in the transcript (`jobs.py:349-351`).
- `set_stage` replaces the whole stage entry (`session.py:75-80`).
- `_rename_output_dir` appends to the folder name (`pipeline.py:121-126`).
- A rename rewrites `transcript.md`; `transcript.json` keeps the labels (`jobs.py:84-91`).
- Tracks are 24 kHz mono (`TrackWriter.swift:5`), and the merge skips tracks in any other format (`SessionMerge.swift:168-171`).
- `merge` hardcodes `recording.wav` and rewrites `capture.json` (`SessionMerge.swift:178`, `Listings.swift:46-49`).
- `session.json` doesn't record the Whisper model (`jobs.py:189-194`).
- The Obsidian card has no switch (`PipelinePanel.swift:128-130`).
- Config loading drops unknown sections (`config.py:196`).
- The protocol lists a closed set of stage statuses (`engine-protocol.md:100`).
- The terminal asks Claude for speaker roles whenever diarization and `ask_speaker_names` are on (`pipeline.py:604`, `:660-664`).

---

## Review A: correctness

The plan's citations are almost all accurate. The execution model has three holes that break the headline features: Re-run with a new model, audio steps, and handling renames.

### Citations

All checked `path:line` references in sections 2, 3, 4 and 6 hold, except these:

- **"Filing twice makes two notes; the app only warns"** is wrong for the app. `PipelineModel.swift:348` guards `vaultNote == nil`, and `PipelinePanel.swift:138-154` shows only "Open in Obsidian" once a note exists. `open()` restores `vaultNote` (`:324-327`), so the app blocks a second filing, and the sheet's warning is practically unreachable. `:432` is the "before the speakers were renamed" text, not a duplicate warning. So **Update… / File as New** is a new capability, not a fix.
- **`events.py:28-29`** is a docstring describing the redirects. The real ones are `mlx_transcriber.py:271,319` (Python-level `redirect_stdout`) and `llama_cpp_summarizer.py:90` (`dup2` on fd 2). The claim itself holds.
- **`mlx_transcriber.py:88-98`**: the pass-through is `_resolve_model_repo` at `:123-124`, and the map is at `:93-99`. The map has no turbo or distil entry, so picker values must be full repo ids.

### Blockers

**B1. Re-running with a new model destroys speaker names, and labels aren't stable.** (verified)

- `jobs.py:225` resets `state["speakers"]` to empty names on every transcription, and `set_speaker_names` rejects labels that aren't in the new transcript (`jobs.py:348-351`).
- Diarization runs inside transcription, so changing only the Whisper model re-runs pyannote too, and `SPEAKER_00` can become a different person. That's most likely after denoising.
- **Fix.** Keep the diarization turns as their own artifact (`diarization.json`), fingerprinted on the audio and diarization settings only. Re-transcription then reuses the turns, so labels and names survive a model change. When diarization really does re-run, map old labels to new ones by time overlap, carry the names across, and flag the ones that don't match.

**B2. The fingerprint rule contradicts the rename rule.** (verified)

- The fingerprint hashes "the artifact files plus the names map". A rename rewrites `transcript.md` and `summary.md` (`jobs.py:84-91`, `:356`, `:366`), so every rename reads as "inputs changed, run again", which is an LLM call.
- The table in 3.4 says the opposite. The app commits names 1.2 s after typing stops (`PipelineModel.swift:458-461`), so this would fire often.
- **Fix.** Use one canonical label-form file per artifact (`transcript.json`, `summary.raw.md`, the plugin's raw file), and store two hashes per step: `content` and `names`. Only a `content` or settings change triggers a re-run; a `names` change alone triggers a re-render or a stale mark.
- `produces` with `speaker_labels=true` also needs a raw file to substitute from. Require such plugins to write a raw file that the runner renders.

**B3. Ordering by `needs` can't express `replaces`, and an audio step failing is undefined.** (judgment)

- A denoiser both needs and replaces `tracks`, while transcription needs `audio`. A sort on `needs` puts no edge between them, and "the user's order breaks ties" could put the denoiser after transcription.
- Not specified:
  - two steps replacing the same artifact;
  - an `audio` replacer followed by a `tracks` replacer, whose re-mix throws the first one's work away;
  - where the effective audio file is recorded;
  - what the re-mixed file is called.
- "A step whose required input failed is skipped" contradicts "transcription always runs": a crashed denoiser would block transcription.
- **Fix.**
  - Replacers of X sort before every step that consumes X without replacing it.
  - Steps that replace `tracks` sort before steps that replace `audio`; among equals, the user's order chains them.
  - Store `artifacts.audio = {file, by: [ids]}`.
  - A failed or removed replacer falls back to the previous version with a warning, and transcription records which audio it used.

### Major

- **M1. `set_stage` replaces the whole stage entry** (`session.py:75-80`), so every status change would wipe `inputs`, `settings_hash`, `draft` and `result`. *Fix:* merge instead of replacing. Keep results and fingerprints under a `last_run` that only a successful run overwrites. (verified)
- **M2. There's no way to compute or show "stale" without running a job.**
  - `plugins --json` isn't per session, and old sessions have no fingerprints.
  - The model and template are global, so one change would mark every past recording stale, filed notes included.
  - `stale` as a status also overwrites `done`, and the sidebar reads raw statuses (`History.swift:22`).
  - *Fix:* add `ownscribe status SESSION --json` (or `run --dry-run`) that emits the plan. Make stale a flag beside the status, not a status. A missing fingerprint means "unknown", not stale. Mark which settings affect output. Treat a changed global default as advice ("Transcribed with base · Re-run with turbo"), not stale.
  - (verified)
- **M3. `run` is ambiguous.**
  - Does `--step` cascade downstream, run missing upstream steps, or force a re-run when nothing changed?
  - Is `--set` saved or per-run, and how is it typed?
  - `--commit` with several steps is undefined.
  - `speakers` can't become a wrapper, because `run` has no way to take names.
  - *Fix:* a state table (status × fingerprint × flags → action), an explicit `--downstream`, a typed per-run `--set`, and a separate entry point for names.
  - (judgment)
- **M4. With review on, every run would now draft automatically.** Today nothing goes to Claude until you click "File to Obsidian…": the automatic path runs only when `asks == false` (`PipelineModel.swift:215`). Drafting in every run sends the transcript off the Mac before the speakers are named, then goes stale on the first rename and needs a second Claude call. *Fix:* with review on, end in `ready` without drafting, and draft on `run --step ID`. (verified)
- **M5. The draft and commit contract is incomplete.**
  - `commit` gets `edits` and `previous` but not the `draft`, and whether `edits` is a diff or the full values isn't said; today it's a merge (`jobs.py:469`).
  - Nothing ties a commit to its draft, so a rename between the two files stale text. The app guards this today with `draft.names == names` (`:349`).
  - A cancel during commit leaves the outcome unknown, and retrying duplicates.
  - *Fix:* pass `draft` and `edits`. Make `--commit` take a `--draft-id` and refuse it if the fingerprint moved. Record `committing` before the call, so an interrupted commit shows as "may have been sent".
  - (judgment)
- **M6. The diarization rules contradict each other.**
  - "Keep the transcript when diarization fails" has to catch the error inside `MLXWhisperTranscriber` (`mlx_transcriber.py:345-348`, `:428-432`). The plan says that class is unchanged, and the change also affects the terminal path, which has no handler (`pipeline.py:620`).
  - Diarization is called a processor (re-runs automatically) but can only run by re-transcribing (never automatic).
  - The `stage` events for diarizing come from the same progress object as transcribing, so tagging them with a step needs a stage-to-step map.
  - (verified)
- **M7. The folder rename.**
  - "Rename after any step that sets `title`" breaks on re-summarizing: `_rename_output_dir` appends (`pipeline.py:126`), giving `id_slug1_slug2`. Today `resummarize` never renames.
  - A second rename also breaks the filed note's `transcript_folder` (`vault_filing.py:211`) and stored link paths.
  - `prepare_note` takes the date and folder name from the path (`vault_filing.py:234-235`).
  - *Fix:* rename once, only while the folder name still equals the session id. Give destinations an implicit `title?` dependency. Use session-relative paths in events.
  - (verified)
- **M8. Plugin code runs before consent, and the consent hash is unstable.**
  - `plugins --json` and `doctor` would run `check` for any folder that was merely dropped in.
  - Hashing the whole folder changes whenever the plugin writes `__pycache__`, a venv, a log or a token.
  - There's no timeout on `check`, which blocks the launch-time `doctor`.
  - *Fix:* never run plugin code before consent matches. Hash `plugin.toml` plus a declared `files` list. Give plugins their own state directory. Reject an `exec` that resolves outside the folder. Store consent as `{id: hash}` in a separate file. Keep plugin problems advisory, so they don't flip `doctor`'s `ok`.
  - (judgment)
- **M9. Re-mixing through the helper is riskier than phase 5 suggests.**
  - `mergeSession` hardcodes `recording.wav` (`SessionMerge.swift:178`), and `merge` rewrites `capture.json` (`Listings.swift:46-49`).
  - A track that isn't 24 kHz mono is silently skipped (`SessionMerge.swift:168-171`), so a plugin writing 48 kHz gives a mix with no mic.
  - The backend can fall back to downloading the upstream helper (`coreaudio.py:111-127`), which lacks the new flag.
  - *Fix:* with `--output`, never touch `capture.json`. Validate format and length before merging, and treat a skipped track as a failure. Probe for `--track` support and never download inside a job. The mock needs a fake `merge`.
  - (verified)
- **M10. The terminal pipeline.**
  - "Runs enabled user plugins after its own steps" can't apply an audio step, so the same config would give different transcripts in the app and the terminal.
  - Terminal folders differ from the artifact model: no `session.json`, only one of `transcript.md`/`transcript.json`, names baked in, no `summary.raw.md`, audio possibly elsewhere or deleted.
  - **The preference migration is a hazard.** The app's diarize default is on (`Prefs.swift:42`). Writing it to `[diarization] enabled` would make the terminal ask for names and call Claude for speaker roles (`pipeline.py:604`, `:660-664`): a new off-Mac send nobody opted into.
  - *Fix:* migrate only keys the user explicitly set, and leave diarize alone or set `ask_speaker_names = false` with it. Either have the terminal call the runner, or say audio steps are app-only.
  - (verified)
- **M11. Compatibility through phases 1–3 is unspecified.** The current app reads `stages.vault`, top-level `vault_note`, the `vault_preview` event and `error.stage == "vault"`, and `session.load` always re-adds `vault`. *Fix:* make `obsidian` canonical, write the legacy keys too until phase 3, and alias them in `load`. (verified)
- **M12. Config.**
  - `[plugins.<id>] enabled` shares a table with the plugin's settings, which makes `enabled` a reserved key; use `[plugins.<id>.settings]`.
  - `_merge_toml` drops unknown sections (`config.py:196-240`), so `Config` needs `plugins` and `pipeline`.
  - There would be two writers of `config.toml`: the app's token editor (`HFConfig.swift`) and the backend.
  - Plugin ids need a charset rule.
  - (verified)
- **M13. The single-writer rule only holds per process.** Each job keeps its own copy of the state and overwrites the file, and the app doesn't stop jobs when it quits. *Fix:* `flock` a session lock for the life of a job, with a `session_busy` error. Start plugin children in their own process group and kill the group. (judgment)

### Minor

- `off_mac` for summarization should depend on the host: `openai` is documented for local servers (`config.py:44`), and `ollama` can be remote.
- Mutes are baked into the tracks at capture; only gains apply at the mix. Track ids contain `:` and dots (`app:us.zoom.xos`), so file and flag naming needs a rule.
- `review`, `stale` and `cancelled` are new status values, but the doc lists a closed set (`engine-protocol.md:100`).
- Hashing `recording.wav` and the tracks on every run is slow; cache by size and modification time.
- Replaced audio files are never cleaned up when a step is switched off.
- A plugin child's environment and working directory are unspecified: PATH, and scrubbing `HF_TOKEN` and `OPENAI_API_KEY`.
- `result.links` URLs need a scheme whitelist.
- The model picker drops `tiny` and `medium`, which are valid today; the current value must still show.
- "Other…" needs a free-text choice type, which isn't in the field list.
- A removed plugin's failed stage keeps the sidebar warning forever (`History.swift:22`).
- Whether a newly added step appears on old recordings is unspecified. A saved `plan` suggests not, which blocks "send an old meeting to Drive".

### Worth keeping

- Orchestration in the backend, in one `run` job.
- Draft and commit as two jobs.
- External executables for user plugins.
- Never overwriting `recording.wav` or `tracks/`.
- Destinations that never re-send by themselves.
- Reusing the existing config keys.
- The phase 2 contract test.
- The honest statement that consent informs and doesn't contain.
- Deferring Keychain, parallelism and an SDK.
- The finding that filing doesn't depend on the summary.

---

## Review B: scope, user experience and phasing

The plan is accurate about the code; every citation checked matched. It is roughly twice the size the three goals need.

- **Sizing:** about 25 working days, with nothing visible until phase 4, around day 14.
- **Generality vs. demand:** the generality (the external executable protocol, fingerprints, consent hashing, `replaces = "tracks"`) exists for two plugins. One is about 30 lines of Python; the other the plan itself says it isn't building.
- **Goals 2 and 3** (switching steps off, choosing the model) need a config writer and two controls.
- **Agreed:** backend orchestration, Python-first steps and running steps one at a time.

### High

**H1. Phases 1–3 deliver nothing visible, and the riskiest phase sits on an untried protocol.** (judgment; code facts verified)

- Phase 1 changes no protocol or UI. Phase 2 is additive. Phase 3, built-ins only, still shows the same four cards.
- Phase 3 rewrites `PipelineModel.swift` (712 lines), `PipelinePanel.swift` (518), `VaultSheet.swift`, `History.swift` and `Sidebar.swift` with no Swift tests. The protocol is frozen in phase 2 before its only consumer exists.
- Rough effort, in days:

  | Phase | 1 | 2 | 3 | 4 | 5 | 6 |
  |---|---|---|---|---|---|---|
  | Days | 3 | 3 | 5–6 | 3–4 | 6–7 | 3 |

- *Recommendation:* ship the first slice below before any runner work. Then build phases 2 and 3 as one vertical slice for a single step (summarization) before generalising.

**H2. "Stale" can't be shown as designed, and Re-run on transcription destroys work.** (verified)

- Staleness is computed only during a run, but the app opens past recordings by reading `session.json` directly (`PipelineModel.swift:257-327`).
- `session.json` doesn't record the model (`jobs.py:188-193`), so changing the model would mark every past recording stale at once.
- Re-running `process` resets names (`jobs.py:225`), regenerates the summary and title, renames the folder again (`jobs.py:249-254`), and leaves a filed note out of date.
- *Recommendation:* record the model on the transcription stage and show "Transcribed with X". Offer an explicit **Transcribe Again…** with a confirmation that says what will be replaced. Carry names over when the speaker count matches.

**H3. The external-plugin protocol exists mostly to "prove the path".** (judgment)

- Drive via the sync folder is a file copy. Phase 5 bundles `ExternalStep`, the stdin/fd 3 protocol, event whitelisting, killing children, manifest validation, folder scanning, consent hashing, the Add UI, mock fakes and `docs/plugins.md` for it.
- *For:* adding steps without touching the backend, and steps that need dependencies outside the frozen environment.
- *Against:* the owner is the only plugin author and already edits this repo. A built-in `Step` subclass is less work and is covered by pytest.
- *Pick:* Python-only steps now, with Drive as a built-in. Shape the `Step` interface so `ExternalStep` can be added once a step needs dependencies that can't go in `uv.lock`.

**H4. Audio steps are the costliest part of phase 5, for a plugin nobody is building and an unproven benefit.** (verified facts, judgment on the cut)

- Tracks are 24 kHz mono (`TrackWriter.swift:5`). RNNoise and DeepFilterNet work at 48 kHz, so a plugin must resample both ways.
- The merge silently skips a track in another format (`SessionMerge.swift:171-175`), giving a mixdown with no microphone that transcription would then run on.
- The output length must equal the input, or timestamps drift against playback.
- `merge` isn't reusable as is: it hardcodes `recording.wav` and rewrites `capture.json`.
- *Recommendation:* cut `replaces` and the helper changes from this plan. Spend an hour running ffmpeg `afftdn` or `arnndn` by hand on three noisy recordings and compare transcripts. If it helps, the cheapest version is a transcription setting that pre-filters through ffmpeg to a temporary file. If audio plugins come later, start with `replaces = "audio"` only, and have the runner validate rate, channels and frame count.

**H5. The Drive walkthrough doesn't work as written.** (verified)

- The plugin would live in the repo at `plugins/gdrive/`, but plugins load only from `~/.config/ownscribe/plugins/`, and nothing copies it there; `install.sh` doesn't.
- *Recommendation:* make Drive a built-in, or scan the repo's `plugins/` as a second, read-only location.

### Medium

- **M1. Fingerprints and automatic re-runs are more than the cases need.** A speaker rename on an old recording could trigger a re-summary because a summarizer setting changed months ago. *Recommendation:* no content hashes. Store two facts per destination result: the names it was sent with, and a hash of the file it wrote. Processors re-run only when asked, as today. (judgment)
- **M2. The "Off this Mac" badge and hash-pinned consent add noise without protection.**
  - Three of five cards would carry the badge, and it conflates "an LLM vendor reads the transcript" with "a copy lands in my own Drive".
  - Hash pinning re-prompts the owner every time they edit their own plugin.
  - *Recommendation:* put the destination in the card's subtitle, as the Obsidian card already does. Use one enable sheet for external plugins, without hash pinning.
  - (judgment)
- **M3. Reviewing a Drive send is friction.** With Obsidian and Drive both on, two sheets and their order are unspecified. The generic draft also needs the "already filed" warning and the empty-title check. *Recommendation:* Drive sends when it finishes, with **Send Again** after a rename. Keep draft and commit, and the existing `VaultReviewSheet`, for Obsidian only until a second interactive step exists. (judgment)
- **M4. Global switches on per-recording cards are ambiguous and slow.** A switch on a past recording's card would change future recordings, and each switch costs a `uv run` start. *Recommendation:* show switches only on the live recording's cards and in Settings. Cut drag-to-reorder and `[pipeline] order`, since `needs` already orders four or five steps. (judgment)
- **M5. A failing or hanging plugin is under-specified.**
  - Where its stderr goes isn't said.
  - There's no timeout, and a running job blocks renames and template changes.
  - A GUI job's PATH may not include `uv` (`jobs.py:39-46`).
  - *Recommendation:* keep the last 20 stderr lines with **Show Log** on a failed card, add a default timeout, and pass the resolved `uv` path and PATH to children.
  - (judgment)
- **M6. Drive via the sync folder is the right choice, with three gaps.**
  - Account detection must not read inside the Drive folder at launch, or the privacy prompt appears when the app starts.
  - In mirror mode the folder may be elsewhere, so add a path setting.
  - "Replace if unchanged" can't hash an online-only file; compare size and modification time, or always write a new file.
- **M7. Phase 1's "`test_jobs.py` passes unchanged" won't hold.** The tests patch `ownscribe.jobs.create_summarizer` and `ownscribe.jobs.shutil.which` (`tests/test_jobs.py:115-116`), which moving the code breaks. Ship the diarization fix as its own commit. (verified)

### Low

- Generic cards are less generic than stated: the diarization card has four sub-steps and a Hugging Face button matched on message text (`PipelinePanel.swift:84-103`).
- Rewriting the config leaves trailing comments that describe the old value. Drop the trailing comment when the value changes, and write a `config.toml.bak` once.
- The picker must store full repo ids, and per-option "downloaded" labels need a loop (`jobs.py:554-561` checks only the configured model).
- `tomlkit` is a new dependency, so `uv.lock` must change with it.

### Testing

- **Make fixtures the contract.** pytest records the real runner's event streams (with fake steps) as JSONL, and `mock_engine.py` replays them, so the mock can't drift.
- **Add a replay flag to the app.** `--replay-events FILE --dump-state` lets pytest snapshot the resulting card state. That needs event handling to be a pure reducer.
- **Diff before and after phase 3.** Run `--dump-session` over every real recording before and after phase 3 and compare.
- **Test config writes against a copy of the real `config.toml`.**

### Recommended cuts

- External executable plugins, consent hashing, the Add UI and `docs/plugins.md`, until a step needs its own dependencies.
- `replaces`, the re-mix and the helper changes, until a denoise trial shows it helps.
- Content fingerprints and automatic processor re-runs.
- The generic review sheet; keep `VaultReviewSheet`.
- The badge, drag-to-reorder, `[pipeline] order` and phase 7.

### Recommended first slice

About 2–3 days:

1. A config writer: `ownscribe config set SECTION.KEY VALUE` with `tomlkit`, tests against the real config, and a one-time backup.
2. A model picker on the Transcription card that records the model on the stage, shows "Transcribed with X", and offers **Transcribe Again…** with a confirmation.
3. A switch on the Obsidian card.
4. Moving the diarize and summarize switches into `config.toml`. (Note Review A's M10: migrate only keys the user explicitly set.)
5. The diarization-failure fix, as its own commit.
