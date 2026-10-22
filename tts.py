"""English voiceover with word timings, using Kokoro (open-source neural TTS, Apache-2.0, runs on CPU).

speak(text) -> (audio float32 at SR, [(word, start_s, end_s), ...])
"""

import re

import numpy as np

SR = 24000
VOICE = "am_michael"    # male American English voice
SPEED = 1.08            # a little brisk, as Reels narration usually is

_pipeline = None


def pipeline():
    global _pipeline
    if _pipeline is None:
        from kokoro import KPipeline
        _pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    return _pipeline


def _estimate_words(text, start, end):
    """Fallback timing: spread words over the chunk by their length."""
    words = re.findall(r"\S+", text)
    if not words:
        return []
    weights = np.array([len(w) + 2 for w in words], dtype=float)
    edges = start + (end - start) * np.concatenate([[0], np.cumsum(weights) / weights.sum()])
    return [(w, float(edges[i]), float(edges[i + 1])) for i, w in enumerate(words)]


def speak(text, voice=VOICE, speed=SPEED):
    chunks, words, offset = [], [], 0.0
    for result in pipeline()(text, voice=voice, speed=speed):
        audio = result.audio
        if audio is None:
            continue
        audio = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
        dur = len(audio) / SR
        tokens = getattr(result, "tokens", None) or []
        timed = [(t.text, t.start_ts, t.end_ts) for t in tokens
                 if getattr(t, "start_ts", None) is not None and getattr(t, "end_ts", None) is not None
                 and t.text.strip() and re.search(r"\w", t.text)]
        if timed:
            # attach trailing punctuation tokens to the word before them, as subtitles show them
            words += [(w, offset + s, offset + e) for w, s, e in timed]
        else:
            words += _estimate_words(result.graphemes, offset, offset + dur)
        chunks.append(audio.astype(np.float32))
        offset += dur
    audio = np.concatenate(chunks) if chunks else np.zeros(int(SR * 0.5), np.float32)
    return audio, _with_punctuation(text, words)


def _with_punctuation(text, words):
    """Show words as written in the script (with commas, full stops) rather than as tokens."""
    written = re.findall(r"\S+", text)
    norm = [re.sub(r"\W", "", x).lower() for x in written]
    out, i = [], 0
    for w, s, e in words:
        core = re.sub(r"\W", "", w).lower()
        match = next((j for j in range(i, min(i + 3, len(written)))      # look at most 2 words ahead
                      if core and (norm[j] == core or norm[j].startswith(core) or core.startswith(norm[j] or "\0"))),
                     None)
        if match is None:
            out.append((w, s, e))
            continue
        out.append((written[match], s, e))
        i = match + 1
    return out
