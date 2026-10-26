"""Offline English draft decoder, invoked only with private temporary file paths."""

import json
import resource
import subprocess
import sys
from pathlib import Path


def main():
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    source = Path(sys.argv[1])
    wav = source.parent / "speech.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-threads",
            "1",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(source),
            "-t",
            "30",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(wav),
        ],
        check=True,
        timeout=8,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    model = "/usr/share/pocketsphinx/model/en-us/"
    result = subprocess.run(
        [
            "pocketsphinx_continuous",
            "-hmm",
            model + "en-us",
            "-lm",
            model + "en-us.lm.bin",
            "-dict",
            model + "cmudict-en-us.dict",
            "-infile",
            str(wav),
            "-logfn",
            "/dev/null",
        ],
        check=True,
        timeout=25,
        capture_output=True,
    )
    text = result.stdout.decode("utf-8").strip()[:10000]
    if not text:
        raise ValueError("no_speech")
    print(json.dumps({"text": text}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(2)
