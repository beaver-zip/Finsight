"""참조 해석기 정확도 측정용 표본: 코퍼스 조문 본문의 참조 표현('제N조', '별표 N') 100개와 해석기 초안.

사용: cd backends && PYTHONPATH=. python -m eval.build.citation_refs
결과: eval/datasets/citation_refs.v1.csv(엑셀에서 열리게 UTF-8 BOM). 사람이 '판정'에 O/X, 틀렸으면 '정답_대상'을 채운다.
표본은 표현이 나온 위치를 단위로 뽑아서, 해석기가 놓친 표현(재현율)과 잘못 푼 표현(정밀도)을 함께 잰다.
"""
from __future__ import annotations

import csv
import random
import re

from app.agents.legal.tools import short_name
from app.core.law.corpus import load_corpus
from app.domain.citations import find_refs
from app.settings import BACKEND_DIR

OUT = BACKEND_DIR / "eval" / "datasets" / "citation_refs.v1.csv"
OCC = re.compile(r"제\d+조(?:의\d+)?|별표\s*\d+(?:의\d+)?")
N, SEED = 100, 20261012


def main() -> None:
    corpus = load_corpus()
    rows = []
    for a in corpus.articles.values():
        law = a.key.split(":")[0]
        for p in a.paragraphs:
            refs = find_refs(law, p.text, from_para=p.no)          # 자기 조문 참조도 남긴다
            for m in OCC.finditer(p.text):
                hit = [r for r in refs if r.span[0] <= m.start() < r.span[1]]
                if hit:
                    r = hit[-1]
                    target = r.key + (f" 제{r.para}항" if r.para else "") + (f" 제{r.item}호" if r.item else "")
                    note = "같은 조문(엣지 아님)" if r.key == a.key else ("코퍼스 밖" if not r.in_corpus else "")
                    if len(hit) > 1:
                        note = (note + " " if note else "") + "범위: " + ", ".join(h.key for h in hit)
                else:
                    target, note = "(해석 안 됨)", ""
                ctx = p.text[max(0, m.start() - 60):m.end() + 30].replace("\n", " ")
                rows.append({"문서": short_name(a.key).split(" ")[0], "조문": short_name(a.key), "항": p.no or "",
                             "표현": m.group(0), "앞뒤 문맥": ctx, "초안_대상": target, "초안_비고": note,
                             "판정(O/X)": "", "정답_대상": ""})
    random.Random(SEED).shuffle(rows)
    sample = rows[:N]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["id", *sample[0].keys()])
        w.writeheader()
        for i, r in enumerate(sample, 1):
            w.writerow({"id": i, **r})
    print(f"표현 {len(rows)}개 중 {len(sample)}개 → {OUT}")


if __name__ == "__main__":
    main()
