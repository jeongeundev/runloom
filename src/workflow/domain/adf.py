"""ADF(Atlassian Document Format) ↔ 텍스트 — 작은 변환기 (ADR-0024 결정 5·14, ARCHITECTURE "Jira 소스 — phase 18").

- `adf_to_text`: 가져오기 전용. 이슈 본문을 요청·양식 칸(`form_sections`) 재료 텍스트로 — 문단·제목(`#`×수준)·목록·코드 블록·
  링크·줄바꿈·멘션만, 모르는 노드는 안의 글만. 깊이 제한을 넘는 부분은 버린다.
- `markdown_to_adf`: 후속 이슈 본문 전용 — 문단·`- ` 목록·``` 코드 블록·`[글](http(s)://…)` 와 맨 `http(s)://` URL 링크.
  다른 스킴은 글 그대로.

둘 다 잘못된 입력에 예외 대신 빈 텍스트/빈 문서를 돌려준다. 글은 표시·요청 재료일 뿐 명령·경로로 해석하지 않는다.
"""

import re
from collections.abc import Mapping

MAX_DEPTH = 32

_INLINE = ("text", "hardBreak", "mention", "emoji", "inlineCard", "date", "status")


def _children(node: Mapping) -> list[Mapping]:
    content = node.get("content")
    if not isinstance(content, list):
        return []
    return [child for child in content if isinstance(child, Mapping)]


def _attr(node: Mapping, name: str):
    attrs = node.get("attrs")
    return attrs.get(name) if isinstance(attrs, Mapping) else None


def _inline(node: Mapping) -> str:
    kind = node.get("type")
    if kind == "text":
        text = node.get("text")
        if not isinstance(text, str):
            return ""
        marks = node.get("marks")
        for mark in marks if isinstance(marks, list) else []:
            if isinstance(mark, Mapping) and mark.get("type") == "link":
                href = _attr(mark, "href")
                if isinstance(href, str) and href and href != text:
                    return f"{text} ({href})"
        return text
    if kind == "hardBreak":
        return "\n"
    if kind == "mention":
        name = _attr(node, "text")
        return name if isinstance(name, str) and name.startswith("@") else f"@{name}" if isinstance(name, str) else ""
    if kind == "emoji":
        value = _attr(node, "text") or _attr(node, "shortName")
        return value if isinstance(value, str) else ""
    if kind == "inlineCard":
        url = _attr(node, "url")
        return url if isinstance(url, str) else ""
    text = _attr(node, "text")
    return text if isinstance(text, str) else ""


def _indent(text: str, first: str) -> str:
    lines = text.split("\n")
    pad = " " * len(first)
    return "\n".join([first + lines[0]] + [pad + line if line else line for line in lines[1:]])


def _list(node: Mapping, depth: int, ordered: bool) -> str:
    start = _attr(node, "order") if ordered else None
    number = start if isinstance(start, int) and not isinstance(start, bool) and start >= 0 else 1
    items = []
    for item in _children(node):
        body = "\n".join(part for part in (_block(child, depth + 1) for child in _children(item)) if part)
        if ordered:
            items.append(_indent(body, f"{number}. "))
            number += 1
        else:
            items.append(_indent(body, "- "))
    return "\n".join(items)


def _block(node: Mapping, depth: int) -> str:
    if depth > MAX_DEPTH:
        return ""
    kind = node.get("type")
    children = _children(node)
    if kind in _INLINE:
        return _inline(node)
    if kind == "paragraph":
        return "".join(_inline(child) for child in children)
    if kind == "heading":
        level = _attr(node, "level")
        level = level if isinstance(level, int) and not isinstance(level, bool) and 1 <= level <= 6 else 1
        text = "".join(_inline(child) for child in children)
        return f"{'#' * level} {text}" if text else ""
    if kind == "bulletList":
        return _list(node, depth, ordered=False)
    if kind == "orderedList":
        return _list(node, depth, ordered=True)
    if kind == "codeBlock":
        language = _attr(node, "language")
        fence = "```" + (language if isinstance(language, str) and re.fullmatch(r"[\w+#.-]{1,30}", language) else "")
        return f"{fence}\n{''.join(_inline(child) for child in children)}\n```"
    # 모르는 노드(doc·panel·blockquote·table …): 안의 글만
    if children and all(child.get("type") in _INLINE for child in children):
        return "".join(_inline(child) for child in children)
    return "\n\n".join(part for part in (_block(child, depth + 1) for child in children) if part)


def adf_to_text(node: Mapping | None) -> str:
    if not isinstance(node, Mapping):
        return ""
    return _block(node, 0).strip()


# --- Markdown → ADF ---

_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)|(https?://[^\s<>\"]+)")
_TRAILING = ".,;:!?)]'"


def _text(text: str, href: str | None = None) -> dict:
    node: dict = {"type": "text", "text": text}
    if href is not None:
        node["marks"] = [{"type": "link", "attrs": {"href": href}}]
    return node


def _inline_nodes(line: str) -> list[dict]:
    nodes: list[dict] = []
    plain = ""
    pos = 0
    for match in _LINK.finditer(line):
        plain += line[pos:match.start()]
        if match.group(1) is not None:
            label, href, tail = match.group(1), match.group(2), ""
        else:
            href = match.group(3).rstrip(_TRAILING)
            label, tail = href, match.group(3)[len(href):]
        if not href.split("://", 1)[1]:
            plain += match.group(0)
            pos = match.end()
            continue
        if plain:
            nodes.append(_text(plain))
        nodes.append(_text(label, href))
        plain = tail
        pos = match.end()
    plain += line[pos:]
    if plain:
        nodes.append(_text(plain))
    return nodes


def _paragraph(lines: list[str]) -> dict:
    content: list[dict] = []
    for i, line in enumerate(lines):
        if i:
            content.append({"type": "hardBreak"})
        content.extend(_inline_nodes(line))
    return {"type": "paragraph", "content": content}


def markdown_to_adf(text: str) -> dict:
    blocks: list[dict] = []
    if not isinstance(text, str):
        return {"type": "doc", "version": 1, "content": blocks}
    lines = text.split("\n")
    i = 0
    paragraph: list[str] = []
    items: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(_paragraph(paragraph))
            paragraph.clear()
        if items:
            blocks.append({"type": "bulletList", "content": [
                {"type": "listItem", "content": [_paragraph([item])]} for item in items
            ]})
            items.clear()

    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            flush()
            language = line[3:].strip()
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1  # 닫는 울타리(없으면 끝까지)
            node: dict = {"type": "codeBlock", "content": [_text("\n".join(body))] if "".join(body) else []}
            if re.fullmatch(r"[\w+#.-]{1,30}", language):
                node["attrs"] = {"language": language}
            blocks.append(node)
            continue
        if not line.strip():
            flush()
        elif line.startswith("- "):
            if paragraph:
                flush()
            items.append(line[2:])
        else:
            if items:
                flush()
            paragraph.append(line)
        i += 1
    flush()
    return {"type": "doc", "version": 1, "content": blocks}
