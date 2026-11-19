import Foundation
import Observation

/// The step switches and the Whisper model, as config.toml has them, so the app and the terminal
/// `ownscribe` agree. Read from `ownscribe doctor`; changed with `ownscribe config set`, shown at once
/// and put back if the write fails.
@MainActor
@Observable
final class PipelineSettings {
    struct ModelChoice: Identifiable, Hashable, Sendable {
        let model: String
        let cached: Bool
        var id: String { model }
    }

    var diarize = true
    var summarize = true
    var template = "meeting"
    var obsidian = false
    /// `[transcription] model`; nil until the first doctor report.
    var model: String?
    var modelCached = true
    var modelChoices: [ModelChoice] = []
    var lastError: String?

    @ObservationIgnored var ownscribe: () throws -> ToolCommand = { throw NSError(domain: "ownscribe", code: 1) }
    /// After a write: read the doctor report again (subtitles, vault state, "downloads on first use").
    @ObservationIgnored var onChanged: (() -> Void)?
    /// Keys being written: a doctor report from before the write mustn't put the old value back.
    @ObservationIgnored private var writing: [String: Int] = [:]

    func apply(_ report: DoctorReport) {
        if writing["diarization.enabled"] == nil, let v = report.diarize { diarize = v }
        if writing["summarization.enabled"] == nil, let v = report.summarize { summarize = v }
        if writing["summarization.template"] == nil, let v = report.template { template = v }
        if writing["obsidian.enabled"] == nil, let v = report.vault?.enabled { obsidian = v }
        if writing["transcription.model"] == nil, let v = report.whisperModel {
            model = v
            modelCached = report.whisperCached
        }
        if !report.modelChoices.isEmpty { modelChoices = report.modelChoices }
    }

    func setDiarize(_ on: Bool) { change("diarization.enabled", on ? "true" : "false", \.diarize, on) }
    func setSummarize(_ on: Bool) { change("summarization.enabled", on ? "true" : "false", \.summarize, on) }
    func setTemplate(_ name: String) { change("summarization.template", name, \.template, name) }
    func setObsidian(_ on: Bool) { change("obsidian.enabled", on ? "true" : "false", \.obsidian, on) }

    func setModel(_ name: String) {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name != model else { return }
        modelCached = modelChoices.first { $0.model == name }?.cached ?? true
        change("transcription.model", name, \.model, Optional(name))
    }

    private func change<V: Equatable>(_ key: String, _ text: String, _ path: ReferenceWritableKeyPath<PipelineSettings, V>, _ value: V) {
        let previous = self[keyPath: path]
        guard previous != value else { return }
        self[keyPath: path] = value
        lastError = nil
        let generation = (writing[key] ?? 0) + 1
        writing[key] = generation
        Task {
            let error = await write(key, text)
            if writing[key] == generation { writing[key] = nil }
            if let error {
                if self[keyPath: path] == value { self[keyPath: path] = previous }
                lastError = "Couldn't save \(key) to config.toml: \(error)"
            }
            onChanged?()
        }
    }

    /// `ownscribe config set KEY VALUE --json`; nil on success, else why not.
    func write(_ key: String, _ value: String, ifMissing: Bool = false) async -> String? {
        let tool: ToolCommand
        do { tool = try ownscribe() } catch { return error.localizedDescription }
        var args = ["config", "set", key, value, "--json"]
        if ifMissing { args.insert("--if-missing", at: 4) }
        switch await Tools.run(tool, args, timeout: 60) {
        case let .success(r):
            if let json = r.json, json.bool("ok") == true { return nil }
            return r.json?.string("message") ?? (r.stderrTail.isEmpty ? "exit \(r.exitCode)" : r.stderrTail)
        case let .failure(error):
            return error.localizedDescription
        }
    }

    // MARK: - Moving the app's old switches into config.toml

    /// Once: the diarize, summarize and template switches used to live in the app's own preferences
    /// (with defaults that could disagree with config.toml). An app that has been used before keeps
    /// behaving as it did, so its values are written to config.toml; a fresh install follows the file.
    /// Turning diarization on this way also sets `ask_speaker_names = false` unless the file has it, so
    /// the terminal doesn't start asking Claude about speakers.
    func migrateAppSwitches(using report: DoctorReport) async {
        let defaults = Prefs.defaults
        guard !defaults.bool(forKey: Prefs.switchesMigrated) else { return }
        // Not against the mock engine: that would mark the real config.toml as done.
        guard let bundle = Bundle.main.bundleIdentifier, let tool = try? ownscribe(),
              !([tool.executable] + tool.arguments([])).joined(separator: " ").contains("mock_engine") else { return }
        let usedBefore = !(defaults.persistentDomain(forName: bundle) ?? [:]).isEmpty
        if usedBefore {
            let appDiarize = Prefs.bool(Prefs.diarize)
            let appSummarize = Prefs.bool(Prefs.summarize)
            let appTemplate = Prefs.string(Prefs.template)
            var failed = false
            if let current = report.diarize, current != appDiarize {
                failed = await write("diarization.enabled", appDiarize ? "true" : "false") != nil || failed
                if appDiarize, !failed {
                    failed = await write("diarization.ask_speaker_names", "false", ifMissing: true) != nil || failed
                }
            }
            if let current = report.summarize, current != appSummarize {
                failed = await write("summarization.enabled", appSummarize ? "true" : "false") != nil || failed
            }
            if !appTemplate.isEmpty, let current = report.template, current != appTemplate {
                failed = await write("summarization.template", appTemplate) != nil || failed
            }
            if failed { return }  // try again next launch
            diarize = appDiarize
            summarize = appSummarize
            if !appTemplate.isEmpty { template = appTemplate }
        }
        defaults.set(true, forKey: Prefs.switchesMigrated)
        for key in [Prefs.diarize, Prefs.summarize, Prefs.template] { defaults.removeObject(forKey: key) }
        onChanged?()
    }

    // MARK: - Labels

    /// "Whisper large-v3-turbo", "Whisper large-v3 · 8-bit", "distil-whisper-large-v3".
    static func label(for model: String) -> String {
        var name = model.split(separator: "/").last.map(String.init) ?? model
        if name.hasPrefix("distil-whisper-") { return "Distil-Whisper " + name.dropFirst("distil-whisper-".count) }
        if !name.contains("whisper") { name = "whisper-" + name }  // the short names: "small" → "Whisper small"
        name = name.replacingOccurrences(of: "-mlx", with: "")
        name = name.replacingOccurrences(of: "whisper-", with: "Whisper ")
        for bits in ["8bit", "4bit"] { name = name.replacingOccurrences(of: "-\(bits)", with: " · \(bits.dropLast(3))-bit") }
        return name
    }
}
