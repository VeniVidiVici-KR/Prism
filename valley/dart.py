"""OpenDART(전자공시시스템) 원본 재무제표 조회 — yfinance 대조용 파일럿.

⚠️ 실험적 기능이다. 목적은 "DART 원본 재무제표가 yfinance보다 정확/유용한지"를 실측
검증하는 것뿐이고, 기존 yfinance 경로(valley/data.py 등)를 대체하지 않는다. 이 모듈은
완전히 독립적으로 동작한다.
"""

import io
import json
import os
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

import requests

CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
FINANCIALS_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
DISCLOSURE_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DISCLOSURE_DOC_URL_TEMPLATE = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

REPORT_CODE_ANNUAL = "11011"  # 사업보고서(연간)

# corp_code 매핑은 자주 안 바뀌므로 로컬 파일로도 캐싱한다 (프로세스 재시작 후에도 재사용).
_CORP_CODE_CACHE_PATH = Path(__file__).resolve().parent.parent / ".dart_corp_code_cache.json"
_CORP_CODE_CACHE_MAX_AGE_SECONDS = 86400

# 계정 매칭은 account_id(IFRS 표준/DART 확장 분류코드)를 우선으로 쓰고, 없거나 특이한 경우에만
# 한글 계정명 후보로 폴백한다. sj_div: BS=재무상태표, IS=손익계산서, CIS=포괄손익계산서.
#
# ⚠️ account_id를 우선순위로 둔 이유(실측으로 발견): 같은 회사 안에서도 같은 한글 계정명이
# 서로 다른 항목을 가리킬 수 있다. 예) SK하이닉스는 "지배기업의 소유주지분"이라는 이름이
# CIS 안에 두 번 나오는데, 하나는 총포괄이익 소속(42,984,408,000,000, account_id
# ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent)이고 다른 하나는 당기순이익
# 소속(42,919,287,000,000, account_id ifrs-full_ProfitLossAttributableToOwnersOfParent)이다.
# 한글 이름만 보고 먼저 나온 행을 집으면 응답 순서에 따라 틀린(더 큰) 값을 집을 위험이 있어,
# 표준화된 account_id로 먼저 정확히 짚고, id가 없는 예외적인 경우에만 이름 후보로 폴백한다.
#
# ⚠️ "당기순이익"의 account_id를 ProfitLossAttributableToOwnersOfParent(지배주주 귀속분)로 잡은
# 이유: yfinance의 Net Income이 (삼성전자·SK하이닉스·현대차 실측 기준) DART의 "당기순이익 총계"
# (ifrs-full_ProfitLoss, 비지배지분 포함)가 아니라 이 지배주주 귀속분과 정확히 일치했기 때문이다
# (예: 삼성전자 2025 44,260,956,000,000, 셋 다 소수점까지 완전 일치 — 총계 기준이었다면 최대
# 7%p까지 차이가 났을 것).
_ACCOUNT_SPECS: dict[str, dict] = {
    "매출액": {
        "sj_div_priority": ("IS", "CIS"),
        "account_ids": ["ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers"],
        "name_candidates": ["매출액", "수익(매출액)", "영업수익"],
    },
    "영업이익": {
        "sj_div_priority": ("IS", "CIS"),
        "account_ids": ["dart_OperatingIncomeLoss"],
        "name_candidates": ["영업이익", "영업이익(손실)"],
    },
    "당기순이익": {
        "sj_div_priority": ("IS", "CIS"),
        "account_ids": ["ifrs-full_ProfitLossAttributableToOwnersOfParent"],
        "name_candidates": [
            "지배기업 소유주지분", "지배기업소유주지분", "지배기업의 소유주지분",
            "지배기업의 소유주에게 귀속되는 당기순이익",
            "당기순이익", "당기순이익(손실)", "반기순이익(손실)", "분기순이익(손실)",
        ],
    },
    "자산총계": {"sj_div_priority": ("BS",), "account_ids": ["ifrs-full_Assets"], "name_candidates": ["자산총계"]},
    "부채총계": {"sj_div_priority": ("BS",), "account_ids": ["ifrs-full_Liabilities"], "name_candidates": ["부채총계"]},
    "자본총계": {"sj_div_priority": ("BS",), "account_ids": ["ifrs-full_Equity"], "name_candidates": ["자본총계"]},
}


class DartDataError(Exception):
    """DART 조회에 실패했을 때 발생 (키 없음/네트워크/파싱/매핑 없음 등)."""


def _get_api_key() -> str:
    key = os.environ.get("DART_API_KEY")
    if not key:
        raise DartDataError("DART_API_KEY가 설정되어 있지 않습니다. .env 파일을 확인하세요.")
    return key


