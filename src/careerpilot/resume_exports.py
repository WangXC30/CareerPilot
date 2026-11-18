"""One logical document feeds private DOCX and controlled Chromium PDF exports."""

from html import escape
from io import BytesIO
from xml.sax.saxutils import escape as xml_escape
from zipfile import ZIP_DEFLATED, ZipFile

from careerpilot.domain import DomainError
from careerpilot.resumes import KINDS


def _header_title(version):
    name_facts = [
        line.strip()
        for f in version.get("facts", [])
        if f.get("kind") == "name"
        for line in f.get("text", "").splitlines()
        if line.strip()
    ]
    if name_facts:
        return name_facts[0]
    title = str(version.get("title") or "").strip()
    return title or "个人简历"


def paragraphs(version):
    header = _header_title(version)
    result = [("Title", header)]
    used_header_as_name = False
    for kind, label in KINDS.items():
        facts = [f for f in version["facts"] if f["kind"] == kind]
        if not facts:
            continue
        if kind == "name":
            for fact in facts:
                for line in fact["text"].splitlines():
                    clean = line.strip()
                    if not clean:
                        continue
                    if not used_header_as_name and clean == header:
                        used_header_as_name = True
                        continue
                    result.append(("Contact", clean))
            continue
        if kind == "contact":
            for fact in facts:
                result.extend(
                    ("Contact", line.strip()) for line in fact["text"].splitlines() if line.strip()
                )
            continue
        result.append(("Heading1", label))
        for fact in facts:
            result.extend(("Normal", line) for line in fact["text"].splitlines() if line.strip())
    return result


def docx(version):
    parts = []
    for style, text in paragraphs(version):
        # XML 1.0 excludes control characters, even when HTML escaping is safe.
        clean = "".join(c for c in text if ord(c) >= 32 or c in "\t\n\r")
        parts.append(
            f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
            f'<w:r><w:t xml:space="preserve">{xml_escape(clean)}</w:t></w:r></w:p>'
        )
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document {ns}><w:body>{"".join(parts)}<w:sectPr>
<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1020" w:right="1080"
w:bottom="1020" w:left="1080" w:header="567" w:footer="567"/>
</w:sectPr></w:body></w:document>"""
    styles = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles {ns}><w:docDefaults><w:rPrDefault><w:rPr>
<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/>
<w:color w:val="202D32"/><w:sz w:val="21"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>
<w:spacing w:after="100" w:line="310" w:lineRule="auto"/>
</w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>
<w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:after="60"/></w:pPr>
<w:rPr><w:b/><w:color w:val="173C2F"/><w:sz w:val="38"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Contact"><w:name w:val="Subtitle"/>
<w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="140"/>
<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="6" w:color="CBD5CF"/></w:pBdr></w:pPr>
<w:rPr><w:color w:val="5C6D63"/><w:sz w:val="20"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>
<w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="220" w:after="100"/>
<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="4" w:color="DCE4E2"/></w:pBdr>
<w:outlineLvl w:val="0"/></w:pPr>
<w:rPr><w:b/><w:color w:val="176653"/><w:sz w:val="25"/></w:rPr></w:style>
</w:styles>"""
    content_types = """<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml"
 ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml"
 ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""
    relns = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
    base = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr(
            "_rels/.rels",
            f'<Relationships {relns}><Relationship Id="rId1" '
            f'Type="{base}officeDocument" Target="word/document.xml"/></Relationships>',
        )
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        archive.writestr(
            "word/_rels/document.xml.rels",
            f"<Relationships {relns}>"
            f'<Relationship Id="rId1" Type="{base}styles" Target="styles.xml"/>'
            "</Relationships>",
        )
    return output.getvalue()


def pdf(version):
    from playwright.sync_api import Error, sync_playwright

    tags = {
        "Title": ('<h1 class="resume-name">', "</h1>"),
        "Contact": ('<p class="resume-contact">', "</p>"),
        "Heading1": ('<h2 class="resume-section">', "</h2>"),
        "Normal": ('<p class="resume-line">', "</p>"),
    }
    body = "".join(f"{tags[s][0]}{escape(t)}{tags[s][1]}" for s, t in paragraphs(version))
    html = (
        """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>
@page {size:A4;margin:18mm 19mm}
body {
  font:10.5pt/1.55 "Noto Sans CJK SC","WenQuanYi Zen Hei","Microsoft YaHei",sans-serif;
  color:#202d32;margin:0
}
.resume-name {font-size:19pt;font-weight:700;color:#173c2f;margin:0 0 4pt;letter-spacing:-0.02em}
.resume-contact {font-size:9.5pt;color:#5c6d63;margin:0 0 4pt}
.resume-contact + .resume-section,.resume-name + .resume-section {
  margin-top:14pt;border-top:1pt solid #cbd5cf;padding-top:10pt
}
.resume-section {
  font-size:12pt;font-weight:700;color:#176653;margin:14pt 0 6pt;
  padding-bottom:3pt;border-bottom:0.75pt solid #dce4e2;break-after:avoid
}
.resume-line {margin:0 0 5pt;white-space:pre-wrap;overflow-wrap:anywhere;orphans:2;widows:2}
</style><body>"""
        + body
        + "</body></html>"
    )
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=["--disable-dev-shm-usage"])
            try:
                page = browser.new_page(java_script_enabled=False)
                page.route("**/*", lambda route: route.abort())
                page.set_content(html, timeout=15000)
                return page.pdf(format="A4", prefer_css_page_size=True)
            finally:
                browser.close()
    except Error:
        raise DomainError(
            "EXPORT_UNAVAILABLE", "PDF 渲染暂不可用，请重试或导出 Word", 503
        ) from None
