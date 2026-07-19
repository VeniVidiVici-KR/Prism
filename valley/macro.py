"""매크로 지표(물가/금리/고용/성장/통화·환율) 조회 레이어.

- 미국 및 대부분의 한국 지표는 FRED 공개 CSV 엔드포인트(API 키 불필요)에서 가져온다.
- 한국 CPI/M2는 FRED 미러링 데이터가 갱신을 멈춰서, 한국은행 ECOS OpenAPI로 우선 조회하고
  키가 없거나 실패하면 FRED 데이터로 폴백한다. ECOS는 같은 지표라도 "신지표"/"구지표"로
  테이블이 나뉘어 있고 구지표는 대부분 2000년대 초반에 갱신이 끊겼으니, 새 지표를 추가할
  때는 반드시 최근 데이터가 실제로 나오는 stat_code인지 확인해야 한다.
"""

import os
from datetime import date

import pandas as pd
import requests

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
ECOS_URL = (
    "https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/5000/"
    "{stat_code}/{cycle}/{start}/{end}/{item_code}"
)


class MacroDataError(Exception):
    """매크로 지표를 조회하지 못했을 때 발생."""


# 카테고리 -> [{key, label, ...}] 형태의 카탈로그. get_indicator_series가 key로 조회한다.
INDICATORS = {
    "US": {
        "물가": [
            {"key": "US_CPI", "label": "CPI (소비자물가지수)", "fred_id": "CPIAUCSL"},
            {"key": "US_CORE_CPI", "label": "근원 CPI", "fred_id": "CPILFESL"},
            {"key": "US_PCE", "label": "PCE (개인소비지출물가)", "fred_id": "PCEPI"},
            {"key": "US_CORE_PCE", "label": "근원 PCE", "fred_id": "PCEPILFE"},
            {"key": "US_PPI", "label": "생산자물가지수(PPI)", "fred_id": "PPIACO"},
        ],
        "금리": [
            {"key": "US_FEDFUNDS", "label": "연방기금금리(기준금리)", "fred_id": "FEDFUNDS"},
            {"key": "US_DGS10", "label": "10년 국채금리", "fred_id": "DGS10"},
            {"key": "US_DGS2", "label": "2년 국채금리", "fred_id": "DGS2"},
            {"key": "US_DGS3MO", "label": "3개월 국채금리", "fred_id": "DGS3MO"},
            {"key": "US_T10Y2Y", "label": "장단기 금리차 (10Y-2Y)", "fred_id": "T10Y2Y"},
            {"key": "US_T10Y3M", "label": "장단기 금리차 (10Y-3M, 침체 예측력 더 강함)", "fred_id": "T10Y3M"},
        ],
        "위험지표": [
            {"key": "US_HY_SPREAD", "label": "하이일드 스프레드 (신용 리스크 프리미엄, %p)", "fred_id": "BAMLH0A0HYM2"},
            {"key": "US_VIX", "label": "VIX 변동성지수 (낮을수록 안정)", "fred_id": "VIXCLS"},
            {"key": "US_BREAKEVEN10", "label": "10년 기대인플레이션(BEI)", "fred_id": "T10YIE"},
            {"key": "US_REAL_RATE10", "label": "10년 실질금리(TIPS)", "fred_id": "DFII10"},
            {"key": "US_DOLLAR", "label": "달러인덱스 (무역가중, 2006년 1월=100 기준)", "fred_id": "DTWEXBGS"},
        ],
        "고용": [
            {"key": "US_UNRATE", "label": "실업률", "fred_id": "UNRATE"},
            {"key": "US_PAYEMS", "label": "비농업고용", "fred_id": "PAYEMS"},
            {"key": "US_ICSA", "label": "신규 실업수당 청구건수", "fred_id": "ICSA"},
        ],
        "성장·소비": [
            {"key": "US_GDPC1", "label": "실질 GDP", "fred_id": "GDPC1"},
            {"key": "US_GDP_NOMINAL", "label": "명목 GDP", "fred_id": "GDP"},
            {"key": "US_RSAFS", "label": "소매판매", "fred_id": "RSAFS"},
            {"key": "US_UMCSENT", "label": "소비자심리지수(미시간대)", "fred_id": "UMCSENT"},
            {"key": "US_HOUST", "label": "주택착공건수", "fred_id": "HOUST"},
        ],
        "통화": [
            {"key": "US_M2SL", "label": "M2 통화량", "fred_id": "M2SL"},
        ],
    },
    "KR": {
        "물가": [
            {
                "key": "KR_CPI",
                "label": "CPI (소비자물가지수)",
                "fred_id": "KORCPIALLMINMEI",
                "ecos": {"stat_code": "901Y009", "cycle": "M", "item_code": "0"},
            },
        ],
        "금리": [
            {"key": "KR_BASE_RATE", "label": "한국은행 기준금리", "fred_id": "IRSTCI01KRM156N"},
            {"key": "KR_DGS10", "label": "10년 국고채금리", "fred_id": "IRLTLT01KRM156N"},
        ],
        "고용": [
            {"key": "KR_UNRATE", "label": "실업률", "fred_id": "LRHUTTTTKRM156S"},
        ],
        "성장": [
            {"key": "KR_GDP", "label": "실질 GDP", "fred_id": "NGDPRSAXDCKRQ"},
            {"key": "KR_GDP_NOMINAL", "label": "명목 GDP", "fred_id": "NGDPSAXDCKRQ"},
            {"key": "KR_IP", "label": "산업생산지수", "fred_id": "KORPRINTO01GYSAM"},
        ],
        "통화·환율": [
            {
                "key": "KR_M2",
                "label": "M2 통화량",
                "fred_id": "MYAGM2KRM189N",
                "ecos": {"stat_code": "161Y005", "cycle": "M", "item_code": "BBHS00"},
            },
            {"key": "KR_FX_USD", "label": "원/달러 환율", "fred_id": "DEXKOUS"},
        ],
    },
}


