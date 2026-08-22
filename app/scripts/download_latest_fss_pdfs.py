"""금융감독원 최신 보도자료 PDF를 RAG 원본 디렉터리에 저장한다."""

from __future__ import annotations

import argparse
import html
import os
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote_plus

import requests
from dotenv import load_dotenv

API_URL = "https://www.fss.or.kr/fss/kr/openApi/api/bodoInfo.jsp"
TARGET_DIR = Path(__file__).resolve().parents[2] / "docs" / "embed_target_pdfs"


def fetch_documents(auth_key: str, start: date, end: date) -> list[dict]:
    response = requests.post(
        API_URL,
        data={
            "apiType": "json",
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "authKey": auth_key,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("reponse") or payload.get("response") or {}
    if str(result.get("resultCode")) != "1":
        raise RuntimeError(result.get("resultMsg", "금융감독원 API 조회 실패"))
    documents = result.get("result") or []
    return documents if isinstance(documents, list) else [documents]


def download_pdfs(documents: list[dict], target_dir: Path = TARGET_DIR) -> list[Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for document in documents:
        names = (document.get("atchfileNm") or "").split("|")
        urls = (document.get("atchfileUrl") or "").split("|")
        for raw_name, raw_url in zip(names, urls):
            name = Path(unquote_plus(raw_name).replace("\\", "/")).name
            path = target_dir / name
            if not raw_url or not name.lower().endswith(".pdf") or path.exists():
                continue
            response = requests.get(html.unescape(raw_url), timeout=60)
            response.raise_for_status()
            if b"%PDF" not in response.content[:1024]:
                raise RuntimeError(f"PDF가 아닌 응답입니다: {name}")
            path.write_bytes(response.content)
            saved.append(path)
    return saved


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="최신 금융감독원 보도자료 PDF 다운로드")
    parser.add_argument("--days", type=int, default=7, help="조회할 최근 일수 (기본: 7)")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days는 1 이상이어야 합니다")

    auth_key = os.getenv("FSS_AUTH_KEY")
    if not auth_key:
        parser.error("FSS_AUTH_KEY 환경변수가 필요합니다")

    end = date.today()
    start = end - timedelta(days=args.days - 1)
    saved = download_pdfs(fetch_documents(auth_key, start, end))
    print(f"{start}~{end}: PDF {len(saved)}개 저장 ({TARGET_DIR})")


if __name__ == "__main__":
    main()
