"""resources.py: reading a PDF or a web page into text, and refusing links
that point at private or local addresses (server-side request forgery)."""

from __future__ import annotations

import httpx
import pytest

from versa import resources
from versa.resources import ResourceError, check_url_is_safe, extract_pdf, fetch_link


def make_pdf(lines: list[str]) -> bytes:
    """A minimal valid one-page PDF with the given text lines."""
    content = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(
        f"({line}) Tj T*" for line in lines
    ) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def test_pdf_text_is_extracted():
    res = extract_pdf(make_pdf(["Photosynthesis basics", "Light reactions make ATP"]), "bio-notes.pdf")
    assert res.kind == "pdf"
    assert "Photosynthesis basics" in res.text
    assert "Light reactions make ATP" in res.text
    assert res.title == "bio-notes"
    assert res.filename == "bio-notes.pdf"


def test_non_pdf_bytes_are_refused():
    with pytest.raises(ResourceError):
        extract_pdf(b"hello, not a pdf", "x.pdf")


def test_oversized_pdf_is_refused(monkeypatch):
    monkeypatch.setattr(resources, "MAX_PDF_BYTES", 100)
    with pytest.raises(ResourceError, match="20 MB"):
        extract_pdf(make_pdf(["x" * 200]), "big.pdf")


async def _public(host: str) -> list[str]:
    return {"internal.test": ["10.0.0.5"], "sneaky.test": ["93.184.216.34", "127.0.0.1"]}.get(
        host, ["93.184.216.34"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://localhost.test@127.0.0.1/",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://10.1.2.3/",
    "http://192.168.0.1/",
    "http://0.0.0.0/",
    "http://internal.test/",
    "http://sneaky.test/",
])
async def test_private_and_local_addresses_are_refused(url):
    with pytest.raises(ResourceError, match="private or local"):
        await check_url_is_safe(url, _public)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd", "javascript:alert(1)"])
async def test_non_http_schemes_are_refused(url):
    with pytest.raises(ResourceError, match="http"):
        await check_url_is_safe(url, _public)


_PAGE = """<html><head><title>Photosynthesis - Wiki</title><style>.x{}</style></head>
<body><nav>menu stuff</nav><h1>Photosynthesis</h1><p>Plants turn light into sugar.</p>
<h2>Light reactions</h2><p>Happen in the thylakoids.</p><script>var a=1;</script>
<h2>Calvin cycle</h2><p>Fixes carbon.</p></body></html>"""


@pytest.mark.asyncio
async def test_a_web_page_is_read_with_its_headings():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_PAGE, headers={"content-type": "text/html; charset=utf-8"})

    res = await fetch_link(
        "https://example.test/wiki/Photosynthesis", resolver=_public,
        transport=httpx.MockTransport(handler),
    )
    assert res.kind == "link"
    assert res.title == "Photosynthesis - Wiki"
    assert res.headings == ["Photosynthesis", "Light reactions", "Calvin cycle"]
    assert "Plants turn light into sugar." in res.text
    assert "var a=1" not in res.text and "menu stuff" not in res.text


@pytest.mark.asyncio
async def test_a_redirect_to_a_private_address_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://internal.test/secret"})

    with pytest.raises(ResourceError, match="private or local"):
        await fetch_link("https://example.test/", resolver=_public, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_a_pdf_link_is_read_as_a_pdf():
    pdf = make_pdf(["Chapter one", "Thermodynamics"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})

    res = await fetch_link("https://example.test/notes.pdf", resolver=_public,
                           transport=httpx.MockTransport(handler))
    assert res.kind == "link" and res.url == "https://example.test/notes.pdf"
    assert "Thermodynamics" in res.text


@pytest.mark.asyncio
async def test_an_oversized_page_is_refused(monkeypatch):
    monkeypatch.setattr(resources, "MAX_LINK_BYTES", 1000)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<p>" + "x" * 5000 + "</p>", headers={"content-type": "text/html"})

    with pytest.raises(ResourceError, match="larger than"):
        await fetch_link("https://example.test/", resolver=_public, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_a_binary_link_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG....", headers={"content-type": "image/png"})

    with pytest.raises(ResourceError, match="not a web page"):
        await fetch_link("https://example.test/a.png", resolver=_public, transport=httpx.MockTransport(handler))
