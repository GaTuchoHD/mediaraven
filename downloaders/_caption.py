import html
import re
from typing import Any

from messages import msg

CAPTION_MAX = 1024
TEXT_MAX = 4096


def _normalize_caption_text(text: str) -> str:
    """Normalize caption transport artifacts without flattening paragraphs."""
    if not text:
        return ""
    normalized = html.unescape(str(text))
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u2028", "\n").replace("\u2029", "\n")
    # A few Instagram responses serialize paragraph breaks as literal escapes.
    normalized = normalized.replace("\\r\\n", "\n").replace("\\n", "\n")
    normalized = normalized.replace("\u00a0", " ")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _pick_uploader(info_dict: dict[str, Any]) -> str:
    uid = str(info_dict.get('uploader_id') or '').strip()
    if uid.startswith('@'):
        return uid
    uploader = str(
        info_dict.get('uploader')
        or info_dict.get('channel')
        or uid
        or ''
    ).strip()
    return uploader


def _clean_text_no_tags_mentions(text: str) -> str:
    if not text:
        return ""
    text = _normalize_caption_text(text)

    lines = text.splitlines()
    cleaned_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append("")
            continue
        words = stripped.split()
        if words:
            # If line is >60% @mentions and #hashtags (like tagged accounts list), drop line
            tags_count = sum(1 for w in words if w.startswith('#') or w.startswith('@'))
            if tags_count / len(words) > 0.6 and len(words) > 3:
                continue

        # Remove individual #hashtags
        line_clean = re.sub(r'#[a-zA-Z0-9_\.-]+', '', line)
        # Remove individual @mentions
        line_clean = re.sub(r'@[a-zA-Z0-9_\.-]+', '', line_clean)

        # Remove trailing colon if line now ends in a colon after removing @mention
        line_clean = re.sub(r':\s*$', '', line_clean)

        # Clean multiple spaces
        line_clean = re.sub(r'[ \t]+', ' ', line_clean).strip()
        cleaned_lines.append(line_clean)

    res = "\n".join(cleaned_lines)
    # Clean excessive blank lines
    res = re.sub(r'\n{3,}', '\n\n', res).strip()
    return res


def _title_is_redundant(title: str, desc: str) -> bool:
    if not title or not desc:
        return False
    t = title.strip().lower()
    d = desc.strip().lower()
    return t == d or d.startswith(t)


def _looks_like_shorts(info_dict: dict[str, Any], url: str) -> bool:
    for key in ('original_url', 'webpage_url'):
        v = info_dict.get(key)
        if isinstance(v, str) and '/shorts/' in v:
            return True
    return '/shorts/' in url


def _build_caption(info_dict: dict[str, Any], url: str) -> tuple[str, str]:
    raw_uploader = _pick_uploader(info_dict)
    raw_title = str(info_dict.get('alt_title') or info_dict.get('title') or '').strip()
    raw_desc = str(
        info_dict.get('description', '')
        or info_dict.get('comment', '')
        or info_dict.get('caption', '')
        or ''
    ).strip()

    # Instagram's embed path historically passes the account as `title`.
    # Preserve a leading @handle before the generic cleaner removes mentions.
    if not raw_uploader and raw_title.startswith('@'):
        raw_uploader, raw_title = raw_title, ''

    # Clean hashtags, @mentions, and tagged lists from title & description
    raw_title = _clean_text_no_tags_mentions(raw_title)
    raw_desc = _clean_text_no_tags_mentions(raw_desc)

    if _looks_like_shorts(info_dict, url):
        if not raw_desc:
            raw_desc = raw_title
        raw_title = ''
    elif _title_is_redundant(raw_title, raw_desc):
        raw_title = ''

    if not raw_uploader and raw_title:
        raw_uploader, raw_title = raw_title, ''

    link_label = msg("caption.link_original_label")
    link_html = f"🔗 <a href='{html.escape(url, quote=True)}'>{link_label}</a>"

    up_esc = html.escape(raw_uploader) if raw_uploader else ""
    ttl_esc = html.escape(raw_title) if raw_title else ""
    ds_esc = html.escape(raw_desc) if raw_desc else ""

    header_lines = []
    if up_esc:
        header_lines.append(f"📌 <b>{up_esc}</b>")
    if ttl_esc:
        header_lines.append(f"<b>{ttl_esc}</b>")

    header_str = "\n".join(header_lines)

    # Assemble FULL caption
    full_parts = []
    if header_str:
        full_parts.append(header_str + "\n\n")
    if ds_esc:
        full_parts.append(ds_esc + "\n\n")
    full_parts.append(link_html)
    full = "".join(full_parts)

    # Assemble SHORT caption (max 1024 for Telegram Media)
    fixed_len = len(link_html) + (len(header_str) + 4 if header_str else 0) + 4
    budget_for_desc = max(200, CAPTION_MAX - fixed_len)

    short = ""
    if raw_uploader or raw_title or raw_desc:
        short_ds_esc = ds_esc
        if len(ds_esc) > budget_for_desc:
            truncated = ds_esc[:budget_for_desc - 5]
            last_space = truncated.rfind(' ')
            if last_space > budget_for_desc // 2:
                truncated = truncated[:last_space]
            short_ds_esc = truncated.rstrip() + "..."

        short_parts = []
        if header_str:
            short_parts.append(header_str + "\n\n")
        if short_ds_esc:
            short_parts.append(short_ds_esc + "\n\n")
        short_parts.append(link_html)
        short = "".join(short_parts)

        if len(short) > CAPTION_MAX:
            short = short[:CAPTION_MAX]

    return short, full
