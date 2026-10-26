"""One logical document feeds private DOCX and controlled Chromium PDF exports."""

from html import escape
from io import BytesIO
from xml.sax.saxutils import escape as xml_escape
from zipfile import ZIP_DEFLATED, ZipFile

from careerpilot.domain import DomainError
from careerpilot.resumes import KINDS


def paragraphs(version):
    result = [("Title", "个人简历")]
    for kind, label in KINDS.items():
        facts = [f for f in version["facts"] if f["kind"] == kind]
        if not facts:
            continue
        if kind not in {"name", "contact"}:
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
<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134"
w:bottom="1134" w:left="1134" w:header="567" w:footer="567"/>
</w:sectPr></w:body></w:document>"""
    styles = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles {ns}><w:docDefaults><w:rPrDefault><w:rPr>
<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/>
<w:sz w:val="22"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>
<w:spacing w:after="120" w:line="300" w:lineRule="auto"/>
</w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>
<w:basedOn w:val="Normal"/><w:pPr><w:keepNext/></w:pPr>
<w:rPr><w:b/><w:sz w:val="36"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>
<w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/>
<w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>
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

    tags = {"Title": "h1", "Heading1": "h2", "Normal": "p"}
    body = "".join(f"<{tags[s]}>{escape(t)}</{tags[s]}>" for s, t in paragraphs(version))
    html = (
        """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>
@page {size:A4;margin:20mm}body {font:11pt/1.5 "WenQuanYi Zen Hei",sans-serif;color:#111}
h1 {font-size:18pt;margin:0 0 12pt}h2 {font-size:13pt;margin:14pt 0 6pt;break-after:avoid}
p {margin:0 0 6pt;white-space:pre-wrap;overflow-wrap:anywhere;orphans:2;widows:2}
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
