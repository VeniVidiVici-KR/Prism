"""주요 지수·금리·환율의 최근 시세를 한 번에 조회하는 "시장 스냅샷" 레이어.

⚠️ 이 모듈만 예외적으로 "자동 조회"를 전제로 설계됐다 — Stock Research 상단에서 페이지
진입 즉시 보여줘야 "열자마자 시장 상황 파악"이라는 목적이 성립한다(다른 모든 조회가 버튼
클릭 시에만인 것과 다름). 대신 호출부(views/_cache.py)가 짧은 ttl(300초=5분)로 캐싱해 호출
부담을 억제한다 — 매크로 지표의 86400과는 신선도 요구가 다르니 혼동하지 않는다.

⚠️ yf.download(다중 티커 OHLCV 배치)보다 yf.Tickers(...).fast_info를 택했다 — 실측 비교
결과, yf.download은 시장마다(한국/미국) 거래일이 달라(공휴일 등) 최근 두 행이 정렬되지
않을 수 있어 "전일 종가"를 안정적으로 뽑기 까다로웠던 반면(다른 시장의 휴장일이 섞이면
행 자체가 밀림), fast_info는 티커별로 lastPrice/previousClose를 야후 쪽에서 이미 정확히
대응시켜 반환해 날짜 정렬 문제 자체가 없다. 종목 개수(10개 내외)가 적어 배치 호출로 얻는
속도 이득도 크지 않았다(실측: yf.Tickers+fast_info 약 0.7~1초/티커 총 8초 내외,
yf.download 전체도 약 1.3초로 큰 차이 없음) — 안정성을 택했다.

⚠️ ^TNX 스케일은 실측 확인했다: fast_info.lastPrice가 곧바로 4.61 형태(수익률 %)로 온다
("×10"으로 온다는 속설과 달리 그대로임) — 별도 나눗셈 불필요.
"""

import yfinance as yf

# (표시명, yfinance 티커, 표기방식) — 표기방식: "pct"(등락률 %), "bp"(금리 변화, basis point).
# ⚠️ 금리(^TNX)에 등락률 %를 쓰면 4.62%→4.58%가 "-0.87%"로 보여 오해를 부른다 — bp로 고정.
MARKET_SNAPSHOT_TICKERS = [
    ("KOSPI", "^KS11", "pct"),
    ("KOSDAQ", "^KQ11", "pct"),
    ("S&P500", "^GSPC", "pct"),
    ("나스닥", "^IXIC", "pct"),
    ("다우존스", "^DJI", "pct"),
    ("VIX", "^VIX", "pct"),
    ("미국 10년물", "^TNX", "bp"),
    ("달러/원", "KRW=X", "pct"),
    ("달러인덱스", "DX-Y.NYB", "pct"),
    ("필라델피아 반도체", "^SOX", "pct"),
]


def get_market_snapshot() -> list[dict]:
    """등록된 모든 시장 지표를 조회해 [{"name","ticker","value","change","mode"}] 리스트로 반환한다.

    - mode="pct": change는 전일 대비 등락률(%, 예: +1.23).
    - mode="bp": change는 전일 대비 변화(basis point, 예: -1.6).

    ⚠️ 개별 티커가 실패해도 예외를 던지지 않고 그 항목만 건너뛴다(Comparison 페이지의
    종목별 실패 격리와 같은 정신) — 이건 부가 정보라 하나의 실패가 나머지를 가리면 안 된다.
    전부 실패하면 빈 리스트를 반환하니 호출부가 빈 리스트를 "표시할 게 없음"으로 처리한다.
    """
    tickers_str = " ".join(symbol for _, symbol, _ in MARKET_SNAPSHOT_TICKERS)
    batch = yf.Tickers(tickers_str)

    rows = []
    for name, symbol, mode in MARKET_SNAPSHOT_TICKERS:
        try:
            fi = batch.tickers[symbol].fast_info
            last = fi["lastPrice"]
            prev = fi["previousClose"]
        except Exception:
            continue
        if last is None or not prev:
            continue

        raw_change = last - prev
        change = (raw_change / prev * 100) if mode == "pct" else raw_change * 100
        rows.append({"name": name, "ticker": symbol, "value": last, "change": change, "mode": mode})
    return rows


def get_us_market_state() -> str | None:
    """대표 티커(^GSPC) 하나로만 미국장 상태(PRE/REGULAR/POST/CLOSED 등)를 확인한다.

    ⚠️ 10개 티커 전부에 .info를 부르면 fast_info보다 훨씬 무거운 호출이 10배로 늘어난다 —
    대표 티커 하나로만 확인한다(한국장 상태는 이 함수로 알 수 없다 — 비용 대비 실익이 낮다고
    판단했다. 화면에서 반드시 "S&P500(미국장) 기준"임을 밝혀야 한다).
    실패(네트워크 오류 등)해도 None을 반환할 뿐 예외를 던지지 않는다 — 이 정보 없이도
    시세 표는 정상 표시돼야 한다.
    """
    try:
        info = yf.Ticker("^GSPC").info
        return info.get("marketState")
    except Exception:
        return None
