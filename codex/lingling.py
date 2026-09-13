#!/usr/bin/env python3
"""Lingling for Codex on macOS.

Reads Codex lifecycle-hook JSON from stdin and speaks useful events.
Designed for Stop, PermissionRequest, and SubagentStop hooks.

No API key is required. Edge TTS is preferred when installed by install.sh;
macOS `say` is the offline fallback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = Path.home()
BASE_DIR = HOME / ".codex" / "lingling"
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"
LOG_PATH = BASE_DIR / "lingling.log"
VENV_BIN = BASE_DIR / "venv" / "bin"

DEFAULTS = {
    "enabled": True,
    "language": "auto",              # auto | th | en
    "voice_engine": "auto",          # auto | edge | say
    "speak_on_complete": True,
    "speak_on_permission": True,
    "speak_on_subagent": False,
    "desktop_notifications": True,
    "skip_under_chars": 35,
    "max_spoken_chars": 420,
    "edge_voice_th": "th-TH-PremwadeeNeural",
    "edge_voice_en": "en-US-AriaNeural",
    "edge_rate": "-10%",
    "say_voice_th": "Kanya",
    "say_voice_en": "Samantha",
    "say_rate": 190,
}

THAI_RE = re.compile(r"[\u0E00-\u0E7F]")
LATIN_RE = re.compile(r"[A-Za-z]")
FENCE_RE = re.compile(r"```.*?```", re.S)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
URL_RE = re.compile(r"https?://\S+")
MARKDOWN_RE = re.compile(r"[*_#>|]+")
BULLET_RE = re.compile(r"(?m)^\s*(?:[-*+•]|\d{1,2}[.)])\s+")
SPOKEN_MARKER = "🔊"


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_PATH.exists():
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def log(message: str) -> None:
    try:
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def is_duplicate(event: str, data: dict, text: str = "") -> bool:
    """Avoid duplicate speech from repeated/background hook events."""
    key_src = "|".join([
        event,
        str(data.get("session_id", "")),
        str(data.get("turn_id", "")),
        text[:400],
    ])
    key = hashlib.sha256(key_src.encode("utf-8", "ignore")).hexdigest()
    state = load_state()
    now = time.time()
    if state.get("last_key") == key and now - float(state.get("last_at", 0)) < 120:
        return True
    state.update({"last_key": key, "last_at": now})
    save_state(state)
    return False


def strip_for_language(text: str) -> str:
    text = FENCE_RE.sub(" ", text)
    text = URL_RE.sub(" ", text)
    text = INLINE_CODE_RE.sub(" ", text)
    return text


def detect_lang(text: str, default: str = "th") -> str:
    probe = strip_for_language(text)
    thai = len(THAI_RE.findall(probe))
    latin = len(LATIN_RE.findall(probe))
    total = thai + latin
    if total == 0:
        return default
    if thai == 0:
        return "en"
    if thai >= 8 and thai / total >= 0.15:
        return "th"
    return "th" if thai > latin else "en"


def remember_lang(lang: str) -> None:
    state = load_state()
    state["last_lang"] = lang
    save_state(state)


def resolve_lang(text: str, cfg: dict) -> str:
    forced = str(cfg.get("language", "auto")).lower().strip()
    if forced in ("th", "en"):
        return forced
    return detect_lang(text, load_state().get("last_lang", "th"))


def clean_text(text: str, lang: str) -> str:
    text = FENCE_RE.sub(" ", text)
    text = INLINE_CODE_RE.sub(lambda m: m.group(1), text)
    text = LINK_RE.sub(r"\1", text)
    text = URL_RE.sub("ลิงก์" if lang == "th" else "link", text)
    text = MARKDOWN_RE.sub(" ", text)
    text = BULLET_RE.sub("", text)
    text = text.replace(SPOKEN_MARKER, " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_marked_summary(message: str) -> str | None:
    tail = message[-1200:]
    idx = tail.rfind(SPOKEN_MARKER)
    if idx < 0:
        return None
    spoken = tail[idx + len(SPOKEN_MARKER):].strip().strip("*_:>- ")
    if not spoken or "```" in spoken or len(spoken) > 800:
        return None
    return spoken


def local_summary(message: str, lang: str, limit: int) -> str:
    """Cheap, zero-token summary: prefer the opening result sentences/paragraphs."""
    marked = extract_marked_summary(message)
    if marked:
        return clean_text(marked, lang)[:limit]

    # Preserve paragraph boundaries before collapsing whitespace.
    raw = FENCE_RE.sub("\n", message)
    raw = LINK_RE.sub(r"\1", raw)
    raw = URL_RE.sub("", raw)
    raw = INLINE_CODE_RE.sub(lambda m: m.group(1), raw)
    lines = []
    for line in raw.splitlines():
        line = re.sub(r"^\s*(?:#{1,6}\s*|[-*+•]\s+|\d{1,2}[.)]\s+)", "", line).strip()
        if not line or line.startswith("|"):
            continue
        if len(line) < 3:
            continue
        lines.append(line)

    text = " ".join(lines)
    text = MARKDOWN_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    # Codex usually leads with the result. Take up to two natural sentence chunks.
    chunks = [c.strip() for c in re.split(r"(?<=[.!?。！？])\s+|\s{2,}", text) if c.strip()]
    chosen: list[str] = []
    for chunk in chunks:
        if len(chunk) < 8:
            continue
        chosen.append(chunk)
        if len(chosen) >= 2 or sum(map(len, chosen)) >= 260:
            break
    summary = " ".join(chosen) if chosen else text
    summary = clean_text(summary, lang)
    if len(summary) > limit:
        summary = summary[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return summary


def edge_tts_path() -> str | None:
    candidates = [VENV_BIN / "edge-tts"]
    found = shutil.which("edge-tts")
    if found:
        candidates.append(Path(found))
    for p in candidates:
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    return None


def play_mp3(path: str) -> bool:
    afplay = shutil.which("afplay") or "/usr/bin/afplay"
    try:
        subprocess.run([afplay, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        return True
    except Exception:
        return False


def speak_edge(text: str, lang: str, cfg: dict) -> bool:
    exe = edge_tts_path()
    if not exe:
        return False
    voice = cfg["edge_voice_th"] if lang == "th" else cfg["edge_voice_en"]
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    try:
        proc = subprocess.run(
            [exe, "--voice", voice, f"--rate={cfg['edge_rate']}", "--text", text, "--write-media", tmp.name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
        if proc.returncode != 0 or os.path.getsize(tmp.name) < 1024:
            return False
        return play_mp3(tmp.name)
    except Exception as exc:
        log(f"edge tts failed: {exc}")
        return False
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def speak_say(text: str, lang: str, cfg: dict) -> bool:
    say = shutil.which("say") or "/usr/bin/say"
    voice = cfg["say_voice_th"] if lang == "th" else cfg["say_voice_en"]
    cmd = [say, "-r", str(cfg["say_rate"]), "-v", voice, text]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        if proc.returncode == 0:
            return True
        subprocess.run([say, text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        return True
    except Exception as exc:
        log(f"say failed: {exc}")
        return False


def desktop_notification(title: str, message: str, cfg: dict) -> None:
    if not cfg.get("desktop_notifications", True):
        return
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.Popen(
            ["/usr/bin/osascript", "-e", f'display notification "{esc(message[:220])}" with title "{esc(title)}"'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def speak(text: str, lang: str, cfg: dict) -> None:
    if not text.strip():
        return
    engine = str(cfg.get("voice_engine", "auto")).lower()
    ok = False
    if engine in ("auto", "edge"):
        ok = speak_edge(text, lang, cfg)
    if not ok and engine in ("auto", "say", "edge"):
        ok = speak_say(text, lang, cfg)
    log(f"speak lang={lang} engine={engine} ok={ok}: {text[:250]}")


def handle_stop(data: dict, cfg: dict) -> None:
    if not cfg.get("speak_on_complete", True):
        return
    message = str(data.get("last_assistant_message") or "").strip()
    if len(message) < int(cfg.get("skip_under_chars", 35)):
        return
    if is_duplicate("Stop", data, message):
        return
    lang = resolve_lang(message, cfg)
    summary = local_summary(message, lang, int(cfg.get("max_spoken_chars", 420)))
    if not summary:
        return
    remember_lang(lang)
    desktop_notification("Lingling • Codex", summary, cfg)
    speak(summary, lang, cfg)


def handle_permission(data: dict, cfg: dict) -> None:
    if not cfg.get("speak_on_permission", True):
        return
    if is_duplicate("PermissionRequest", data, str(data.get("tool_name", ""))):
        return
    lang = str(cfg.get("language", "auto")).lower()
    if lang not in ("th", "en"):
        lang = load_state().get("last_lang", "th")
    if lang == "th":
        text = "Codex ต้องการการอนุญาตเพื่อทำงานต่อ กรุณากลับมาดูที่หน้าต่าง Codex ครับ"
    else:
        text = "Codex needs your permission to continue. Please check the Codex window."
    desktop_notification("Lingling • Permission needed", text, cfg)
    speak(text, lang, cfg)


def handle_subagent_stop(data: dict, cfg: dict) -> None:
    if not cfg.get("speak_on_subagent", False):
        return
    message = str(data.get("last_assistant_message") or "").strip()
    if is_duplicate("SubagentStop", data, message):
        return
    lang = resolve_lang(message, cfg) if message else load_state().get("last_lang", "th")
    summary = local_summary(message, lang, 240) if message else ""
    if lang == "th":
        text = "เอเจนต์ย่อยทำงานเสร็จแล้วครับ" + (f", {summary}" if summary else "")
    else:
        text = "A Codex subagent finished." + (f" {summary}" if summary else "")
    desktop_notification("Lingling • Subagent", text, cfg)
    speak(text, lang, cfg)


def read_stdin_json() -> dict:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}
    except Exception as exc:
        log(f"invalid hook input: {exc}")
        return {}


def run_hook(cfg: dict) -> None:
    data = read_stdin_json()
    event = str(data.get("hook_event_name") or "")
    log(f"event={event} session={data.get('session_id', '')} turn={data.get('turn_id', '')}")
    if event == "Stop":
        handle_stop(data, cfg)
    elif event == "PermissionRequest":
        handle_permission(data, cfg)
    elif event == "SubagentStop":
        handle_subagent_stop(data, cfg)


def test(cfg: dict) -> None:
    print(f"config : {CONFIG_PATH}")
    print(f"log    : {LOG_PATH}")
    print(f"edge   : {edge_tts_path() or 'not installed; using macOS say fallback'}")
    print("Speaking Thai test...")
    speak("สวัสดีครับ Lingling สำหรับ Codex พร้อมทำงานบน Mac แล้วครับ", "th", cfg)
    print("Speaking English test...")
    speak("Lingling for Codex is ready on your Mac.", "en", cfg)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    cfg = load_config()
    if not cfg.get("enabled", True) and not args.test:
        return 0
    try:
        if args.test:
            test(cfg)
        else:
            run_hook(cfg)
    except Exception as exc:
        log(f"unhandled error: {exc}")
    # A notification hook must never break Codex.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
