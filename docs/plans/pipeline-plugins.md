# Pipeline plugins: turning "After recording" into composable steps

Status: plan only, nothing implemented. Revised after two independent reviews
([pipeline-plugins-review.md](pipeline-plugins-review.md)). Written from reading the code on `feat/engine`
(44b5377); cited lines were checked against the source, but nothing here has been verified by running it.
Paths are relative to the repository root.

Terms: a **step** is one card in the "After recording" panel. A **plugin** is what provides a step: a
built-in Python class, or a folder with a manifest and an executable (an **add-in**).

## 1. Context

Today the panel is four hardcoded cards: Transcription, Diarization, Summarization and Obsidian. Each is wired
by name through the backend, the protocol, `session.json` and the Swift model, so adding a step means editing
all four layers. The goals:

1. **Add steps later as add-ins**, without touching Earshot or the backend. Examples: "send to Google Drive"
   instead of Obsidian, or noise cancellation on the audio before transcription.
2. **Switch off steps you don't want.**
3. **Choose the transcription model** from the transcription step's settings.

Goals 2 and 3 need no plugin machinery, so they ship first, in a small slice (phase 0). Goal 1 follows in
phases that each leave the app working.

### What changed after review

- **Shape of the work.** A 2–3 day first slice now comes before any plugin machinery.
- **Re-transcription.**
  - It keeps speaker names, because speaker turns are saved separately and reused.
  - It is an explicit **Transcribe Again…**, not an automatic re-run.
- **Change detection.**
  - "Stale" is a flag, not a status, and is read with a status command.
  - There are no content fingerprints. Steps re-run only when asked, and destinations remember what they sent.
- **Privacy and state fixes.**
  - With review on, the Obsidian draft is never started automatically.
  - Stage writes merge instead of replacing the entry.
  - The folder is renamed once.
  - Jobs take a session lock.
  - Settings migrate safely.
- **Add-ins.** Add-ins stay, built after the built-in steps. No add-in code runs before you approve it.
- **Audio steps** move to the last phase, behind a short denoising trial, starting with whole-mixdown
  replacement only.
- **Cuts.** No "Off this Mac" badge, no reorder, no generic review sheet yet. Drive sends without a review.

## 2. The current architecture

### Flow

| What | Where |
|---|---|
| One job runs transcription, diarization and summarization | `src/ownscribe/jobs.py:145-264` |
| Diarization is a thread inside the transcriber, joined before speakers are assigned | `jobs.py:201-213`, `src/ownscribe/transcription/mlx_transcriber.py:284-286`, `:413-431` |
| Summary, title, then the session folder is renamed after the title | `jobs.py:267-325`, `:249-254` |
| Renaming speakers re-renders `transcript.md` from `transcript.json` (which keeps the labels) and substitutes labels in `summary.raw.md`; the LLM runs only with `--resummarize` | `jobs.py:84-91`, `:337-374` |
| Re-summarize with another template (never renames the folder) | `jobs.py:394-417` |
| Vault preview (Claude drafts from the **transcript**, not the summary) and write | `jobs.py:423-478`, `src/ownscribe/vault_filing.py:223-244`, `:176-220` |
| Doctor, with per-stage checks inline | `jobs.py:492-543` |
| Job wrapper: events, exit codes 0/1/2/130, SIGTERM cancels | `src/ownscribe/cli.py:362-397` |
| Terminal pipeline: its own copy of the flow, with TTY prompts | `src/ownscribe/pipeline.py:583-730` |
| With `confirm_before_filing = false`, the **app** chains process → preview → write | `app/Sources/Earshot/Model/PipelineModel.swift:215-218`, `:400-413` |

### Facts the design depends on

- **What `session.json` records.** `options` records template, diarize, summarize and language, but not the
  Whisper model (`jobs.py:189-194`).
- **Transcription and speaker names.** Every transcription resets speaker names (`jobs.py:225`), and renaming
  rejects labels that aren't in the transcript (`jobs.py:348-351`).
- **Stage writes.** `set_stage` replaces the whole stage entry (`src/ownscribe/session.py:75-80`). `STAGES` is
  fixed, and `load` adds all four to every session (`session.py:17`, `:63-65`). Vault state lives in the
  top-level keys `vault_preview` and `vault_note` (`session.py:50-51`).
- **Folder renames.** `_rename_output_dir` *appends* the slug to the folder name (`pipeline.py:121-126`).
- **Duplicate filing.** The app blocks a second filing (`PipelineModel.swift:348` guards `vaultNote == nil`;
  `app/Sources/Earshot/Views/PipelinePanel.swift:138-154`). Only the backend would write twice (`jobs.py:427`).
- **When the transcript is sent.** With review on, nothing goes to Claude until **File to Obsidian…** is
  clicked (`PipelineModel.swift:215`).
- **A pyannote failure.** It fails transcription too, and no transcript is saved (`PipelineModel.swift:537-547`,
  `mlx_transcriber.py:345-348`, `:428-432`).
