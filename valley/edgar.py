"""SEC EDGAR 공시 목록 조회 — 미국 상장기업의 최근 공시(10-K/10-Q/8-K 등) 목록을 가져온다.

⚠️ 파일럿 기능이다. 한국 종목의 DART 공시 섹션과 대칭 위치이며, 이번엔 목록 조회까지만
다룬다 — 유형 필터·본문 요약은 다음 라운드. 기존 yfinance/DART 경로와 독립적으로 동작한다.
"""

import json
import time
from pathlib import Path

import requests

# ⚠️ SEC EDGAR API는 User-Agent 헤더에 연락처가 없으면 요청을 막는다(실측 확인: User-Agent
# 없이 호출 시 HTTP 403 "Request Rate Threshold Exceeded", 있으면 200 정상 응답). 아래 이메일은
# 플레이스홀더이니 실제 배포 시 연락 가능한 주소로 바꿀 것.
_USER_AGENT = "PRISM research app admin@example.com"
_HEADERS = {"User-Agent": _USER_AGENT}

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_ARCHIVE_URL_TEMPLATE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dash}/{primary_document}"

# 티커-CIK 매핑은 자주 안 바뀌므로 DART corp_code 캐시와 동일하게 로컬 파일로도 캐싱한다
# (views/_cache.py의 st.cache_data는 프로세스 재시작 시 사라지므로 이중 방어).
_TICKER_CIK_CACHE_PATH = Path(__file__).resolve().parent.parent / ".edgar_ticker_cik_cache.json"
_TICKER_CIK_CACHE_MAX_AGE_SECONDS = 86400

# 실제 응답에서 자주 보이는 주요 유형에만 짧은 한글 설명을 병기한다. 모르는 유형은 코드 그대로
# 보여준다(억지로 다 매핑하지 않음).
_FORM_TYPE_LABELS = {
    "10-K": "10-K(연간보고서)",
    "10-Q": "10-Q(분기보고서)",
    "8-K": "8-K(수시공시)",
    "DEF 14A": "DEF 14A(주총 안건)",
    "4": "Form 4(임원·대주주 지분변동)",
    "3": "Form 3(최초 지분보고)",
    "144": "144(지분매도 신고)",
    "SD": "SD(분쟁광물 보고)",
    "SC 13G": "SC 13G(5%이상 지분보고)",
    "SCHEDULE 13G": "SCHEDULE 13G(5%이상 지분보고)",
    "SCHEDULE 13G/A": "SCHEDULE 13G/A(지분보고 정정)",
}


class EdgarDataError(Exception):
    """EDGAR 공시 데이터를 조회하지 못했을 때 발생 (네트워크/매핑 없음/파싱 등)."""


def describe_form_type(form: str) -> str:
    """공시 유형 코드에 짧은 한글 설명을 병기한다. 모르는 유형은 코드 그대로 반환."""
    return _FORM_TYPE_LABELS.get(form, form)


def _load_ticker_cik_file_cache() -> dict[str, str] | None:
    if not _TICKER_CIK_CACHE_PATH.exists():
        return None
    try:
        payload = json.loads(_TICKER_CIK_CACHE_PATH.read_text(encoding="utf-8"))
        if time.time() - payload["fetched_at"] > _TICKER_CIK_CACHE_MAX_AGE_SECONDS:
            return None
        return payload["mapping"]
    except Exception:
        return None  # 캐시 파일이 손상됐으면 그냥 다시 받는다


def _save_ticker_cik_file_cache(mapping: dict[str, str]) -> None:
    try:
        _TICKER_CIK_CACHE_PATH.write_text(
            json.dumps({"fetched_at": time.time(), "mapping": mapping}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass  # 로컬 파일 저장 실패는 치명적이지 않다 (다음 조회 때 다시 받으면 됨)


def get_ticker_to_cik_map(*, force_refresh: bool = False) -> dict[str, str]:
    """SEC가 공개하는 전체 티커->CIK 매핑을 {티커(대문자): 10자리 zero-pad CIK}로 반환한다.

    company_tickers.json의 cik_str은 zero-pad 안 된 정수로 온다(실측 확인, 예: AAPL -> 320193).
    submissions API는 10자리 zero-pad 형식(예: 0000320193)을 요구하므로 여기서 미리 변환해둔다.
    """
    if not force_refresh:
        cached = _load_ticker_cik_file_cache()
        if cached is not None:
            return cached

    try:
        resp = requests.get(_TICKERS_URL, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise EdgarDataError(f"SEC 티커-CIK 매핑을 가져오지 못했습니다: {e}") from e

    mapping = {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in data.values()}
    if not mapping:
        raise EdgarDataError("SEC 티커-CIK 매핑이 비어 있습니다.")

    _save_ticker_cik_file_cache(mapping)
    return mapping


def get_cik(ticker: str) -> str:
    """티커에 해당하는 10자리 zero-pad CIK를 반환한다.

    신규상장·비주류 등 SEC 매핑에 없는 티커도 있을 수 있어, 그 경우는 EdgarDataError로
    명확히 구분한다(앱이 죽지 않고 "찾을 수 없음" 안내로 이어질 수 있도록).
    """
    mapping = get_ticker_to_cik_map()
    cik = mapping.get(ticker.upper())
    if cik is None:
        raise EdgarDataError(f"'{ticker}'를 EDGAR 티커 목록에서 찾을 수 없습니다.")
    return cik


def get_recent_filings(cik: str, limit: int = 30) -> list[dict]:
    """특정 CIK의 최근 공시 목록을 최신순으로 최대 limit건 반환한다.

    ⚠️ submissions API의 filings.recent는 레코드 리스트가 아니라 "컬럼별 배열"로 온다
    (실측 확인: form/filingDate/accessionNumber 등이 각각 같은 길이의 리스트) — 인덱스로
    묶어야 레코드 하나가 된다.

    Returns:
        [{"form": "10-K", "filing_date": "2025-11-01", "description": "...", "url": "https://..."}, ...]
    """
    url = _SUBMISSIONS_URL.format(cik=cik)
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise EdgarDataError(f"SEC 공시 목록을 가져오지 못했습니다: {e}") from e

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form") or []
    filing_dates = recent.get("filingDate") or []
    accession_numbers = recent.get("accessionNumber") or []
    primary_documents = recent.get("primaryDocument") or []
    primary_descriptions = recent.get("primaryDocDescription") or []

    count = min(limit, len(forms))
    filings = []
    for i in range(count):
        accession_no_dash = accession_numbers[i].replace("-", "")
        primary_document = primary_documents[i]
        filings.append(
            {
                "form": forms[i],
                "filing_date": filing_dates[i],
                "description": primary_descriptions[i] if i < len(primary_descriptions) else "",
                "url": _ARCHIVE_URL_TEMPLATE.format(
                    cik=cik, accession_no_dash=accession_no_dash, primary_document=primary_document
                ),
            }
        )
    return filings
