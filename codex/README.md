# Lingling for Codex on macOS

This is the Codex CLI version of Lingling.

It listens to Codex lifecycle hooks and speaks when:

- a Codex turn finishes (`Stop`)
- Codex needs permission (`PermissionRequest`)
- a subagent finishes (`SubagentStop`, off by default)

It auto-detects Thai vs English, shows a macOS desktop notification, prefers Edge TTS neural voices, and falls back to the built-in macOS `say` command when offline or when Edge TTS is unavailable.

## Install

From the repository root:

```bash
zsh codex/install.sh
```

Then restart Codex.

If Codex asks you to review or trust command hooks, open `/hooks` and approve the Lingling hooks.

## Test

```bash
python3 ~/.codex/lingling/lingling.py --test
```

You should hear one Thai sentence and one English sentence.

## Configuration

Lingling reads:

```text
~/.codex/lingling/config.json
```

Example:

```json
{
  "enabled": true,
  "language": "auto",
  "voice_engine": "auto",
  "speak_on_complete": true,
  "speak_on_permission": true,
  "speak_on_subagent": false,
  "desktop_notifications": true,
  "max_spoken_chars": 420,
  "edge_voice_th": "th-TH-PremwadeeNeural",
  "edge_voice_en": "en-US-AriaNeural",
  "edge_rate": "-10%",
  "say_voice_th": "Kanya",
  "say_voice_en": "Samantha",
  "say_rate": 190
}
```

`language` may be `auto`, `th`, or `en`.

`voice_engine` may be `auto`, `edge`, or `say`.

## How it differs from the Claude Code version

The Claude plugin injects an instruction asking Claude to finish with a `🔊` summary line. The Codex version intentionally does not inject prompt context. Instead, it uses Codex lifecycle hooks and builds a short local summary from the final assistant message. This avoids altering the user's Codex prompt and uses zero extra model/API tokens.

If a future Codex response already contains a `🔊` line, Lingling will prefer that line automatically.

## Files

```text
codex/
├── install.sh
├── lingling.py
└── README.md
```

Runtime files are kept under:

```text
~/.codex/lingling/
├── lingling.py
├── config.json          # optional; create this yourself
├── lingling.log
├── state.json
└── venv/                # private edge-tts install
```

The installer preserves existing `~/.codex/hooks.json` entries and creates a timestamped backup before editing it.

## Uninstall

Remove the Lingling hook entries from `~/.codex/hooks.json`, then delete:

```bash
rm -rf ~/.codex/lingling
```