def _load_corp_code_file_cache() -> dict[str, str] | None:
    if not _CORP_CODE_CACHE_PATH.exists():
        return None
    try:
        payload = json.loads(_CORP_CODE_CACHE_PATH.read_text(encoding="utf-8"))
        if time.time() - payload["fetched_at"] > _CORP_CODE_CACHE_MAX_AGE_SECONDS:
            return None
        return payload["mapping"]
    except Exception:
        return None  # 캐시 파일이 손상됐으면 그냥 다시 받는다


def _save_corp_code_file_cache(mapping: dict[str, str]) -> None:
    try:
        _CORP_CODE_CACHE_PATH.write_text(
            json.dumps({"fetched_at": time.time(), "mapping": mapping}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass  # 로컬 파일 저장 실패는 치명적이지 않다 (다음 조회 때 다시 받으면 됨)


def get_corp_code_map(*, force_refresh: bool = False) -> dict[str, str]:
    """전체 상장사의 {6자리 종목코드: DART corp_code} 매핑을 만든다.

    DART corpCode.xml(zip) 안에는 비상장사도 섞여 있어, stock_code(종목코드)가 있는
    항목만 남긴다. 매 조회마다 zip을 새로 받지 않도록 로컬 파일로 캐싱한다
    (views/_cache.py의 st.cache_data는 프로세스 재시작 시 사라지므로 이중 방어).
    """
    if not force_refresh:
        cached = _load_corp_code_file_cache()
        if cached is not None:
            return cached

    api_key = _get_api_key()
    try:
        resp = requests.get(CORP_CODE_URL, params={"crtfc_key": api_key}, timeout=30)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_bytes = zf.read("CORPCODE.xml")
    except Exception as e:
        raise DartDataError(f"DART corp_code 목록 조회에 실패했습니다: {e}") from e

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise DartDataError(f"DART corp_code 응답 파싱에 실패했습니다: {e}") from e

    mapping: dict[str, str] = {}
    for item in root.iter("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        if stock_code and corp_code:
            mapping[stock_code] = corp_code

    if not mapping:
        raise DartDataError("DART corp_code 목록이 비어 있습니다 (상장사 항목을 찾지 못함).")

    _save_corp_code_file_cache(mapping)
    return mapping


def get_corp_code(stock_code: str) -> str:
    """6자리 종목코드에 대응하는 DART corp_code를 반환한다."""
    mapping = get_corp_code_map()
    corp_code = mapping.get(stock_code)
    if not corp_code:
        raise DartDataError(f"'{stock_code}'에 대응하는 DART corp_code를 찾을 수 없습니다 (비상장 또는 목록에 없음).")
    return corp_code


def _find_account_row(rows: list[dict], spec: dict) -> dict | None:
    """sj_div 우선순위를 엄격히 지켜가며 계정을 찾는다.

    각 sj_div 안에서는 account_id(표준 분류코드)로 먼저 찾고, 못 찾으면 한글 계정명
    후보로 폴백한다 — 둘 다 sj_div 우선순위(예: IS 전체를 다 훑은 뒤에야 CIS로 넘어감)를
    지킨다. 같은 이름이 sj_div별로, 심지어 같은 sj_div 안에서도 서로 다른 항목을 가리킬 수
    있어서다(_ACCOUNT_SPECS 위 주석 참고) — sj_div를 집합으로 묶어 한꺼번에 뒤지면 응답
    안의 우연한 행 순서에 따라 엉뚱한 값을 집을 위험이 있다.
    """
    for sj_div in spec["sj_div_priority"]:
        for account_id in spec["account_ids"]:
            for row in rows:
                if row.get("sj_div") == sj_div and row.get("account_id") == account_id:
                    return row
        for name in spec["name_candidates"]:
            for row in rows:
                if row.get("sj_div") == sj_div and (row.get("account_nm") or "").strip() == name:
                    return row
    return None


def _parse_amount(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def get_dart_financial_statements(corp_code: str, bsns_year: int | None = None) -> dict:
    """corp_code의 최근 3개년(당기/전기/전전기) 핵심 재무제표를 한 번의 호출로 조회한다.

    연결재무제표(CFS)를 우선 시도하고, 없으면(연결 대상이 아닌 회사 등 status="013" 등)
    별도재무제표(OFS)로 폴백한다. 실제로 어느 쪽을 썼는지 반환값의 "fs_div_used"에 남긴다.

    Returns:
        {
            "fs_div_used": "CFS" | "OFS",
            "matched_account_names": {"매출액": "매출액" | None, ...},  # 실제 매칭된 DART 계정명
            "by_year": {2025: {"매출액": 1.2e14, ...}, 2024: {...}, 2023: {...}},
        }
    """
    api_key = _get_api_key()
    if bsns_year is None:
        bsns_year = date.today().year - 1  # 사업보고서는 통상 다음해 3월 제출

    last_error = None
    for fs_div in ("CFS", "OFS"):
        try:
            resp = requests.get(
                FINANCIALS_URL,
                params={
                    "crtfc_key": api_key,
                    "corp_code": corp_code,
                    "bsns_year": str(bsns_year),
                    "reprt_code": REPORT_CODE_ANNUAL,
                    "fs_div": fs_div,
                },
                timeout=15,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            last_error = f"DART 재무제표 조회 중 오류가 발생했습니다 ({fs_div}): {e}"
            continue

        status = payload.get("status")
        if status != "000":
            last_error = f"DART 응답 오류 ({fs_div}, status={status}): {payload.get('message', '알 수 없는 오류')}"
            continue

        rows = payload.get("list") or []
        if not rows:
            last_error = f"DART 응답에 데이터가 없습니다 ({fs_div})."
            continue

        matched_names: dict[str, str | None] = {}
        by_year: dict[int, dict[str, float | None]] = {
            bsns_year: {}, bsns_year - 1: {}, bsns_year - 2: {},
        }
        for label, spec in _ACCOUNT_SPECS.items():
            row = _find_account_row(rows, spec)
            if row is None:
                matched_names[label] = None
                for y in by_year:
                    by_year[y][label] = None
                continue
            matched_names[label] = row.get("account_nm")
            by_year[bsns_year][label] = _parse_amount(row.get("thstrm_amount"))
            by_year[bsns_year - 1][label] = _parse_amount(row.get("frmtrm_amount"))
            by_year[bsns_year - 2][label] = _parse_amount(row.get("bfefrmtrm_amount"))

        return {"fs_div_used": fs_div, "matched_account_names": matched_names, "by_year": by_year}

    raise DartDataError(last_error or "DART 재무제표를 연결/별도 어느 쪽으로도 조회하지 못했습니다.")


def _months_ago(today: date, months: int) -> date:
    """today에서 months개월 전 날짜를 구한다 (day는 1일로 고정 — 조회 시작일 용도라 정밀할 필요 없음)."""
    total_months = today.year * 12 + (today.month - 1) - months
    year, month = divmod(total_months, 12)
    return date(year, month + 1, 1)


def get_dart_disclosure_list(corp_code: str, *, months: int = 6, page_count: int = 30) -> list[dict]:
    """corp_code의 최근 공시 목록을 최신순으로 조회한다.

    ⚠️ bgn_de를 명시하지 않으면 DART가 기본 범위를 좁게 잡을 수 있어, 오늘 기준으로
    역산한 날짜를 항상 명시적으로 넣는다.

    결과가 없는 것(해당 기간 공시 없음, status="013")은 에러가 아니라 빈 리스트로 반환한다.
    조회 자체의 실패(키 없음/네트워크/그 외 오류)만 DartDataError로 구분해서 올린다.
    """
    api_key = _get_api_key()
    today = date.today()
    bgn_de = _months_ago(today, months).strftime("%Y%m%d")
    end_de = today.strftime("%Y%m%d")

    try:
        resp = requests.get(
            DISCLOSURE_LIST_URL,
            params={
                "crtfc_key": api_key,
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_no": 1,
                "page_count": page_count,
            },
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        raise DartDataError(f"DART 공시 목록 조회 중 오류가 발생했습니다: {e}") from e

    status = payload.get("status")
    if status == "013":  # 조회된 데이터가 없음 — 정상적인 "공시 없음" 상태
        return []
    if status != "000":
        raise DartDataError(f"DART 공시 목록 응답 오류 (status={status}): {payload.get('message', '알 수 없는 오류')}")

    rows = payload.get("list") or []
    disclosures = []
    for row in rows:
        rcept_no = row.get("rcept_no", "")
        disclosures.append(
            {
                "rcept_dt": row.get("rcept_dt", ""),
                "report_nm": row.get("report_nm", ""),
                "flr_nm": row.get("flr_nm", ""),
                "rcept_no": rcept_no,
                "rm": row.get("rm", ""),
                "url": DISCLOSURE_DOC_URL_TEMPLATE.format(rcept_no=rcept_no) if rcept_no else None,
            }
        )
    disclosures.sort(key=lambda d: d["rcept_dt"], reverse=True)
    return disclosures
