import html
from typing import Any

from messages import msg


CAPTION_MAX = 1024
TEXT_MAX = 4096
FRAGMENT_RESERVE = 200


def _pick_uploader(info_dict: dict[str, Any]) -> str:
    uid = str(info_dict.get('uploader_id') or '').strip()
    if uid.startswith('@'):
        return uid
    return str(
        info_dict.get('uploader')
        or info_dict.get('channel')
        or uid
        or ''
    ).strip()


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
    """Retorna (short, full).

    - short: caption curto (≤ CAPTION_MAX) pra colar em mídia do Telegram. Trunca
      o corpo da descrição com '...' preservando header e link.
    - full: texto completo, sem truncar nada. Pode passar de TEXT_MAX — quem for
      enviar deve quebrar via chunk_html_text(full, TEXT_MAX).
    """
    raw_uploader = _pick_uploader(info_dict)
    raw_title = str(info_dict.get('alt_title') or info_dict.get('title') or '').strip()
    raw_desc = str(
        info_dict.get('description', '')
        or info_dict.get('comment', '')
        or info_dict.get('caption', '')
        or ''
    ).strip()

    if _looks_like_shorts(info_dict, url):
        if not raw_desc:
            raw_desc = raw_title
        raw_title = ''
    elif _title_is_redundant(raw_title, raw_desc):
        raw_title = ''

    # Sem autor (post do Reddit), o título sobe pro cabeçalho — e continua inteiro.
    title_in_header = not raw_uploader and bool(raw_title)
    if title_in_header:
        raw_uploader, raw_title = raw_title, ''

    link_label = msg("caption.link_original_label")
    title_prefix = msg("caption.title_prefix")
    link_prefix = msg("caption.link_prefix")
    link_html = f"{link_prefix}<a href='{html.escape(url, quote=True)}'>{link_label}</a>"

    def _assemble(uploader_text: str, title_text: str, desc_text: str) -> str:
        up_esc = html.escape(uploader_text)
        ttl_esc = html.escape(title_text)
        ds_esc = html.escape(desc_text)

        header_lines = []
        if up_esc:
            header_lines.append(f"{title_prefix}<b>{up_esc}</b>")
        if ttl_esc:
            header_lines.append(f"<b>{ttl_esc}</b>")

        parts = []
        if header_lines:
            parts.append("\n".join(header_lines) + "\n\n")
        if ds_esc:
            parts.append(f"{ds_esc}\n\n")
        parts.append(link_html)
        return "".join(parts)

    has_content = bool(raw_uploader or raw_title or raw_desc)

    short = ""
    if has_content:
        caption_budget = max(200, CAPTION_MAX - FRAGMENT_RESERVE - len(url))

        def _cut(text: str, limit: int) -> str:
            return text if len(text) <= limit else text[:max(0, limit)].rstrip() + "..."

        up_t = _cut(raw_uploader, caption_budget if title_in_header else min(80, caption_budget // 5))
        # Título vai inteiro (no Reddit chega a 300 chars): quem cede espaço é a
        # descrição, que sai completa no texto à parte quando não cabe aqui.
        ttl_t = _cut(raw_title, caption_budget - len(up_t))
        ds_b = caption_budget - len(up_t) - len(ttl_t)
        ds_t = _cut(raw_desc, ds_b) if ds_b > 3 else ""

        short = _assemble(up_t, ttl_t, ds_t)
        if len(short) > CAPTION_MAX:
            short = short[:CAPTION_MAX]

    full = _assemble(raw_uploader, raw_title, raw_desc)

    return short, full
