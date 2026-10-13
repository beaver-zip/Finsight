"""별표의 괘선 표(고정폭 텍스트) → 마크다운 표.

법령 API의 `별표내용`은 표를 괘선 문자(┌─┬┐ │ ┃ ┠ …)로 그린 고정폭 텍스트다. 셀 글이 여러 줄로 잘려 있어
그대로 검색·답변에 넘기면 행·열 관계가 사라진다. 가로 괘선 사이를 한 행으로 보고, 세로 괘선 위치로 칸을 나눠
칸마다 줄을 이어 붙인다.

- 줄 잇기: 칸 끝 여백이 화면 폭 1칸 이하면(한글은 두 칸) 글자가 칸을 꽉 채워 단어 중간에서 줄이 바뀐 것으로 보고
  띄어쓰기 없이 잇는다. 여백이 더 있으면 띄어 쓴다. 다음 줄이 항목 기호(ㅇ, ⑴, 가., 1., -, ·)로 시작하거나
  첫 줄과 같은 머리말("법 제…")로 시작하면 새 항목이라 띄어 쓴다.
  레이아웃만으로 모자란 곳(원문이 칸을 다 채우지 않고 단어 중간에서 끊은 줄)은 같은 법령 본문의 낱말 목록(Vocab)을
  본다: 앞줄 끝 조각과 다음 줄 첫 조각을 붙인 말("위"+"반한")이 본문 낱말로(또는 낱말 앞부분으로) 쓰이면 붙인다.
- 칸 위치: 구분선(│ ┃ ┼ ├ …)의 화면 위치로 칸을 정한다. 행마다 구분선 수가 달라도(병합 칸) 같은 칸에 들어간다.
- 병합 칸: 일부 칸에만 가로선이 있는 줄("│      ├──┼──")에서 행을 끝내고, 아래로 이어지는 칸은 다음 행에서 빈칸이 된다.
- 첫 행을 머리 행으로 쓴다. 표가 아닌 글(제목·일반기준 등)은 그대로 둔다.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

VBAR = "│┃║"          # ASCII | 는 넣지 않는다(마크다운 표를 다시 읽지 않게)
_RULE_CHARS = set("─━═┌┐└┘├┤┬┴┼┏┓┗┛┣┫┳┻╋┠┨┯┷┿╂┝┥┰┸┞┟┡┢┦┧┩┪┭┮┱┲┵┶┹┺┽┾╀╁╃╄╅╆╇╈╉╊╒╓╔╕╖╗╘╙╚╛╜╝╞╟╠╡╢╣╤╥╦╧╨╩╪╫╬ ")
_ITEM = re.compile(r"^(ㅇ|○|●|◦|▪|■|□|※|-|·|ㆍ|•|[⑴-⒇]|[①-⑳]|\(\d+\)|\d+\.\s|[가-하]\.\s|\([가-하]\))")


_SEP = set(VBAR) | set("├┤┼┠┨┿╂┝┥╋┣┫┬┴┯┷┰┸┳┻╤╧╦╩╪╫╬")   # 칸을 가르는 문자(세로선과 교차점)


def _is_rule(line: str) -> bool:
    s = line.strip()
    return bool(s) and all(ch in _RULE_CHARS for ch in s)


def _is_row(line: str) -> bool:
    """칸 구분선이 있는 줄(왼쪽 테두리가 없는 표도 있다)."""
    return any(ch in _SEP for ch in line)


def _segments(line: str) -> list[tuple[int, str]]:
    """줄을 칸 구분 문자로 나눈 (조각 시작 화면 위치, 조각) 목록과 구분 문자 위치."""
    out, pos, start, buf = [], 0, 0, ""
    for ch in line:
        if ch in _SEP:
            out.append((start, buf))
            buf = ""
            pos += _width(ch)
            start = pos
        else:
            buf += ch
            pos += _width(ch)
    out.append((start, buf))
    return out


def _bounds(line: str) -> list[int]:
    pos, out = 0, []
    for ch in line:
        if ch in _SEP:
            out.append(pos)
        pos += _width(ch)
    return out


_TOK = re.compile(r"[가-힣0-9]+")
_ENDS = {"다", "음", "함", "임", "됨"}          # 문장 끝 한 글자가 다음 줄로 넘어간 경우


@dataclass
class Vocab:
    """법령 본문 낱말 빈도와 낱말 안 글자열(2~6자) 빈도."""
    words: Counter = field(default_factory=Counter)
    subs: Counter = field(default_factory=Counter)

    @classmethod
    def build(cls, texts: list[str]) -> "Vocab":
        v = cls()
        for t in texts:
            for w in _TOK.findall(t):
                v.words[w] += 1
                for k in range(2, 7):
                    for i in range(0, len(w) - k + 1):
                        v.subs[w[i:i + k]] += 1
        return v

    def glued(self, a: str, b: str) -> bool:
        """a(앞줄 끝 조각)와 b(다음 줄 첫 조각)가 원래 한 낱말인가: 이음매 앞뒤 세 글자씩을 붙인 글자열이
        본문 낱말 안에 나온다."""
        if b in _ENDS:
            return True
        return self.subs.get(a[-3:] + b[:3], 0) > 0

    def known(self, w: str) -> bool:
        return self.words.get(w, 0) > 0


def _width(s: str) -> int:
    """고정폭 화면 폭. 한글·전각·모호 폭 문자(·, ○ 등)는 두 칸."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WFA" else 1 for ch in s)


