"""Reading a learning resource into text for "Learn a topic" (topics.py): a
PDF the student uploads, or a web page they link.

Both paths return an `ExtractedResource`: a title, the plain text, and the
headings found (PDF outline entries, HTML h1-h3), so the course outline can be
built from the document's own structure rather than guessed.

Fetching a link is the one place the server makes an outbound request on a
student's behalf, so it is guarded against server-side request forgery:
http/https only, the host is resolved and refused if ANY address it resolves
to is private, loopback, link-local, multicast, reserved or unspecified, every
redirect hop is checked the same way (at most 3), the body is capped at 5 MB
and the whole fetch at 15 s. The check-then-connect gap (DNS rebinding) is
accepted for a local-only server; see IDEAS.md.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel

MAX_LINK_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 20 * 1024 * 1024
LINK_TIMEOUT_S = 15.0
MAX_REDIRECTS = 3
MAX_PDF_PAGES = 300
# What the outline prompt sees: the headings plus the start of the text.
OUTLINE_TEXT_CHARS = 60_000

Resolver = Callable[[str], Awaitable[list[str]]]


class ResourceError(Exception):
    """A resource that can't be read -- the message is safe to show."""


class ExtractedResource(BaseModel):
    kind: str  # 'pdf' | 'link'
    title: str
    text: str
    headings: list[str] = []
    url: str | None = None
    filename: str | None = None

    def outline_excerpt(self) -> str:
        return self.text[:OUTLINE_TEXT_CHARS]


def _clean(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------- PDF


def extract_pdf(data: bytes, filename: str | None = None) -> ExtractedResource:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    if len(data) > MAX_PDF_BYTES:
        raise ResourceError("that PDF is larger than 20 MB")
    if not data.startswith(b"%PDF"):
        raise ResourceError("that file is not a PDF")
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = reader.pages[:MAX_PDF_PAGES]
        text = "\n\n".join((p.extract_text() or "") for p in pages)
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise ResourceError(f"could not read that PDF ({exc})") from exc
    text = _clean(text)
    if not text:
        raise ResourceError("that PDF has no readable text (it may be scanned images)")
    headings: list[str] = []
    try:
        def walk(items) -> None:
            for item in items:
                if isinstance(item, list):
                    walk(item)
                elif getattr(item, "title", None):
                    headings.append(str(item.title).strip())
        walk(reader.outline)
    except Exception:  # noqa: BLE001 -- an unreadable outline just means no headings
        headings = []
    title = ""
    try:
        meta_title = reader.metadata.title if reader.metadata else None
        title = (meta_title or "").strip()
    except Exception:  # noqa: BLE001
        title = ""
    if not title:
        title = (filename or "").rsplit(".", 1)[0].strip() or text.split("\n", 1)[0][:80]
    return ExtractedResource(
        kind="pdf", title=title[:200], text=text, headings=headings[:200], filename=filename
    )


# ---------------------------------------------------------------- HTML


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside"}
    _BLOCK = {"p", "div", "li", "br", "tr", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.headings: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False
        self._heading: list[str] | None = None

    def handle_starttag(self, tag, attrs) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("h1", "h2", "h3"):
            self._heading = []
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2", "h3") and self._heading is not None:
            heading = " ".join("".join(self._heading).split())
            if heading:
                self.headings.append(heading)
            self._heading = None
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip_depth:
            return
        self.parts.append(data)
        if self._heading is not None:
            self._heading.append(data)


def extract_html(html: str, url: str | None = None) -> ExtractedResource:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = _clean("".join(parser.parts))
    if not text:
        raise ResourceError("that page has no readable text")
    title = " ".join(parser.title.split()) or (parser.headings[0] if parser.headings else "")
    if not title and url:
        title = urlsplit(url).netloc
    return ExtractedResource(
        kind="link", title=title[:200], text=text, headings=parser.headings[:200], url=url
    )


# ---------------------------------------------------------------- links


async def _default_resolver(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )


async def check_url_is_safe(url: str, resolver: Resolver = _default_resolver) -> None:
    """Raise ResourceError unless `url` is http(s) and every address its
    host resolves to is public."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ResourceError("only http and https links can be read")
    host = parts.hostname
    if not host:
        raise ResourceError("that link has no host")
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = await resolver(host)
        except (OSError, socket.gaierror) as exc:
            raise ResourceError(f"could not resolve {host}") from exc
    if not addresses:
        raise ResourceError(f"could not resolve {host}")
    if not all(_is_public(a) for a in addresses):
        raise ResourceError("that link points to a private or local address, which can't be read")


async def fetch_link(
    url: str,
    *,
    resolver: Resolver = _default_resolver,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ExtractedResource:
    url = url.strip()
    async with httpx.AsyncClient(
        transport=transport,
        follow_redirects=False,
        timeout=httpx.Timeout(LINK_TIMEOUT_S),
        # Wikimedia (and others) 403 clients whose User-Agent carries no contact URL.
        headers={"User-Agent": "Versa/0.1 (https://github.com/sjr27-maker/Versa-ver-2; learning resource reader)"},
    ) as client:
        try:
            return await asyncio.wait_for(
                _fetch(client, url, resolver), timeout=LINK_TIMEOUT_S
            )
        except TimeoutError as exc:
            raise ResourceError("that link took too long to load") from exc
        except httpx.HTTPError as exc:
            raise ResourceError(f"could not load that link ({type(exc).__name__})") from exc


async def _fetch(client: httpx.AsyncClient, url: str, resolver: Resolver) -> ExtractedResource:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        await check_url_is_safe(current, resolver)
        async with client.stream("GET", current) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ResourceError("that link redirects nowhere")
                current = urljoin(current, location)
                continue
            if response.status_code >= 400:
                raise ResourceError(f"that link answered {response.status_code}")
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_LINK_BYTES:
                    raise ResourceError("that page is larger than 5 MB")
            data = bytes(body)
            if content_type == "application/pdf" or data.startswith(b"%PDF"):
                resource = extract_pdf(data, filename=urlsplit(current).path.rsplit("/", 1)[-1])
                return resource.model_copy(update={"kind": "link", "url": current})
            if content_type and not (
                content_type.startswith("text/") or content_type in ("application/xhtml+xml",)
            ):
                raise ResourceError(f"that link is not a web page or PDF ({content_type})")
            encoding = response.encoding or "utf-8"
            html = data.decode(encoding, errors="replace")
            if content_type == "text/plain":
                text = _clean(html)
                if not text:
                    raise ResourceError("that page has no readable text")
                return ExtractedResource(
                    kind="link", title=urlsplit(current).path.rsplit("/", 1)[-1] or current,
                    text=text, url=current,
                )
            return extract_html(html, url=current)
    raise ResourceError("that link redirects too many times")