- **Track and merge formats.**
  - Tracks are 24 kHz mono (`swift/Sources/TrackWriter.swift:5`).
  - The merge silently skips any track in another format (`swift/Sources/SessionMerge.swift:168-171`).
  - The merge also hardcodes `recording.wav` (`:178`), and `merge` rewrites `capture.json`
    (`swift/Sources/Listings.swift:46-49`).
- **Process-wide output redirects.** The built-ins redirect process-wide output: `redirect_stdout` in
  `mlx_transcriber.py:271`, `:319`, and `dup2` of fd 2 in
  `src/ownscribe/summarization/llama_cpp_summarizer.py:90`.
- **Jobs at quit.** Quitting the app leaves a running job going (`app/Sources/Earshot/EarshotApp.swift`,
  `applicationShouldTerminate`). Cancel is SIGTERM to the direct child (`app/Sources/Earshot/Engine/Engine.swift:159`).
- **Config loading.** It ignores unknown sections (`src/ownscribe/config.py:196-240`).
- **Defaults that disagree.** The app defaults diarization on (`app/Sources/Earshot/Model/Prefs.swift:42`);
  the config defaults it off (`config.py:98`). With diarization on and `ask_speaker_names` on, the terminal
  asks Claude for speaker roles (`pipeline.py:604`, `:660-664`).
- **Statuses.** The protocol lists a closed set of stage statuses: `pending`, `running`, `done`, `failed`,
  `skipped` (`docs/engine-protocol.md:100`).
- **Model names.** The model map has only `tiny` to `large-v3` (`mlx_transcriber.py:93-99`). Anything else
  passes straight through as a repo id or path (`_resolve_model_repo`, `:123-124`).

### What is coupled to the four fixed stages

| Layer | Coupling |
|---|---|
| Backend | `STAGES`; the `process()` body; `doctor()` checks; the `file` subcommand (`cli.py:505-530`) |
| App model | Four `Stage` properties (`PipelineModel.swift:45-47`, `:65`); `applyStage` switches on stage names (`:567-601`); `open()` reads stages by name (`:282-284`, `:324-327`); the vault flow (`:330-445`); error routing by stage name (`:548-559`) |
| App views | Four card properties (`PipelinePanel.swift:33-38`, `:59-156`), including diarization's four sub-steps and its Hugging Face button matched on message text (`:84-103`); a two-case `Tab` enum (`:17`); `VaultReviewSheet` (`app/Sources/Earshot/Views/VaultSheet.swift`) |
| App elsewhere | `DoctorReport` (`app/Sources/Earshot/Engine/Tools.swift:55-80`, `:165-198`); `HistoryItem.vaultNote` and "Filed to Obsidian" (`app/Sources/Earshot/Model/History.swift:20`, `:105`; `app/Sources/Earshot/Views/Sidebar.swift:59-60`, `:112-114`); the status line (`app/Sources/Earshot/Model/AppModel.swift:311-312`) |
| Preferences | Diarize, summarize and template live in UserDefaults and are passed as flags (`PipelineModel.swift:70-72`, `:137-139`). Obsidian has no card switch at all (`PipelinePanel.swift:128-130`), only `[obsidian] enabled` |
| Mock | One function per command (`scripts/mock_engine.py:438-587`), fake doctor (`:606-621`) |

Already generic: `StageCard`, `StatusIcon`, `ProgressBar` and the markdown view `SummaryView`
(`PipelinePanel.swift:263-333`, `:421-505`); `History.read` copies whatever stage keys exist
(`History.swift:99-102`).

## 3. The design

### 3.1 Decisions

| Question | Decision |
|---|---|
| Who orchestrates | The backend, in one `ownscribe run` job. It replaces the app's three-job chain, and the logic lives once, under pytest |
| Is transcription a plugin | No. It is a fixed core step, shown as a card, with settings like any plugin (model, language, prompt) |
| Is diarization a plugin | A built-in you can switch off. It is computed inside the transcription unit, but its **turns are saved separately** (3.4) |
| Plugin kinds | `processor` (writes files in the session folder) and `destination` (sends them somewhere) |
| How plugins run | Built-ins are Python classes, in-process. Add-ins are external executables, so their dependencies stay out of the `--frozen` backend and a crash stays out of the job |
| Re-runs | Only when you ask. No content fingerprints. Destinations remember what they sent, which is enough to say "sent before the speakers were renamed" |
| Stale | A flag beside the status, read with `ownscribe status SESSION`. Never a status of its own |
| Interactive steps | Draft, then commit, as two jobs. Obsidian keeps its review sheet; a generic sheet waits for a second interactive step |
| Enable and settings storage | `config.toml`, written only by the backend |
| Order | Derived (3.4). No user reordering |
| Parallelism | Serial, except transcription with diarization |

### 3.2 Artifacts

