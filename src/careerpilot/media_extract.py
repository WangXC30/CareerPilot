"""Bounded local extraction subprocess. Never receives account credentials or remote URLs."""

import json
import resource
import subprocess
import sys
from pathlib import Path


def main():
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    kind, filename = sys.argv[1:]
    path = Path(filename)
    segments = []
    if kind == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        if reader.is_encrypted or len(reader.pages) > 30:
            raise ValueError("unsupported_pdf")
        for index, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            segments.append({"anchor": f"page:{index + 1}", "text": text[:10000]})
    else:
        frames = [(path, "image:1")]
        if kind == "video":
            frame = path.parent / "frame.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-threads",
                    "1",
                    "-filter_threads",
                    "1",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=800:-1",
                    str(frame),
                ],
                check=True,
                timeout=10,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            frames = [(frame, "frame:0")]
        for frame, anchor in frames:
            result = subprocess.run(
                ["tesseract", str(frame), "stdout", "-l", "eng+chi_sim"],
                check=True,
                timeout=15,
                capture_output=True,
            )
            segments.append({"anchor": anchor, "text": result.stdout.decode("utf-8")[:10000]})
    if not any(s["text"].strip() for s in segments):
        raise ValueError("no_text_detected")
    print(
        json.dumps(
            {
                "segments": segments,
                "method": {
                    "pdf": "pdf_text",
                    "image": "local_ocr",
                    "video": "first_frame_local_ocr",
                }[kind],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("extraction_unavailable", file=sys.stderr)
        sys.exit(2)
