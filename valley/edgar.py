"""SEC EDGAR 공시 목록 조회 — 미국 상장기업의 최근 공시(10-K/10-Q/8-K 등) 목록을 가져온다.

⚠️ 파일럿 기능이다. 한국 종목의 DART 공시 섹션과 대칭 위치이며, 이번엔 목록 조회까지만
다룬다 — 유형 필터·본문 요약은 다음 라운드. 기존 yfinance/DART 경로와 독립적으로 동작한다.
"""

import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ⚠️ SEC EDGAR API는 User-Agent 헤더에 연락처가 없으면 요청을 막는다(실측 확인: User-Agent
# 없이 호출 시 HTTP 403 "Request Rate Threshold Exceeded", 있으면 200 정상 응답). 아래 이메일은
# 플레이스홀더이니 실제 배포 시 연락 가능한 주소로 바꿀 것. browse-edgar(유형별 조회)도 동일
# 규칙이 실측으로 확인됐다(User-Agent 없이 403).
_USER_AGENT = "PRISM research app admin@example.com"
_HEADERS = {"User-Agent": _USER_AGENT}

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_ARCHIVE_URL_TEMPLATE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dash}/{primary_document}"
_BROWSE_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
_FILING_INDEX_URL_TEMPLATE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dash}/{accession_number}-index.htm"

# 8-K 본문 요약 대상 텍스트 길이 가드. 실측 결과 8-K 자체는 짧지만(원문+EX-99.1 합쳐도 보통
# 수천~2만자) 예외적으로 큰 첨부가 붙는 경우를 대비해 상한을 둔다 — 넘으면 앞부분만 쓰고
# 잘렸다는 사실을 결과에 명시한다(숨기지 않는다).
_FILING_TEXT_MAX_CHARS = 45000

# 10-K/10-Q MD&A(경영진의 논의 및 분석) 섹션 길이 가드. 실측 결과(AAPL 10-K 1.8만자, AAPL
# 10-Q 2.2만자, MSFT 10-K 4.8만자, MSFT 10-Q 5.8만자) 대부분 9만자 이내라 이 값을 상한으로
# 잡는다 — 넘으면 앞부분만 쓰고 잘렸다는 사실을 결과에 명시한다(8-K와 동일한 투명성 원칙).
_MDA_TEXT_MAX_CHARS = 90000

# 10-K/10-Q는 MD&A를 가리키는 Item 번호가 다르다(실측 확인) — 10-K는 Item 7(~7A 또는 8
# 직전까지), 10-Q는 Part I Item 2(~3 또는 4 직전까지). form_type별로 반드시 분기해야 한다.
# "end"를 우선 쓰고 못 찾으면 "end_fallback"으로 넘어간다(일부 문서는 7A를 8과 합쳐 쓰기도 함).
_MDA_SECTION_SPECS: dict[str, dict[str, str]] = {
    "10-K": {
        "start": r"^Item\s+7\.?\s*Management",
        "end": r"^Item\s+7A\.?\s*Quantitative",
        "end_fallback": r"^Item\s+8\.?\s*Financial Statements",
    },
    "10-Q": {
        "start": r"^Item\s+2\.?\s*Management",
        "end": r"^Item\s+3\.?\s*Quantitative",
        "end_fallback": r"^Item\s+4\.?\s*Controls",
    },
}

# MD&A 추출 후 경계 바로 앞에 남는 페이지 머리말 잔재("PART II", "Item 7A" 단독, "Item 3, 4"
# 처럼 여러 항목을 한 줄에 묶은 머리말, 페이지번호 숫자만 있는 줄 등 실측으로 확인된 흔한
# 패턴)를 정리한다 — 실질 내용은 아니라 요약 품질에 영향은 적지만, 깔끔하게 잘라낸다.
_MDA_NOISE_TAIL_PATTERN = re.compile(r"^(PART\s+[IVX]+|Item\s+\d+[A-Z]?(,\s*\d+[A-Z]?)*)$", re.IGNORECASE)

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