Each artifact maps to files, and each has **one canonical label-form file**. Speaker names are applied when
rendering, never baked into the canonical file.

| Artifact | Canonical | Rendered |
|---|---|---|
| `audio` | `recording.wav` | |
| `tracks` | `tracks/*.wav` (24 kHz mono; gains in `capture.json`, mutes baked in at capture) | |
| `turns` | `diarization.json` (speaker turns, new) | |
| `transcript` | `transcript.json` | `transcript.md` |
| `speakers` | names in `session.json` | |
| `summary` | `summary.raw.md` | `summary.md` |
| `title` | in `session.json` | |
| A plugin's output | `<id>.<name>.raw.md` (or its own file) | `<id>.<name>.md` when `speaker_labels = true` |

A plugin that sets `speaker_labels = true` writes the raw, label form, and the runner renders the named
version. So a rename never needs the plugin again.

### 3.3 The plugin contract

**Manifest** (`plugin.toml`; built-ins declare the same fields in Python):

```toml
id = "gdrive"                   # [a-z0-9-], equals the folder name, not a built-in id
name = "Google Drive"
kind = "destination"            # processor | destination
protocol = 1
exec = "./run"                  # must resolve inside the plugin folder
files = ["run", "gdrive.py"]    # what consent covers (3.8)
needs = ["summary", "transcript?", "speakers?"]   # "?" = optional
produces = []                   # processors: [{name="actions", file="actions.raw.md", tab="Actions", speaker_labels=true}]
# replaces = "audio"            # audio processors only (3.6)
interactive = false             # true: draft + commit
off_mac = { sends = ["summary", "transcript"], to = "Google Drive" }   # omit when nothing leaves the Mac

[[settings]]
key = "folder"
type = "string"                 # bool | string | int | choice | path | text | secret
label = "Folder in My Drive"
default = "Meetings"
# choice: options = [...], allow_other = true for a free-text "Other…", or options_from = "check"
# inline = true also shows the control on the card
```

The runner enforces two rules:

- A plugin with `off_mac` may not need `audio`, `tracks` or `turns`.
- The plugin `id` must be valid and unique.

**Runtime interface** for an add-in:

```
<exec> check                       stdin: {settings}      stdout: {"problems":[...], "options":{key:[...]}}
<exec> run|draft|commit --events-fd 3
    stdin:  {"protocol":1, "phase":"commit", "session":{"id","path","title","created"},
             "artifacts":{"summary":{"files":["summary.md"]}}, "speakers":{...}, "settings":{...},
             "draft":{...}?, "edits":{...}?, "previous":{...}?}
    fd 3:   progress, artifact, result, error   (JSON lines, same conventions as job events)
    exit:   0 ok · 1 failed · 130 cancelled
```

- **Process.** The child runs in its own process group with the working directory set to its state directory
  (`~/.config/ownscribe/plugins/.state/<id>/`, also passed as `OWNSCRIBE_PLUGIN_STATE`).
- **Environment.** PATH comes from `prepare_environment` plus the resolved `uv`. `HF_TOKEN` and
  `OPENAI_API_KEY` are removed, and secrets go in as `OWNSCRIBE_SECRET_<KEY>`.
- **Timeouts.** `check` gets 10 s. `run`, `draft` and `commit` get a per-plugin default of 30 minutes,
  overridable in the manifest.
- **Logs.** The last 20 lines of stderr go into `stages.<id>.error` and the app log. A failed card shows
  **Show Log**.
- **Events.** The runner forwards a whitelist of the child's events, tagged with the step id. A plugin cannot
  emit `transcript` or `session_moved`, and `result.links` accepts only `file:` paths and `https:` URLs.
- **Who writes state.** Only the runner writes `session.json`.

### 3.4 Pipeline execution

**Ordering.** No user reordering. The order is derived:

1. Audio replacers (3.6). An audio replacer sorts before every step that consumes `audio` without replacing it.
2. Transcription.
3. Processors, by `needs`.
4. Destinations. Each implicitly needs `title?`, so it starts after the folder rename. Ties go in built-in
   order, then by id.

**`ownscribe run SESSION`**, with these options:

- `--step ID` runs only that step.
- `--downstream` also runs the steps that consume its outputs.
- `--set ID.KEY=VALUE` is a typed, per-run override and is not saved.
- `--commit ID --draft-id X --edits FILE` commits a draft.

What a run does:

1. **Plan.** Emits a `plan` event so the app draws the cards before anything runs.
2. **Run** steps in order.
   - A step whose required input is missing or failed is `skipped`, with a reason. Independent steps still run.
   - Transcription always runs, on the newest good audio (3.6).
   - Exit code 2 when an optional step failed, as today.
