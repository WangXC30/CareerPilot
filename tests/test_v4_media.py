"""Real local PDF/OCR/video extraction using disposable media only."""

import base64
import io
import subprocess

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot.growth_models import GrowthRecord, GrowthRevision
from tests.test_v4_collaboration import ok


def pdf_bytes():
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 18 Tf 20 100 Td (CareerPilot Evidence) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def upload(client, headers, kind, content):
    return client.post(
        "/api/v1/media-index",
        headers=headers,
        json={
            "kind": kind,
            "title": "本人授权合成文档",
            "source_url": "",
            "license_basis": "self-authored synthetic content",
            "content_base64": base64.b64encode(content).decode(),
            "rights_confirmed": True,
            "confirmed": True,
        },
    )


def test_pdf_extract_and_delete_all_revisions(system):
    client, engine, alice, bob, _, _ = system
    result = ok(upload(client, alice, "pdf", pdf_bytes()), 201)
    assert result["method"] == "pdf_text"
    assert result["segments"][0]["anchor"] == "page:1"
    assert "CareerPilot Evidence" in result["segments"][0]["text"]
    assert client.get("/api/v1/media-index", headers=bob, params={"q": "CareerPilot"}).json() == []
    assert (
        len(ok(client.get("/api/v1/media-index", headers=alice, params={"q": "CareerPilot"}))) == 1
    )
    assert client.delete("/api/v1/media-index/" + result["id"], headers=bob).status_code == 404
    ok(client.delete("/api/v1/media-index/" + result["id"], headers=alice))
    assert ok(client.get("/api/v1/media-index", headers=alice)) == []
    with Session(engine) as db:
        assert db.scalar(select(GrowthRecord).where(GrowthRecord.id == result["id"])) is None
        assert (
            list(db.scalars(select(GrowthRevision).where(GrowthRevision.record_id == result["id"])))
            == []
        )


def generated_image(tmp_path):
    path = tmp_path / "sample.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=800x160",
            "-frames:v",
            "1",
            "-vf",
            "drawtext=text=CareerPilot:fontcolor=black:fontsize=56:x=20:y=40",
            str(path),
        ],
        check=True,
        timeout=15,
    )
    return path.read_bytes()


def test_local_image_ocr(system, tmp_path):
    client, _, alice, _, _, _ = system
    result = ok(upload(client, alice, "image", generated_image(tmp_path)), 201)
    assert result["method"] == "local_ocr"
    assert "CareerPilot" in result["segments"][0]["text"]


def test_local_video_first_frame_ocr(system, tmp_path):
    client, _, alice, _, _, _ = system
    source = tmp_path / "image.png"
    source.write_bytes(generated_image(tmp_path))
    video = tmp_path / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-loop",
            "1",
            "-i",
            str(source),
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        timeout=15,
    )
    result = ok(upload(client, alice, "video", video.read_bytes()), 201)
    assert result["method"] == "first_frame_local_ocr"
    assert result["segments"][0]["anchor"] == "frame:0"