# 유형 필터용 범주 묶음 — AAPL/MSFT/JPM/NVDA/GOOGL/TSLA 실측(각 최근 100건) 기준으로 실제
# 나타나는 form 문자열을 확인해 매핑했다. ⚠️ 여기 없는 form(13F-HR, CERT, 8-A12B, S-3ASR,
# ARS, IRANNOTICE, 25-NSE 등 드물게 보이는 유형)은 의도적으로 다 나열하지 않고 "기타" 범주로
# 묶는다 — categorize_form()이 매핑되지 않은 form을 "기타"로 폴백하므로, 새 form이 나타나도
# 절대 화면에서 통째로 사라지지 않는다(필터에서 빠졌다고 데이터가 없어지면 안 된다는 요구사항).
FORM_CATEGORIES: dict[str, list[str]] = {
    "실적·정기보고": ["10-K", "10-K/A", "10-Q", "10-Q/A"],
    "중요 사건": ["8-K", "8-K/A"],
    "지분 변동": [
        "3", "3/A", "4", "4/A", "5", "5/A",
        "SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A",
        "SC 13G", "SC 13G/A", "SCHEDULE 13G", "SCHEDULE 13G/A",
        "144", "144/A",
    ],
    # ⚠️ S-1/S-3/S-8(증권신고서)은 의도적으로 "증권 발행"에 넣지 않고 여기 남겨뒀다 — 이들은
    # "향후 증권을 발행할 수 있게 등록"하는 절차 서류(발행 등록)라, 실제 발행 조건(금액·만기·
    # 쿠폰)이 담기는 424B 계열(실제 발행)과 성격이 다르다. 신용시장 라운드의 목적(회사채
    # 조달 "실행" 동향 모니터링)에는 실제 발행 시점의 424B/FWP가 맞지, 몇 달~몇 년 전에
    # 미리 걸어둔 shelf 등록(S-3)까지 섞으면 "언제 실제로 자금을 조달했는가"가 흐려진다.
    "주주총회·기타": [
        "DEF 14A", "DEFA14A", "DEFM14A", "PRE 14A", "PREM14A",
        "S-1", "S-1/A", "S-3", "S-3/A", "S-8", "S-8 POS",
    ],
    # ⚠️ 424B 계열(발행설명서)과 FWP(발행 조건을 담은 마케팅 자료)를 "주주총회·기타"에서
    # 분리했다 — 신용시장 라운드의 목적(회사채 실제 발행 동향)에 맞춘 재분류다. 기존
    # "주주총회·기타"에 뭉쳐 있으면 채권 발행을 보려는 목적과 안 맞고, DEF 14A(주총) 등과도
    # 성격이 완전히 다르다(주총 안건 vs 자본시장 조달). Stock Research의 EDGAR 필터는 범주
    # 이름/구성만 바뀌고 나머지(유형 필터 UI, 전체보기, 직접조회 버튼) 동작은 그대로다.
    "증권 발행": [
        "424B1", "424B2", "424B3", "424B4", "424B5", "424B8",
        "FWP",
    ],
}
OTHER_CATEGORY = "기타"
ALL_CATEGORIES = list(FORM_CATEGORIES.keys()) + [OTHER_CATEGORY]


def categorize_form(form: str) -> str:
    """form 코드를 실무 범주로 묶는다. 매핑에 없는 form은 전부 "기타"로 폴백한다(누락 없음)."""
    for category, forms in FORM_CATEGORIES.items():
        if form in forms:
            return category
    return OTHER_CATEGORY