def _find_indicator(key: str) -> dict:
    for categories in INDICATORS.values():
        for indicators in categories.values():
            for ind in indicators:
                if ind["key"] == key:
                    return ind
    raise MacroDataError(f"'{key}'는 등록되지 않은 지표입니다.")


def get_fred_series(series_id: str) -> pd.Series:
    """FRED 공개 CSV 엔드포인트에서 시계열을 가져온다 (API 키 불필요)."""
    try:
        df = pd.read_csv(FRED_CSV_URL.format(series_id=series_id))
    except Exception as e:
        raise MacroDataError(f"FRED 시리즈 '{series_id}' 조회에 실패했습니다: {e}") from e

    date_col, value_col = df.columns[0], df.columns[1]
    df[date_col] = pd.to_datetime(df[date_col])
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    series = df.set_index(date_col)[value_col].dropna()

    if series.empty:
        raise MacroDataError(f"FRED 시리즈 '{series_id}'에 유효한 데이터가 없습니다.")
    return series.sort_index()


def get_ecos_series(stat_code: str, cycle: str, item_code: str) -> pd.Series:
    """한국은행 ECOS에서 월별 시계열을 가져온다 (stat_code/item_code 조합은 호출부가 지정)."""
    api_key = os.environ.get("ECOS_API_KEY")
    if not api_key:
        raise MacroDataError("ECOS_API_KEY가 설정되어 있지 않습니다. .env 파일을 확인하세요.")

    url = ECOS_URL.format(
        api_key=api_key,
        stat_code=stat_code,
        cycle=cycle,
        start="196501",
        end=date.today().strftime("%Y%m"),
        item_code=item_code,
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        raise MacroDataError(f"ECOS 조회 중 오류가 발생했습니다: {e}") from e

    result = payload.get("StatisticSearch")
    if not result or "row" not in result:
        error_msg = payload.get("RESULT", {}).get("MESSAGE", "알 수 없는 오류")
        raise MacroDataError(f"ECOS 응답에 데이터가 없습니다: {error_msg}")

    rows = result["row"]
    index = pd.to_datetime([r["TIME"] + "01" for r in rows], format="%Y%m%d")
    values = pd.to_numeric([r["DATA_VALUE"] for r in rows], errors="coerce")
    series = pd.Series(values, index=index).dropna().sort_index()

    if series.empty:
        raise MacroDataError(f"ECOS 시리즈 '{stat_code}/{item_code}'에 유효한 데이터가 없습니다.")
    return series


def get_indicator_series(key: str) -> pd.Series:
    """카탈로그의 key로 지표 시계열을 조회하는 단일 진입점."""
    indicator = _find_indicator(key)

    ecos_params = indicator.get("ecos")
    if ecos_params:
        try:
            return get_ecos_series(**ecos_params)
        except MacroDataError:
            pass  # ECOS 실패 시 FRED로 폴백

    return get_fred_series(indicator["fred_id"])


def normalize_indexed(series: pd.Series, base: float = 100.0) -> pd.Series:
    """시계열을 첫 관측치를 base로 지수화한다 (예: "구간 시작=100 기준 상대 추이").

    단위가 서로 다른 지표(예: 실업률 %  vs M2 수십조 원)를 같은 차트에 겹쳐도 스케일이
    맞도록 오버레이 차트에서 쓴다. 절대 수준이 궁금하면 원계열(단일 지표 보기)을 봐야 한다.
    """
    if series.empty:
        raise MacroDataError("정규화할 시계열이 비어 있습니다.")
    first = series.iloc[0]
    if not first:
        raise MacroDataError("정규화 기준값(첫 관측치)이 0이라 지수화할 수 없습니다.")
    return series / first * base