def _join(prev: str, seg: str, nxt: str, first: str, vocab: Vocab | None) -> str:
    """prev(지금까지 이은 글) 뒤에 다음 줄 조각 nxt를 잇는다. seg는 앞줄 원래 조각(여백 포함), first는 칸의 첫 줄."""
    if not prev:
        return nxt
    if not nxt:
        return prev
    lead = first.split(" ", 1)[0]
    if _ITEM.match(nxt) or (" " in first[:4] and nxt.startswith(lead + " ")):    # 새 항목("ㅇ…", 줄마다 "법 제…")
        return f"{prev} {nxt}"
    full = _width(seg.rstrip()) >= _width(seg) - 1        # 글자가 칸을 꽉 채웠다 = 단어 중간에서 줄이 바뀌었을 수 있다
    a, b = re.search(r"[가-힣0-9]*$", prev).group(), re.match(r"[가-힣0-9]*", nxt).group()
    if vocab is None or not a or not b:
        return prev + nxt if full else f"{prev} {nxt}"
    if vocab.glued(a, b):
        return prev + nxt
    if full and not (vocab.known(a) and vocab.known(b)):   # 꽉 찼어도 앞뒤가 각각 본문 낱말이면 띄어 쓴다
        return prev + nxt
    return f"{prev} {nxt}"


def _table(lines: list[str], vocab: Vocab | None) -> list[list[str]]:
    """괘선 표 줄 → 행 목록. 칸은 구분선의 화면 위치로 정한다(행마다 구분선 수가 달라도 같은 칸에 들어가게).
    일부 칸에만 가로선이 있는 줄(병합 칸)에서 행을 끝내고, 아래로 이어지는 칸은 다음 행에서 빈칸이 된다."""
    raw = sorted({b for ln in lines for b in _bounds(ln)})
    bounds: list[int] = []
    for b in raw:                                   # 폭 계산이 한 칸 어긋난 구분선은 하나로 본다
        if not bounds or b - bounds[-1] > 1:
            bounds.append(b)

    def col_of(pos: int) -> int:
        return sum(1 for b in bounds if b < pos)     # pos 앞에 있는 구분선 수 = 칸 번호

    n = len(bounds) + 1
    rows: list[list[str]] = []
    text, last, first = [""] * n, [""] * n, [""] * n

    def flush() -> None:
        nonlocal text, last, first
        if any(t for t in text):
            rows.append([re.sub(r"\s+", " ", t).strip().replace("|", "/") for t in text])
        text, last, first = [""] * n, [""] * n, [""] * n

    for ln in lines:
        if _is_rule(ln):
            flush()
            continue
        closed = False
        for start, seg in _segments(ln):
            if not seg.strip():
                continue
            if all(ch in _RULE_CHARS for ch in seg.strip()):
                closed = True                          # 이 칸은 여기서 행이 끝난다
                continue
            c = col_of(start)
            text[c] = _join(text[c], last[c], seg.strip(), first[c], vocab)
            last[c], first[c] = seg, first[c] or seg.strip()
        if closed:
            flush()
    flush()
    keep = [j for j in range(n) if any(r[j] for r in rows)]
    return [[r[j] for j in keep] for r in rows]


def to_markdown_table(rows: list[list[str]]) -> str:
    n = max(len(r) for r in rows)
    rows = [r + [""] * (n - len(r)) for r in rows]
    head, body = rows[0], rows[1:]
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * n]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


def convert(text: str, vocab: Vocab | None = None) -> str:
    """별표 본문의 괘선 표를 마크다운 표로 바꾼다. 괘선 표가 없으면 그대로 돌려준다(여러 번 불러도 같다).
    vocab: 같은 법령 본문 낱말(줄 잇기 판단). 없으면 칸 여백만 본다."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if _is_rule(lines[i]) or _is_row(lines[i]):
            j = i
            while j < len(lines) and (_is_rule(lines[j]) or _is_row(lines[j])):
                j += 1
            rows = _table(lines[i:j], vocab)
            if rows and max(len(r) for r in rows) >= 2:
                out.append(to_markdown_table(rows))
            else:                                   # 칸이 하나뿐인 상자(글 상자)는 글로 푼다
                out.extend(c for r in rows for c in r if c)
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def table_blocks(text: str) -> list[tuple[str, list[str]]]:
    """마크다운으로 바꾼 별표 본문을 (머리 두 줄, 행 목록) 또는 ("", 글 줄 목록) 덩어리로 나눈다(청크 자르기용)."""
    out: list[tuple[str, list[str]]] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        if lines[i].startswith("| ") and i + 1 < len(lines) and lines[i + 1].startswith("|---"):
            j = i + 2
            while j < len(lines) and lines[j].startswith("| "):
                j += 1
            out.append((lines[i] + "\n" + lines[i + 1], lines[i + 2:j]))
            i = j
        else:
            if out and out[-1][0] == "":
                out[-1][1].append(lines[i])
            else:
                out.append(("", [lines[i]]))
            i += 1
    return out


def split_annex(text: str, limit: int) -> list[str]:
    """별표 본문을 limit자 이하 조각으로 자른다. 표는 행 단위로 자르고 조각마다 표 머리 행을 붙인다."""
    out: list[str] = []
    buf = ""

    def push(line: str, head: str = "") -> None:
        nonlocal buf
        if buf and len(buf) + len(line) + 1 > limit:
            out.append(buf)
            buf = head
        buf = f"{buf}\n{line}" if buf else line

    for head, lines in table_blocks(text):
        if head:
            push(head)
            for row in lines:
                push(row, head)
        else:
            for line in lines:
                push(line)
    if buf:
        out.append(buf)
    return out