# "이 유형 직접 조회하기" 버튼용 — 범주 안의 form을 전부 개별 호출하면(예: "지분 변동"은 16종)
# 호출이 과도해지므로, 카테고리를 대표하는 핵심 유형 1~2개만 골라 조회한다. "기타"는 범주
# 자체가 잡다한 폴백이라 대표 유형을 고를 수 없어 제외한다.
CATEGORY_PRIMARY_FORM_TYPES: dict[str, list[str]] = {
    "실적·정기보고": ["10-K", "10-Q"],
    "중요 사건": ["8-K"],
    "지분 변동": ["4", "SC 13G"],
    "주주총회·기타": ["DEF 14A"],
    "증권 발행": ["424B2"],
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
        [{"form": "10-K", "filing_date": "2025-11-01", "description": "...", "url": "https://...",
          "accession_number": "0000320193-25-000079"}, ...]
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
        accession_number = accession_numbers[i]
        accession_no_dash = accession_number.replace("-", "")
        primary_document = primary_documents[i]
        filings.append(
            {
                "form": forms[i],
                "filing_date": filing_dates[i],
                "description": primary_descriptions[i] if i < len(primary_descriptions) else "",
                "url": _ARCHIVE_URL_TEMPLATE.format(
                    cik=cik, accession_no_dash=accession_no_dash, primary_document=primary_document
                ),
                "accession_number": accession_number,
            }
        )
    return filings


def get_filings_by_type(cik: str, form_type: str, limit: int = 10) -> list[dict]:
    """특정 form type의 공시만 최신순으로 최대 limit건 직접 조회한다.

    ⚠️ get_recent_filings("최근 N건에서 거르기")의 한계 때문에 만들었다 — JPM처럼 424B2
    (구조화채권 발행서류) 발행량이 압도적인 종목은 실측 결과 최근 1000건 안에도 10-K/10-Q가
    하나도 없을 수 있다(과거로 더 거슬러 올라가는 submissions API의 filings.files 방식도
    검토했으나, JPM은 파일 하나(~2000건)에도 10-K가 0건이라 몇 개를 더 받아야 할지 알 수
    없어 비실용적이었다 — 실측 후 기각). 대신 SEC의 browse-edgar 엔드포인트(type 파라미터로
    서버 사이드에서 이미 걸러줌)를 쓴다 — 1회 호출로 원하는 유형만 정확히 받는다.

    ⚠️ 응답은 Atom XML이고, 실측 확인 결과 <entry>의 자식 원소(filing-date/filing-type/
    form-name/filing-href 등)까지 전부 Atom 네임스페이스(http://www.w3.org/2005/Atom)
    안에 있어 네임스페이스 접두어 없이 찾으면 매칭되지 않는다 — 모든 탐색에 네임스페이스를
    명시한다.

    filing-href는 원문 문서 자체가 아니라 그 공시의 "색인 페이지"(index page) URL이다
    (get_recent_filings의 url은 원문 문서를 직접 가리키는 것과 다름 — browse-edgar 응답에는
    개별 원문 파일명이 없어 이 방식으로는 얻을 수 없다). 실제로 열리는 유효한 링크임을
    실측으로 확인했다. accession-number는 atom 응답에도 그대로 포함돼 있어(실측 확인)
    get_8k_filing_text가 어느 목록에서 온 필링이든 동일하게 쓸 수 있다.

    Returns:
        [{"form": "10-K", "filing_date": "2026-02-13", "description": "Annual report...", "url": "https://...",
          "accession_number": "0001628280-26-008131"}, ...]
    """
    params = {
        "action": "getcompany",
        "CIK": cik,
        "type": form_type,
        "dateb": "",
        "owner": "include",
        "count": limit,
        "output": "atom",
    }
    try:
        resp = requests.get(_BROWSE_URL, params=params, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        raise EdgarDataError(f"SEC EDGAR 유형별 공시 조회에 실패했습니다: {e}") from e

    filings = []
    for entry in root.findall("a:entry", _ATOM_NS):
        content = entry.find("a:content", _ATOM_NS)
        if content is None:
            continue
        filing_type = content.findtext("a:filing-type", default="", namespaces=_ATOM_NS)
        filing_date = content.findtext("a:filing-date", default="", namespaces=_ATOM_NS)
        form_name = content.findtext("a:form-name", default="", namespaces=_ATOM_NS)
        filing_href = content.findtext("a:filing-href", default="", namespaces=_ATOM_NS)
        accession_number = content.findtext("a:accession-number", default="", namespaces=_ATOM_NS)
        filings.append(
            {
                "form": filing_type or form_type,
                "filing_date": filing_date,
                "description": form_name,
                "accession_number": accession_number,
                "url": filing_href,
            }
        )
    return filings


def _html_to_text(content: bytes) -> str:
    """HTML(바이트) 문서를 요약용 평문으로 변환한다. script/style 제거, 공백 정리.

    ⚠️ 실측 확인: 최신 8-K는 Inline XBRL(iXBRL)로 제출돼 <div style="display:none"> 안에
    체크박스 플래그·컨텍스트 태그 등이 잔뜩 숨어있다(예: AAPL 8-K에서 "true true true..."
    같은 잡음이 본문 앞에 수백자 붙는 것 확인) — CSS로는 안 보이지만 BeautifulSoup의
    get_text는 그대로 뽑아버려 요약 품질을 떨어뜨린다. display:none 요소를 먼저 제거하면
    실질 본문(둘 다 "UNITED STATES SECURITIES AND EXCHANGE COMMISSION..."로 바로 시작)만
    남는 것으로 실측 확인했다.

    표는 완벽히 보존하지 않는다(요약이 목적이라 텍스트 흐름이 더 중요) — BeautifulSoup의
    get_text가 셀 텍스트를 공백으로 이어붙이는 정도로 충분하다.
    """
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for tag in soup.find_all(style=lambda s: s and "display:none" in s.replace(" ", "")):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


def _find_filing_document_url(cik: str, accession_number: str, doc_type: str) -> str | None:
    """공시 색인 페이지(index.htm)의 "Document Format Files" 표에서 doc_type과 정확히 일치하는
    문서의 절대 URL을 찾는다. 못 찾으면 None(호출부가 "없음"으로 처리하면 된다 — 모든 8-K가
    첨부를 갖는 건 아니므로 정상 상황이다).

    ⚠️ 파일명을 추측하지 않는다 — 실측 확인 결과 이 표의 "Type" 열에 "8-K"/"EX-99.1" 같은
    값이 SEC가 직접 분류해 정확히 표기돼 있어, 그 값을 그대로 매칭한다(AAPL/MSFT 두 회사의
    서로 다른 제출 도구가 만든 문서에서 동일 구조를 확인).
    """
    accession_no_dash = accession_number.replace("-", "")
    index_url = _FILING_INDEX_URL_TEMPLATE.format(
        cik=cik, accession_no_dash=accession_no_dash, accession_number=accession_number
    )
    try:
        resp = requests.get(index_url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception:
        return None  # 색인 조회 실패는 치명적이지 않다 — 호출부가 없음으로 처리

    soup = BeautifulSoup(resp.content, "html.parser")
    table = soup.find("table", attrs={"class": "tableFile", "summary": "Document Format Files"})
    if table is None:
        return None

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        if cells[3].get_text(strip=True) != doc_type:
            continue
        link = cells[2].find("a")
        if link is None or not link.get("href"):
            return None
        href = link["href"]
        if href.startswith("/ix?doc="):  # inline XBRL 뷰어 경유 링크 — 실제 문서 경로만 남긴다
            href = href.split("/ix?doc=", 1)[1]
        if not href.startswith("http"):
            href = "https://www.sec.gov" + href
        return href
    return None


def get_8k_filing_text(cik: str, accession_number: str, max_chars: int = _FILING_TEXT_MAX_CHARS) -> dict:
    """8-K 공시의 요약용 원문 텍스트를 구성한다.

    ⚠️ 실측(AAPL 2026-04-30 8-K, MSFT 8-K 2건) 결과:
    - Item 2.02(실적 발표) 같은 8-K는 primary document가 "보도자료는 Exhibit 99.1 참조"라는
      표지 한 장뿐이고(AAPL 케이스, 평문 약 4천자 중 실질 정보 없음), 진짜 내용은 EX-99.1
      (보도자료) 첨부에 있다. 색인 페이지의 문서 표(Type 열)에 "EX-99.1"이 정확히 표기돼
      있어 이걸로 찾아 함께 가져온다.
    - Item 5.02(임원 변경) 같은 8-K는 첨부가 아예 없고 본문 자체가 실질 내용이다(MSFT 케이스).
      이 경우 EX-99.1을 찾지 못하는 게 정상이며, 본문만으로 충분하다.
    - 그래서 매번 EX-99.1 유무를 색인에서 실제로 확인하고, 있을 때만 이어붙인다 — 무조건
      찾으려 하거나 무조건 본문만 쓰지 않는다.

    Returns:
        {"text": str, "truncated": bool, "used_exhibit": bool}

    Raises:
        EdgarDataError: primary document 자체를 가져오지 못했을 때(치명적 실패).
    """
    primary_url = _find_filing_document_url(cik, accession_number, "8-K")
    if primary_url is None:
        raise EdgarDataError("공시 색인에서 8-K 원문 문서를 찾지 못했습니다.")

    try:
        resp = requests.get(primary_url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        raise EdgarDataError(f"8-K 원문을 가져오지 못했습니다: {e}") from e
    primary_text = _html_to_text(resp.content)

    combined_text = primary_text
    used_exhibit = False
    exhibit_url = _find_filing_document_url(cik, accession_number, "EX-99.1")
    if exhibit_url:
        try:
            exhibit_resp = requests.get(exhibit_url, headers=_HEADERS, timeout=20)
            exhibit_resp.raise_for_status()
            exhibit_text = _html_to_text(exhibit_resp.content)
            combined_text = primary_text + "\n\n[첨부 EX-99.1]\n" + exhibit_text
            used_exhibit = True
        except Exception:
            pass  # 첨부 조회가 실패해도 본문만으로 계속 진행한다 (전체를 실패시키지 않음)

    truncated = len(combined_text) > max_chars
    if truncated:
        combined_text = combined_text[:max_chars]

    return {"text": combined_text, "truncated": truncated, "used_exhibit": used_exhibit}


def _html_to_lines(content: bytes) -> list[str]:
    """HTML을 줄(블록) 단위 텍스트 리스트로 변환한다.

    get_8k_filing_text가 쓰는 _html_to_text와 달리 공백 하나로 뭉개지 않고 개행을 보존한다 —
    MD&A 섹션 경계를 "그 줄에 항목번호와 제목이 함께 있는가"로 판별해야 하기 때문이다
    (get_mda_section_text 참고).
    """
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for tag in soup.find_all(style=lambda s: s and "display:none" in s.replace(" ", "")):
        tag.decompose()
    text = soup.get_text(separator="\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    lines = [ln.strip() for ln in text.split("\n")]
    return [ln for ln in lines if ln]


def get_mda_section_text(
    cik: str, accession_number: str, form_type: str, max_chars: int = _MDA_TEXT_MAX_CHARS
) -> dict:
    """10-K/10-Q의 MD&A(경영진의 논의 및 분석, Item 7 또는 Item 2) 섹션만 추출한다.

    ⚠️ 문서 전체(수백 페이지)를 요약에 넣으면 토큰·비용이 폭증하고 요약 품질도 떨어진다 —
    MD&A만 뽑는다.

    ⚠️ 목차(TOC) 오탐 방지(실측으로 확인한 함정): "Item 7"만으로 찾으면 문서 앞 목차의
    "Item 7 ... 21"(페이지번호) 항목이 먼저 잡혀 목차 조각만 나온다. 실측 확인 결과 목차는
    항목번호와 제목이 서로 다른 줄로 떨어져 있고("Item 7." 한 줄 + "Management's Discussion..."
    다음 줄 + 페이지번호 또 다음 줄), 진짜 소제목(heading)은 항목번호 바로 뒤에 정식 제목이
    같은 줄로 붙어 있다("Item 7.    Management's Discussion and Analysis..." 한 줄). 그래서
    "항목번호 뒤에 정식 제목이 곧바로 이어지는 줄"만 진짜 소제목으로 인정한다 — AAPL(Workiva
    템플릿)·MSFT(다른 제출대행사 템플릿) 둘 다 이 구조가 동일함을 실측으로 확인했다. 문서
    본문 안의 교차 참조(예: "…재무제표는 Part II, Item 8 참조")는 정식 제목이 이어지지 않아
    이 조건에 걸리지 않는다.

    ⚠️ 10-K와 10-Q는 MD&A를 가리키는 Item 번호가 다르다(_MDA_SECTION_SPECS 참고, 실측 확인) —
    form_type으로 반드시 분기한다. 지원하지 않는 form_type(10-K/A, 10-Q/A 등)은 EdgarDataError.
    (실측: TSLA 10-K/A는 Part III만 담은 정정본이라 "Item 7" 자체가 아예 없었다 — 이런
    이유로 이번 라운드는 정정본(/A)을 지원하지 않는다.)

    Returns:
        {"text": str, "truncated": bool}

    Raises:
        EdgarDataError: form_type 미지원, 원문 조회 실패, 또는 MD&A 시작/끝 소제목을 못 찾았을
            때. ⚠️ 못 찾았을 때 전체 문서로 폴백하지 않는다 — 비용 폭증을 막기 위해 명확히
            실패로 처리한다.
    """
    spec = _MDA_SECTION_SPECS.get(form_type)
    if spec is None:
        raise EdgarDataError(f"'{form_type}'은 MD&A 추출을 지원하지 않습니다 (10-K/10-Q만 지원).")

    primary_url = _find_filing_document_url(cik, accession_number, form_type)
    if primary_url is None:
        raise EdgarDataError(f"공시 색인에서 {form_type} 원문 문서를 찾지 못했습니다.")

    try:
        resp = requests.get(primary_url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        raise EdgarDataError(f"{form_type} 원문을 가져오지 못했습니다: {e}") from e

    lines = _html_to_lines(resp.content)

    start_idx = [i for i, ln in enumerate(lines) if re.match(spec["start"], ln, re.IGNORECASE)]
    if not start_idx:
        raise EdgarDataError(
            "MD&A(경영진의 논의 및 분석) 섹션의 시작 부분을 찾지 못했습니다. 문서 구조가 예상과 달라 추출할 수 없습니다."
        )

    end_idx = [i for i, ln in enumerate(lines) if re.match(spec["end"], ln, re.IGNORECASE)]
    if not end_idx:
        end_idx = [i for i, ln in enumerate(lines) if re.match(spec["end_fallback"], ln, re.IGNORECASE)]
    if not end_idx:
        raise EdgarDataError(
            "MD&A(경영진의 논의 및 분석) 섹션의 끝 부분을 찾지 못했습니다. 문서 구조가 예상과 달라 추출할 수 없습니다."
        )

    start = start_idx[-1]
    end = end_idx[-1]
    if end <= start:
        raise EdgarDataError("MD&A 섹션 경계를 정상적으로 판별하지 못했습니다(시작이 끝보다 뒤에 있음).")

    mda_lines = lines[start:end]
    while mda_lines and (_MDA_NOISE_TAIL_PATTERN.match(mda_lines[-1]) or mda_lines[-1].isdigit()):
        mda_lines.pop()

    mda_text = "\n".join(mda_lines)
    if not mda_text.strip():
        raise EdgarDataError("추출된 MD&A 섹션이 비어 있습니다.")

    truncated = len(mda_text) > max_chars
    if truncated:
        mda_text = mda_text[:max_chars]

    return {"text": mda_text, "truncated": truncated}
