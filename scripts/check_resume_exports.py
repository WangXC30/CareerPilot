"""Run in the isolated docqa image after browser downloads and DOCX rendering."""

import argparse
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pdf2image import convert_from_path
from pypdf import PdfReader
from temporary_artifacts import temporary_artifacts


def normalized(value):
    return re.sub(r"\s+", "", value)


def run(root, output):
    reports = []
    for size in ("short", "long"):
        with ZipFile(root / "ui-hiring" / f"sample-{size}.docx") as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
            lines = [
                n.text
                for n in document.iter(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
                )
                if n.text
            ]
        render_dir = root / "export-qa" / f"{size}-docx"
        word_pdfs = list(render_dir.glob("*.pdf"))
        assert len(word_pdfs) == 1
        for fmt, pdf in [
            ("pdf", root / "ui-hiring" / f"sample-{size}.pdf"),
            ("docx", word_pdfs[0]),
        ]:
            reader = PdfReader(pdf)
            text = "\n".join(page.extract_text() for page in reader.pages)
            for line in lines:
                assert normalized(line) in normalized(text), (size, fmt, line, text)
            assert all(page.extract_text().strip() for page in reader.pages)
            if size == "long":
                assert len(reader.pages) >= 2
            else:
                assert len(reader.pages) == 1
            if fmt == "pdf":
                directory = output / f"{size}-pdf"
                directory.mkdir(exist_ok=True, parents=True)
                for number, image in enumerate(convert_from_path(pdf, dpi=120), 1):
                    image.save(directory / f"page-{number}.png")
            reports.append(
                {
                    "sample": size,
                    "format": fmt,
                    "pages": len(reader.pages),
                    "all_logical_document_lines_present": True,
                    "blank_pages": False,
                }
            )
    result = {
        "samples": reports,
        "source": "synthetic user-confirmed facts",
        "visual_review": "required separately for each generated page PNG",
    }
    (output / "content-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    with temporary_artifacts("export-qa") as output:
        run(args.root, output)
