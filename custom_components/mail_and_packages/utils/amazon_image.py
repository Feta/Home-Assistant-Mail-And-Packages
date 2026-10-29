"""Amazon image download and processing utilities."""

from __future__ import annotations

import email
import logging
import re
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from aioimaplib import IMAP4_SSL

from custom_components.mail_and_packages.const import (
    AMAZON_IMG_LIST,
    AMAZON_IMG_PATTERN,
)
from custom_components.mail_and_packages.utils.cache import EmailCache
from custom_components.mail_and_packages.utils.image import io_save_file
from custom_components.mail_and_packages.utils.imap import email_fetch

_LOGGER = logging.getLogger(__name__)

_MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB


def _validated_amazon_image_url(url: str) -> str | None:
    """Return a safe Amazon S3 image URL, or None for untrusted destinations.

    The hostname must come from the parsed URL, not a regex capture from
    email HTML. An allowlisted host before an @ is userinfo, not the destination.
    Reject control characters and backslashes as well.
    """
    if not isinstance(url, str):
        return None
    normalized = unescape(url)
    if "\\" in normalized or any(
        ord(char) < 32 or ord(char) == 127 for char in normalized
    ):
        return None
    try:
        parsed = urlsplit(normalized)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in AMAZON_IMG_LIST
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
        ):
            return None
    except ValueError:
        return None
    return normalized


async def download_amazon_img(
    img_url: str,
    img_path: str,
    img_name: str,
    hass: Any,
) -> None:
    """Download an approved Amazon delivery image without following redirects."""
    safe_url = _validated_amazon_image_url(img_url)
    if safe_url is None:
        _LOGGER.warning("Skipping Amazon delivery image with unapproved destination")
        return
    img_path_obj = Path(img_path) / "amazon"
    filepath = img_path_obj / img_name
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.get(safe_url, allow_redirects=False) as resp:
                if resp.status != 200:
                    return
                content_type = resp.headers.get("content-type", "")
                if "image" not in content_type:
                    return
                content_length = int(resp.headers.get("content-length", 0))
                if content_length > _MAX_IMAGE_SIZE:
                    _LOGGER.warning(
                        "Amazon image too large to download (%d bytes), skipping",
                        content_length,
                    )
                    return
                data = await resp.read()
                if len(data) > _MAX_IMAGE_SIZE:
                    _LOGGER.warning(
                        "Amazon image exceeds size limit after download, discarding"
                    )
                    return
                await hass.async_add_executor_job(io_save_file, filepath, data)
        except aiohttp.ClientError as err:
            _LOGGER.error("Problem downloading file: %s", err)


def _extract_amazon_urls_from_msg(
    msg: email.message.Message,
    pattern: re.Pattern[str],
) -> list[str]:
    """Extract Amazon delivery image URLs from a message."""
    urls = []
    for part in msg.walk():
        if part.get_content_type() != "text/html":
            continue
        part_payload = part.get_payload(decode=True)
        if not isinstance(part_payload, (bytes, bytearray)):
            continue
        part_content = part_payload.decode("utf-8", "ignore")
        for url in pattern.findall(part_content):
            if url[1] in AMAZON_IMG_LIST:
                safe_url = _validated_amazon_image_url(url[0] + url[1] + url[2])
                if safe_url and safe_url not in urls:
                    urls.append(safe_url)
    return urls


async def get_amazon_image_urls(
    sdata: Any,
    account: IMAP4_SSL,
    cache: EmailCache | None = None,
) -> list[str]:
    """Find all Amazon delivery image URLs."""
    mail_list = sdata.split()
    pattern = re.compile(rf"{AMAZON_IMG_PATTERN}")
    urls: list[str] = []
    for i in mail_list:
        if cache:
            data = (await cache.fetch(i, "(RFC822)"))[1]
        else:
            data = (await email_fetch(account, i, "(RFC822)"))[1]
        for response_part in data:
            if isinstance(response_part, (bytes, bytearray)):
                msg = email.message_from_bytes(response_part)
                for full_url in _extract_amazon_urls_from_msg(msg, pattern):
                    if full_url not in urls:
                        urls.append(full_url)
    return urls