3. **Interactive destinations.**
   - **Review on:** the step ends in a `ready` state, and **nothing is drafted**. Clicking **File to
     Obsidian…** runs `run --step obsidian` to draft, then the review sheet, then `--commit` with the draft id.
     The commit is refused if the names changed since the draft (today's guard, `PipelineModel.swift:349`).
     `committing` is recorded before the call, so an interrupted commit shows as "may have been sent".
   - **Review off:** draft and commit in one job, which removes the app-side chain.
4. **Cancel.** SIGTERM as today. The runner kills the child's process group (SIGKILL after 5 s) and records
   `cancelled`, instead of leaving `running` in `session.json`.

**Session lock.** A job holds `flock` on `<session>/.session.lock` for its whole life. A second job on the
same recording fails with `session_busy`. That covers the app and the terminal, and an orphaned job the app
left running at quit.

**Stage writes merge.** `set_stage` merges into the entry instead of replacing it. A step's outputs and what it
sent live under `last_run`, which only a successful run overwrites.

**Folder rename.** Only once, and only while the folder name still equals the session id. A later
re-summarize never renames, as today. Events carry session-relative paths.

**Diarization turns.**

- Transcription with diarization writes `diarization.json` (the turns), keyed by the audio file and the
  diarization settings.
- **Transcribe Again** with only a different Whisper model reuses the turns: the new words are assigned to
  the same labels, so the names survive.
- When diarization really does re-run (new audio, or new speaker counts), old labels are mapped to new ones
  by time overlap. Names carry across where the overlap is clear. Unmatched speakers are listed for you to
  name again.
- A pyannote failure keeps the transcript without speakers and marks only diarization failed. This needs a
  catch inside `MLXWhisperTranscriber` (`mlx_transcriber.py:345-348`, `:428-432`), which the terminal also
  benefits from (`pipeline.py:620`).

**Stale and re-runs.**

| Situation | What happens |
|---|---|
| You change a global default (model, template) | Nothing goes stale. Cards show what was used ("Transcribed with base"); the transcription card offers **Transcribe Again…** |
| You rename speakers | Rendered files are re-rendered from their canonical form. No LLM call. A destination whose `last_run.sent_names` differ is flagged `stale: {reason: "renamed"}`, with **Update…** or **Send Again** |
| Transcribe Again… | A confirmation lists what will be replaced: the transcript, then the summary and any outputs that depend on it. Names survive (turns reused). Filed notes are flagged stale, never changed |
| A recording with no `last_run` record (older ones) | Unknown, so not stale |

`ownscribe status SESSION --json` emits the `plan` with statuses and stale flags, without running anything.
The app calls it when opening a recording.

**Update… for a filed note** is a new capability (the app blocks a second filing today). **Replace** is
offered when the earlier file is unchanged (same size and modification time as `last_run.written`);
otherwise **File as New**.

**Parallelism.** Serial in v1. Two in-process steps can't overlap safely because of the process-wide output
redirects. Events and state are keyed by step id, so concurrency can come later without a protocol change.

### 3.5 Discovery, enabling and storage

**Where plugins come from.**

- **Built-ins:** `src/ownscribe/steps/builtin/`.
- **Bundled add-ins:** `plugins/` in the repo (or the installer's checkout), scanned read-only. Drive ships
  here, off by default.
- **User add-ins:** `~/.config/ownscribe/plugins/<id>/`. Settings has **Add…** (choose a folder to copy in) and
  **Reveal Plugins Folder**.

**Config keys.**

- Built-ins keep their existing keys (`[diarization] enabled`, `[summarization] enabled`,
  `[obsidian] enabled`, and their settings).
- Add-ins use `[plugins.<id>] enabled` and `[plugins.<id>.settings]`.
- `Config` gains `plugins`, since unknown sections are dropped today.

**Writes.**

- `ownscribe config set KEY VALUE`, and `ownscribe plugins enable|disable <id>`, use `tomlkit`. The write is
  atomic, keeps mode 600, writes `config.toml.bak` once, and drops a trailing comment when its value changes.
- `tomlkit` is a new dependency, so `uv.lock` changes in the same commit.
- The app's Hugging Face token editor (`app/Sources/Earshot/Engine/HFConfig.swift`) moves onto `config set`,
  so there is one writer.

**Per recording.** `session.json` gains `plan` (the ids that ran) and per-step `name`, `kind`, `settings` and
`last_run`. A past recording still shows cards for a plugin that was later removed: status only, no switch.
A plugin enabled later can be run on an old recording from its card ("Not run · **Run**"), for example to send
an old meeting to Drive.

### 3.6 Audio steps

Built last, after a trial (phase 7).

- **`replaces = "audio"` only, at first.** The plugin writes `recording.<id>.wav`, and transcription uses it.
  The original `recording.wav` and `tracks/` are never overwritten.
- **The runner validates the output** before accepting it: same sample rate, channel count and frame count
  (within one buffer) as the input. A mismatch fails the step.
- **Several audio steps** chain in the derived order. `session.json` records
  `artifacts.audio = {file, by: [ids]}`, and transcription records which audio it used.
- **A failed or removed audio step** falls back to the previous version, with a warning on its card.
  Transcription never gets skipped because of it.
- **Switching an audio step on or off** doesn't re-run anything. Transcription shows "Transcribed from the
  original audio" with **Transcribe Again…**.
- **Replaced files** are deleted when their step is switched off and its recording is next re-transcribed.
- **App-only.** The terminal prints a notice that it skips audio steps.
- **Later, `replaces = "tracks"`** (clean one source, then re-mix). It needs a helper
  `merge --session DIR --track ID=FILE --output FILE` that never touches `capture.json` or `recording.wav`,
  an exact naming rule for track ids (`app:us.zoom.xos`), and the same validation per track. Deferred until a
  plugin needs it.

### 3.7 Protocol changes (`docs/engine-protocol.md`, section 3)

All additive. The doc's closed status list is extended explicitly, and the app treats an unknown status as
`pending`.

| Addition | Content |
|---|---|
| `ownscribe plugins --json` | Per step: manifest, `enabled`, `subtitle` (composed by the backend), settings (secrets as `{"set": true}`), `problems` (advisory; they never flip `doctor`'s `ok`), `consent` |
| `ownscribe status SESSION --json` | The `plan` with statuses and stale flags, read-only |
| `ownscribe config set KEY VALUE` | The single config writer |
| `ownscribe run` | As in 3.4 |
| `doctor --json` | Gains `steps` (same data as `plugins`). Existing keys stay |
| Statuses | Adds `ready`, `committing` and `cancelled`. `stale` is a separate field: `stale: {reason}` |
| `plan` event | `steps: [{id, name, kind, status, stale?, used?}]`. `used` is, for example, the model |
| `step` event | `step`, `status`, `detail?`, `message?` |
| `stage` events | Gain `step`, from a stage-to-step map in the runner's event sink |
| `artifact` event | `step`, `name`, `path` (session-relative), `tab?` |
| `result` event | `step`, `text`, `links` |
| `error` event | Gains `step` |

`transcript`, `speakers`, `segment`, `summary` and `session_moved` stay. Until the generic cards land, the
runner also writes the legacy `stages.vault`, `vault_note` and `vault_preview`. `session.load` aliases them,
so a session holds only one of each.

### 3.8 UI

- **Cards.** `ForEach(plan.steps)` over one generic card: title, status, subtitle, progress and sub-steps
  (diarization's four are kept), foot line, inline settings, and buttons from state and `result.links`.
  - Switches appear only on the live recording's cards. A past recording's cards show status, what was used
    ("Transcribed with turbo") and actions.
  - No "Off this Mac" badge: the subtitle names the destination ("Claude · Personal vault",
    "Google Drive · My Drive/Meetings").
- **Results.** Transcript stays a core tab with the speaker fields. Every `artifact` with a `tab` becomes a tab
  rendered by the existing markdown view. "Summary" becomes the summarization step's artifact.
- **Review.** `VaultReviewSheet` stays as is for Obsidian, including its empty-title check and "already
  filed" text. A generic sheet waits for a second interactive step.
- **Settings ▸ Pipeline.**
  - A standard list of steps with switches, plus **Add…** and **Reveal Plugins Folder**.
  - The selected step's form appears on a grouped `Form`. Field types map to `Toggle`, `TextField`,
    `SecureField`, `Picker` (with "Other…" for `allow_other`), `TextEditor`, and a path field with **Choose…**.
  - The Hugging Face tab keeps its token test.

### 3.9 Secrets and trust

- **Secrets.** Existing ones (`hf_token`, `api_key`) stay in `config.toml` (mode 600). New `secret` settings
  go into the login Keychain via the backend, but only when a plugin needs one. The Drive plugin doesn't.
  How Keychain prompts behave for an ad-hoc-signed app and a uv-managed Python needs a spike first.
- **Consent.** No add-in code runs before consent: not `check`, not `run`.
  - Enabling shows a sheet with the plugin's folder and what it declares it sends.
  - Consent is stored in `~/.config/ownscribe/plugins/consent.json` as `{id: hash}`. The hash covers
    `plugin.toml` plus the files listed in `files`, so caches, logs and tokens the plugin writes don't
    invalidate it.
  - A changed plugin asks again.
  - The terminal skips unconsented plugins, with a notice.
- **What consent doesn't do.** A plugin is still arbitrary code running as you; `off_mac` is a declaration,
  not enforcement.
- **Existing off-Mac steps** (Obsidian filing, and Claude or remote summarization) count as already consented.
  Summarization is off the Mac when the backend is `claude`, or when the `openai` or `ollama` host isn't local.

### 3.10 Deliberately left out

- Swappable transcription or diarization engines. Choosing the Whisper model *is* in (section 4).
- Reordering steps, profiles per recording, and a parallel scheduler.
- A plugin SDK, a marketplace, auto-update and sandboxing.
- A generic review sheet, until a second interactive step exists.
- Moving Obsidian's category map (`vault_filing.py:27-36`) into settings.
- New terminal features beyond running enabled, consented, non-audio add-ins.

## 4. Built-ins mapped onto the design

| Step | Kind | Needs → produces | Code moves from → to | Settings (existing keys) |
|---|---|---|---|---|
| `transcription` | core | `audio`, `turns?` → `transcript` | `jobs.py:163-234` → `steps/builtin/transcription.py` | `[transcription] model` (inline), `language`, `initial_prompt` |
| `diarization` | processor, hosted by transcription | `audio` → `turns`, `speakers` | Same unit; writes `diarization.json` | `[diarization] enabled`, `min_speakers`, `max_speakers`; the token stays on the Hugging Face tab |
| `summarization` | processor | `transcript` → `summary`, `title` | `jobs.py:267-331` → `steps/builtin/summarization.py` | `[summarization] backend`, `model`, `host`, `template` (inline) |
| `obsidian` | destination, interactive | `transcript`, `speakers?`, `title?` → note | `jobs.py:423-478` → `steps/builtin/obsidian.py`; `vault_filing.py` unchanged | `[obsidian] enabled`, `vault_dir`, `confirm_before_filing` |

**Transcription settings.**

| Setting | Type | Options |
|---|---|---|
| `model` | choice, inline, `allow_other` | `tiny`, `base`, `small`, `medium`, `large-v3`, and the full repo ids `mlx-community/whisper-large-v3-mlx-8bit`, `mlx-community/whisper-large-v3-turbo`, `mlx-community/whisper-large-v3-turbo-q4` and `mlx-community/distil-whisper-large-v3` |
| `language` | choice | Auto-detect, then languages |
| `initial_prompt` | text | Words and names to prime Whisper with |

- **Model labels.** Each option is labelled with its size and "downloaded" or "downloads on first use". The
  cache is checked per option, since `_whisper_cached` covers only the configured model (`jobs.py:554-561`).
- **The current value** always shows, even when it isn't in the list.
- **Which models are listed.** Only ones known to load. In an earlier benchmark, the turbo 8-bit and 4-bit
  conversions (`model.safetensors` layout) failed with mlx-whisper 0.4.3; turbo, turbo-q4 and distil loaded.
- **Where it's stored.** The choice goes into `[transcription] model`, so the terminal uses the same model.
- **What a change affects.** New recordings use the new model. Open recordings show what they were
  transcribed with, and **Transcribe Again…**.

**Not steps, and unchanged:** speaker naming and role suggestion (`jobs.py:377-391`, which the app never
requests). The folder rename is done by the runner (3.4).

## 5. Google Drive, end to end

| Approach | Setup | Gets you | Costs |
|---|---|---|---|
| **A. Copy into the Google Drive for desktop folder** | None beyond the Drive app | Files appear in Drive; works offline and syncs later | No Drive link; Markdown files, not Google Docs |
| B. `rclone copy` | Install and configure rclone | A link; can convert to Docs | Another tool and its own token file |
| C. Drive API with OAuth | A Google Cloud project, OAuth client, sign-in, token storage | Real Google Docs and links | Most code; needs the secrets work |

**Recommendation: A**, as a bundled add-in in `plugins/gdrive/`, off by default. It needs no secrets, and it
proves the add-in path with a real use. C can replace it later behind the same card.

- **Account folder.** Google Drive for desktop keeps a `GoogleDrive-<account>` folder under
  `~/Library/CloudStorage`. The plugin's `check` only lists the folders there and never reads inside them, so
  the macOS privacy prompt for Drive files appears on the first send, not at app launch.
- **Mirror mode** keeps the folder elsewhere, so there is a `path` setting with the detected folder as its
  default.
- **Sending.** No review step: it sends when the pipeline reaches it. **Send Again** appears after a rename.
- **Replace or keep.** "Replace if unchanged" compares size and modification time, since an online-only file
  can't be hashed; otherwise it writes a new file.

What the user sees:

1. Settings ▸ Pipeline ▸ Google Drive ▸ switch on. A sheet: "Sends the summary and transcript to Google Drive.
   Audio is never sent." **Enable**.
2. A form: Drive folder (detected), folder in My Drive (`Meetings`), what to send (summary, or summary and
   transcript).
3. To replace Obsidian, switch Obsidian off in the same list.
4. After a recording, a card: "Google Drive · My Drive/Meetings". It ends with "Copied · Google Drive uploads
   it" and **Show in Finder**.
5. After a speaker rename: "Sent before the speakers were renamed" and **Send Again**.

## 5b. Example: noise cancellation as a plugin

Not built as part of this plan. It shows that an audio step is an ordinary add-in once phase 7 lands.

```toml
id = "denoise"
name = "Noise cancellation"
kind = "processor"
protocol = 1
exec = "./run"
files = ["run"]
needs = ["audio"]
replaces = "audio"

[[settings]]
key = "strength"
type = "choice"
label = "Strength"
options = ["Light", "Strong"]
default = "Light"
```

- **What it receives:** the current audio file's path, and where to write `recording.denoise.wav`.
- **What it must keep:** 24 kHz mono and the same length. The runner checks, and fails the step otherwise.
  Engines that work at 48 kHz (RNNoise, DeepFilterNet) must resample both ways.
- **What you see:** a "Noise cancellation" card ahead of Transcription. Switching it on or off later shows
  **Transcribe Again…** on Transcription.
- **No `off_mac`:** it works on audio.
- **Engines:** ffmpeg's `afftdn`, `anlmdn` or `arnndn` (ffmpeg 9 here has all three), or DeepFilterNet
  with its dependencies inside the plugin.
- **Cleaning only the microphone** needs `replaces = "tracks"` (3.6, later).

## 6. Migration

- **Old sessions.** No file is rewritten.
  - With no `plan`, the reader uses the built-in order for whichever stages exist.
  - `vault` reads as `obsidian`, `vault_note` as its `last_run` result, and `vault_preview` as its draft.
  - With no `last_run`, a recording is never flagged stale.
  - With no `diarization.json`, Transcribe Again re-runs diarization and maps the labels by time overlap.
  - Folders with only `transcript.md` (from the terminal) keep working through the existing fallback
    (`PipelineModel.swift:300-307`).
- **Old config.** No keys move.
- **App switches into `config.toml`** (phase 0). Copy a UserDefaults value only if you set it yourself
  (`object(forKey:) != nil`), then stop reading it. When copying diarization **on**, also write
  `ask_speaker_names = false` unless it is already set, so the terminal doesn't start asking Claude for
  speaker roles.
- **Terminal pipeline.** `pipeline.py` keeps its own flow. In phase 5 it gains: run enabled, consented,
  non-audio add-ins after its own steps; a destination with review uses `click.confirm` on a TTY. Terminal
  folders without `session.json` are read as legacy sessions.
- **Old commands.** `process`, `speakers`, `resummarize` and `file` keep working as wrappers over the runner.

## 7. Phases

Each phase ships alone and leaves the app working. Every phase runs `uv run pytest` and
`uv run ruff check src/ tests/`. The effort column is a rough estimate.

| # | Phase | Delivers | Main files | Verify | Days |
|---|---|---|---|---|---|
| 0 | **First slice.** In order: (a) keep the transcript when diarization fails, as its own commit; (b) `ownscribe config set` with `tomlkit`; (c) a model picker on the Transcription card that records the model on the stage and shows "Transcribed with X", plus **Transcribe Again…** with a confirmation that names will be reset (until phase 1); (d) a switch on the Obsidian card; (e) the diarize, summarize and template switches move into `config.toml` (safe migration, section 6) | Goals 2 and 3 | `mlx_transcriber.py`, `jobs.py`, `cli.py`, new `config_write.py`, `pyproject.toml`, `uv.lock`; `PipelineModel.swift`, `PipelinePanel.swift`, `Prefs.swift`; `mock_engine.py` | `tests/test_config_write.py` against a copy of the real `config.toml` (comments, mode, backup). A diarization-failure test. By hand with the mock: switch Obsidian off, change the model, Transcribe Again | 2–3 |
| 1 | **Speaker turns saved.** `diarization.json`; Transcribe Again reuses it; label mapping by overlap | Names survive Transcribe Again | `mlx_transcriber.py`, `jobs.py`, `session.py` | Tests: same turns give the same labels and names; changed turns map by overlap and list the unmatched | 2 |
| 2 | **Runner, as a vertical slice.** The step registry, `run`, `status`, the session lock, merging stage writes, rename-once, the new statuses, built behind today's commands. First wire **summarization** end to end (backend, mock, app), then move the other built-ins over | No visible change; one code path | New `src/ownscribe/steps/{base,registry,runner}.py`, `steps/builtin/*`; `jobs.py` (wrappers), `session.py`, `cli.py`, `docs/engine-protocol.md`; tests' patch targets updated (`tests/test_jobs.py:115-116`) | `tests/test_steps.py` (ordering, skip, lock, merge, rename once). Fixture event streams recorded from the real runner, replayed by the mock (section 8) | 4–5 |
| 3 | **Generic cards and tabs** in the app, built-ins only. Obsidian keeps its sheet; review on never drafts automatically; commit by draft id | The panel is data-driven | `PipelineModel.swift` (event handling as a pure reducer), `PipelinePanel.swift`, `History.swift`, `Sidebar.swift`, `Tools.swift`, `AppModel.swift` | `--replay-events FILE --dump-state` snapshots under pytest; a before-and-after diff over every real recording (section 8); by hand with the mock (`OWNSCRIBE_MOCK_VAULT=ask`, `auto`, `off`) | 5–6 |
| 4 | **Settings ▸ Pipeline.** The step list, switches, schema forms (including the transcription settings) | All steps configurable in one place | `SettingsView.swift`, new `Views/SchemaForm.swift`, `cli.py` (`plugins`), `HFConfig.swift` (onto `config set`) | By hand: switch steps off, change settings, record with the mock; the terminal honours the same switches | 3 |
| 5 | **Add-ins and Google Drive.** `ExternalStep`, folder scan (bundled and user), consent, the environment and timeouts, Show Log, Drive as a bundled add-in, the terminal hook | Goal 1 for text steps | New `steps/external.py`, `steps/registry.py`, `plugins/gdrive/`, `docs/plugins.md`, `mock_engine.py`, the Add and consent UI | `tests/test_external_step.py` with fixture plugins: no code runs before consent, events forwarded, forbidden events dropped, timeout and cancel kill the group, bad manifest rejected. By hand: enable Drive, record, find the file | 5–6 |
| 6 | **Stale destinations and Update…** Replace or File as New; Send Again; Run on an old recording | Safe re-sending | `steps/runner.py`, `PipelineModel.swift`, `PipelinePanel.swift` | Tests: a rename flags a destination stale with no LLM call; Replace only when the file is unchanged | 2–3 |
| 7 | **Audio steps** (only if the trial below shows a benefit). `replaces = "audio"`, validation, ordering and fallback | Goal 1 for audio | `steps/runner.py`, `session.py`, `mock_engine.py` (a fake audio step) | Tests with a fixture audio plugin (ffmpeg `afftdn`): transcription gets the new file, the original is untouched, a bad format fails the step, a failure falls back | 2 |
| 8 | *Optional:* `replaces = "tracks"` with the helper merge, Keychain secrets, the Drive API, parallelism | | | Each starts with a spike | |

**The trial before phase 7** takes about an hour. Run ffmpeg `afftdn` and `arnndn` by hand on three noisy real
recordings, then compare transcripts (word counts and a read-through) and speaker agreement.

**Order of delivery.**

- Phase 0 delivers goals 2 and 3.
- Phases 2–4 make every step switchable and configurable in one place.
- Phase 5 delivers add-ins.
- Phase 7 adds audio add-ins.

## 8. Testing

The app has no Swift test target, so the plan leans on these:

1. **Fixtures as the contract.** pytest records the real runner's event streams (with fake steps) as JSONL
   files. `mock_engine.py` replays them instead of hand-building events, so the mock can't drift from the
   backend.
2. **A replay flag on the app.** Phase 3 makes event handling a pure reducer and adds
   `Earshot --replay-events FILE --dump-state`. pytest runs it on the fixtures and snapshots the card state.
3. **A before-and-after diff.** `Earshot --dump-session DIR` over every recording in the real output folder,
   run before and after phase 3, then diffed. This checks the migration aliases in section 6.
4. **Config writes** tested against a copy of the real `config.toml`, not just the default text.

## 9. Risks

- **Phase 3 rewrites the app's largest model with no Swift tests.** The mitigations are the reducer, the
  replay flag and the before-and-after diff (section 8), and doing summarization end to end in phase 2 first.
- **Config rewriting.** A bug damages `config.toml`. The write is atomic, keeps mode 600, makes a backup, and is
  tested against the real file.
- **Speaker mapping by overlap** can mislabel speakers when the turns differ a lot. Unmatched speakers are
  shown, never guessed.
- **macOS privacy prompts.** The Drive folder may prompt again after each rebuild of the ad-hoc-signed app,
  as the microphone does (`app/README.md:101`). That is unverified.
- **Slow toggles.** Each config write is a `uv run` start. The UI updates optimistically and reconciles from
  the next listing.
- **Trust.** Consent informs; it doesn't contain. A plugin can read anything you can.
- **Scope.** Phase 8 waits for real need. Audio steps wait for the trial.

## 10. Open questions

| # | Question | Recommendation |
|---|---|---|
| 1 | Should Drive be a bundled add-in or a built-in step? | A bundled add-in: it proves the add-in path with a real use |
| 2 | Should newly enabled steps be runnable on old recordings? | Yes: they show as "Not run · **Run**" on the card |
| 3 | When Transcribe Again changes the speakers a lot, what happens to the names? | Carry them where the overlap is clear; list the rest to name again |
| 4 | Should Obsidian draft from the summary instead of the transcript? | Leave as is; it's a separate change to the note |
| 5 | Should the Obsidian category map become a setting? | Yes, as a follow-up; it needs a `map` field type |
| 6 | Where should new secrets live? | Keychain via the backend, after a spike, and only when a plugin needs one |
| 7 | Do audio steps happen at all? | Decided by the trial before phase 7 |

### Critical files for implementation

- `src/ownscribe/jobs.py`
- `src/ownscribe/session.py`
- `src/ownscribe/transcription/mlx_transcriber.py`
- `app/Sources/Earshot/Model/PipelineModel.swift`
- `app/Sources/Earshot/Views/PipelinePanel.swift`
- `scripts/mock_engine.py`
