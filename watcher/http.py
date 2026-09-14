"""极小 HTTP 客户端：只用标准库，整个工程零第三方依赖。

设计取舍：GitHub Actions 上 pip install 越多越容易失败，
所以这里直接用 urllib 而不是 requests，config 用 JSON 而不是 YAML。
"""

import gzip
import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

log = logging.getLogger("codewatch.http")

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_VERIFIED_CTX = ssl.create_default_context()
_UNVERIFIED_CTX = ssl.create_default_context()
_UNVERIFIED_CTX.check_hostname = False
_UNVERIFIED_CTX.verify_mode = ssl.CERT_NONE


class HttpError(RuntimeError):
    """网络层失败。数据源调用方应当捕获并降级，而不是让整个进程挂掉。"""


def get_text(url, headers=None, timeout=20, retries=3, backoff=1.6):
    """GET 并返回解码后的文本。失败会重试，最终抛 HttpError。"""
    merged = {"User-Agent": DEFAULT_UA, "Accept-Encoding": "gzip, deflate"}
    if headers:
        merged.update(headers)

    last_err = None
    for attempt in range(retries):
        for ctx in (_VERIFIED_CTX, _UNVERIFIED_CTX):
            try:
                return _once(url, merged, timeout, ctx)
            except ssl.SSLError as exc:
                last_err = exc
                continue
            except urllib.error.HTTPError as exc:
                last_err = exc
                break
            except Exception as exc:
                last_err = exc
                break
        if attempt < retries - 1:
            time.sleep(backoff ** attempt)
    raise HttpError(f"GET {url} 失败：{type(last_err).__name__}: {last_err}")


def get_json(url, headers=None, timeout=20, retries=3):
    text = get_text(url, headers=headers, timeout=timeout, retries=retries)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise HttpError(f"GET {url} 返回的不是合法 JSON：{text[:200]!r}") from exc


def post_json(url, payload, headers=None, timeout=20, retries=2):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    merged = {
        "User-Agent": DEFAULT_UA,
        "Content-Type": "application/json; charset=utf-8",
        "Accept-Encoding": "gzip, deflate",
    }
    if headers:
        merged.update(headers)

    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=merged, method="POST")
            with urllib.request.urlopen(req, timeout=timeout, context=_VERIFIED_CTX) as resp:
                raw = resp.read()
                return json.loads(_decode(raw, resp.headers))
        except Exception as exc:
            last_err = exc
            if attempt < retries - 1:
                time.sleep(1.5 ** attempt)
    raise HttpError(f"POST {url} 失败：{type(last_err).__name__}: {last_err}")


def _once(url, headers, timeout, ctx):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return _decode(resp.read(), resp.headers)


def _decode(raw, headers):
    encoding = (headers.get("Content-Encoding") or "").lower()
    if "gzip" in encoding:
        raw = gzip.decompress(raw)
    elif "deflate" in encoding:
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw.decode("utf-8", "replace")


def quote(text):
    return urllib.parse.quote(text, safe="")
