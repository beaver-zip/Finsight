"""평가셋을 LangSmith 데이터셋으로 올린다(사람 검수가 끝난 버전만 올린다).

데이터셋 이름: finsight-<평가셋>-v<N>. 같은 이름이 있으면 예제를 모두 지우고 다시 올린다.
사용: cd backends && PYTHONPATH=. python -m eval.upload_langsmith legal_qa routing ...
"""
from __future__ import annotations

import sys

from langsmith import Client

from app.settings import get_settings, require
from eval.common import DATASETS, load_jsonl

INPUT_KEYS = {"legal_qa": ["question"], "routing": ["question"], "composite_tasks": ["question", "company"],
              "multiturn": ["turns"], "report_companies": ["corp_code", "name", "stratum"],
              "news_labels": ["company", "url", "title"]}


def upload(name: str) -> str:
    s = get_settings()
    client = Client(api_key=require(s.langsmith_api_key, "LANGSMITH_API_KEY"))
    path = sorted(DATASETS.glob(f"{name}.v*.jsonl"), key=lambda p: int(p.stem.rsplit(".v", 1)[1]))[-1]
    ds_name = f"finsight-{name}-v{path.stem.rsplit('.v', 1)[1]}"
    rows = load_jsonl(path)
    if client.has_dataset(dataset_name=ds_name):
        ds = client.read_dataset(dataset_name=ds_name)
        for ex in client.list_examples(dataset_id=ds.id):
            client.delete_example(ex.id)
    else:
        ds = client.create_dataset(ds_name, description=f"Finsight v2 평가셋 {path.name}")
    keys = INPUT_KEYS.get(name, ["id"])
    client.create_examples(dataset_id=ds.id,
                           inputs=[{k: r.get(k) for k in keys} for r in rows],
                           outputs=[{k: v for k, v in r.items() if k not in keys} for r in rows],
                           metadata=[{"id": r["id"], "split": r.get("split")} for r in rows])
    return f"{ds_name}: {len(rows)}개"


if __name__ == "__main__":
    for n in sys.argv[1:]:
        print(upload(n))
