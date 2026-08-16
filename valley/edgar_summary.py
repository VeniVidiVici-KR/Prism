"""SEC EDGAR 공시 원문을 OpenAI 호환 API로 한국어 요약한다.

valley.llm의 공통 엔진 헬퍼(학교 게이트웨이/Gemini)를 재사용한다.
- summarize_8k_filing: 8-K(수시공시) 전문을 그대로 요약(짧아서 통째로 가능).
- summarize_mda_section: 10-K/10-Q는 수백 페이지라 통째로 넣을 수 없어, MD&A 섹션만 추출한
  텍스트(valley.edgar.get_mda_section_text 결과)를 요약한다 — 8-K 경로와 완전히 분리된
  별도 함수·프롬프트다(기존 8-K 요약 동작에는 영향 없음).
- summarize_424b_filing: 424B(발행설명서) 앞부분(valley.edgar.get_424b_filing_text 결과)을
  요약한다. 문서 성격이 8-K/MD&A와 전혀 달라(수시공시/실적서술이 아니라 채권 발행조건
  명세) 프롬프트가 발행금액·쿠폰·만기·트랜치·자금사용목적 추출에 특화돼 있다 — 역시 완전히
  분리된 별도 함수다.
"""

from openai import RateLimitError

from valley.llm import get_llm_client

SYSTEM_PROMPT = """당신은 개인 투자자를 위한 SEC 공시 리서치 보조 도구입니다.
아래에 주어진 미국 상장기업의 8-K(수시공시) 원문을 한국어로 요약하세요.

작성 규칙:
1. 문서에 실제로 있는 내용만 쓰세요 — 없는 내용을 지어내지(할루시네이션) 마세요.
2. 미국 공시 특유의 용어는 필요하면 원어를 함께 적으세요(예: "실적 발표(Item 2.02)").
3. 다음 구조로 작성하세요:
   ① 한 줄 요약(무슨 일이 있었는지)
   ② 핵심 내용 3~5개 불릿
   ③ 문서에 구체적인 수치가 있으면 원문 그대로 인용하세요.
4. 절대 "사세요"/"파세요" 같은 매매 추천 문구를 쓰지 마세요. 이 도구는 정보 제공 목적이며
   투자자문업이 아닙니다.
5. 마지막 줄에 반드시 "본 요약은 정보 제공 목적이며, 정확한 내용은 원문을 확인하세요." 문구를
   포함하세요.
6. 전체 10문장 이내로 간결하게 작성하세요.
"""


class EdgarSummaryError(Exception):
    """8-K 요약 생성에 실패했을 때 발생 (API 키 없음/호출 실패/빈 응답 등)."""


def _build_user_prompt(company_name: str, form: str, filing_date: str, text: str, truncated: bool) -> str:
    truncated_note = (
        "\n\n[안내: 문서가 길어 앞부분만 발췌했습니다. 이 발췌 이후 내용은 요약에 포함되지 않았습니다.]"
        if truncated
        else ""
    )
    return f"""[공시 정보]
회사: {company_name}
유형: {form}
접수일: {filing_date}

[원문 텍스트]
{text}{truncated_note}
"""


def summarize_8k_filing(
    company_name: str, form: str, filing_date: str, text: str, truncated: bool, engine: str | None = None
) -> str:
    """8-K 원문 텍스트(get_8k_filing_text 결과)를 받아 한국어 요약 문자열을 반환한다.

    Args:
        engine: valley.llm의 ENGINE_SCHOOL|ENGINE_GEMINI. None(기본값)이면 기존과 동일하게
            학교 게이트웨이를 쓴다(하위호환).

    Raises:
        EdgarSummaryError: API 키 없음, 요청 한도 초과, 호출 실패, 빈 응답 중 하나.
    """
    try:
        client, model, engine_label = get_llm_client(engine)
    except Exception as e:
        raise EdgarSummaryError(str(e)) from e

    user_prompt = _build_user_prompt(company_name, form, filing_date, text, truncated)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )
    except RateLimitError as e:
        raise EdgarSummaryError(f"{engine_label} API 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.") from e
    except Exception as e:
        raise EdgarSummaryError(f"AI 요약 생성 중 오류가 발생했습니다: {e}") from e

    content = response.choices[0].message.content
    if not content or not content.strip():
        raise EdgarSummaryError("AI가 빈 응답을 반환했습니다.")
    return content


FORM_424B_SYSTEM_PROMPT = """당신은 개인 투자자를 위한 SEC 공시 리서치 보조 도구입니다.
아래에 주어진 미국 상장기업의 424B(발행설명서/프로스펙터스 서플리먼트) 앞부분 원문을
한국어로 요약하세요. 이 문서는 회사채 등 신규 증권 발행의 조건을 담고 있습니다.

작성 규칙:
1. 문서에 실제로 있는 내용만 쓰세요 — 없는 내용을 지어내지(할루시네이션) 마세요. 특정 조건이
   원문에 명시되어 있지 않으면 "문서에 없음"이라고 쓰세요(추정하거나 채우지 마세요).
2. 다음 구조로 작성하세요:
   ① 한 줄 요약(어떤 증권을 얼마나 발행하는지)
   ② 발행 조건 — 다음 항목을 원문에 있는 그대로(숫자·단위 포함) 정리하세요:
      - 발행금액(트랜치가 여러 개면 트랜치별로 각각)
      - 쿠폰(이자율, 고정/변동 여부)
      - 만기일 / 발행일
      - 트랜치 구조(만기별로 나눠 발행하는 경우 각 트랜치를 구분해서)
      - 상환(콜) 조건이 언급되어 있다면 간단히
   ③ 자금 사용 목적(use of proceeds) — 원문에 명시된 용도를 그대로 옮기세요.
3. 절대 "사세요"/"파세요" 같은 매매 추천 문구를 쓰지 마세요. 이 도구는 정보 제공 목적이며
   투자자문업이 아닙니다.
4. 마지막 줄에 반드시 "본 요약은 정보 제공 목적이며, 정확한 내용은 원문을 확인하세요." 문구를
   포함하세요.
5. 전체 12문장 이내로 간결하게 작성하세요.
"""


def _build_424b_user_prompt(company_name: str, form: str, filing_date: str, text: str, truncated: bool) -> str:
    truncated_note = (
        "\n\n[안내: 문서가 길어 앞부분만 발췌했습니다(발행조건은 보통 문서 서두의 표지에 "
        "있으나, 이 발췌 이후 내용은 요약에 포함되지 않았습니다).]"
        if truncated
        else ""
    )
    return f"""[공시 정보]
회사: {company_name}
유형: {form}
접수일: {filing_date}

[원문 텍스트(앞부분 발췌)]
{text}{truncated_note}
"""


def summarize_424b_filing(
    company_name: str, form: str, filing_date: str, text: str, truncated: bool, engine: str | None = None
) -> str:
    """424B 원문 텍스트(get_424b_filing_text 결과)를 받아 한국어 요약 문자열을 반환한다.

    summarize_8k_filing/summarize_mda_section과 별도 함수다 — 문서 성격(발행조건 명세)과
    프롬프트가 완전히 달라 기존 두 요약 경로를 전혀 건드리지 않기 위해 의도적으로 코드를
    공유하지 않는다.

    Args:
        engine: valley.llm의 ENGINE_SCHOOL|ENGINE_GEMINI. None(기본값)이면 학교 게이트웨이.

    Raises:
        EdgarSummaryError: API 키 없음, 요청 한도 초과, 호출 실패, 빈 응답 중 하나.
    """
    try:
        client, model, engine_label = get_llm_client(engine)
    except Exception as e:
        raise EdgarSummaryError(str(e)) from e

    user_prompt = _build_424b_user_prompt(company_name, form, filing_date, text, truncated)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": FORM_424B_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )
    except RateLimitError as e:
        raise EdgarSummaryError(f"{engine_label} API 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.") from e
    except Exception as e:
        raise EdgarSummaryError(f"AI 요약 생성 중 오류가 발생했습니다: {e}") from e

    content = response.choices[0].message.content
    if not content or not content.strip():
        raise EdgarSummaryError("AI가 빈 응답을 반환했습니다.")
    return content


MDA_SYSTEM_PROMPT = """당신은 개인 투자자를 위한 SEC 공시 리서치 보조 도구입니다.
아래에 주어진 미국 상장기업의 10-K(연간보고서) 또는 10-Q(분기보고서) 중 MD&A(경영진의 논의
및 분석) 섹션 원문을 한국어로 요약하세요.

작성 규칙:
1. 문서에 실제로 있는 내용만 쓰세요 — 없는 내용을 지어내지(할루시네이션) 마세요.
2. 다음 구조로 작성하세요:
   ① 실적 요약(매출·이익 등 핵심 지표가 늘었는지 줄었는지)
   ② 경영진이 밝힌 증감 원인
   ③ 향후 전망·리스크로 언급된 내용
   ④ 문서에 구체적인 수치가 있으면 원문 그대로 인용하세요.
3. 절대 "사세요"/"파세요" 같은 매매 추천 문구를 쓰지 마세요. 이 도구는 정보 제공 목적이며
   투자자문업이 아닙니다.
4. 마지막 줄에 반드시 "본 요약은 정보 제공 목적이며, 정확한 내용은 원문을 확인하세요." 문구를
   포함하세요.
5. MD&A는 8-K보다 다룰 내용이 많으니 전체 12문장 이내로 작성하세요(8-K보다 소폭 허용).
"""


def _build_mda_user_prompt(company_name: str, form: str, filing_date: str, text: str, truncated: bool) -> str:
    truncated_note = (
        "\n\n[안내: MD&A 섹션 자체가 길어 앞부분만 발췌했습니다. 이 발췌 이후 내용은 요약에 포함되지 않았습니다.]"
        if truncated
        else ""
    )
    return f"""[공시 정보]
회사: {company_name}
유형: {form}
접수일: {filing_date}
섹션: MD&A(경영진의 논의 및 분석)

[MD&A 원문 텍스트]
{text}{truncated_note}
"""


def summarize_mda_section(
    company_name: str, form: str, filing_date: str, text: str, truncated: bool, engine: str | None = None
) -> str:
    """10-K/10-Q의 MD&A 섹션 텍스트(get_mda_section_text 결과)를 받아 한국어 요약 문자열을 반환한다.

    summarize_8k_filing과 별도 함수다 — 프롬프트·용도가 다르고(전체 요약 vs 섹션 요약),
    기존 8-K 요약 경로를 전혀 건드리지 않기 위해 의도적으로 코드를 공유하지 않는다.

    Args:
        engine: valley.llm의 ENGINE_SCHOOL|ENGINE_GEMINI. None(기본값)이면 학교 게이트웨이.

    Raises:
        EdgarSummaryError: API 키 없음, 요청 한도 초과, 호출 실패, 빈 응답 중 하나.
    """
    try:
        client, model, engine_label = get_llm_client(engine)
    except Exception as e:
        raise EdgarSummaryError(str(e)) from e

    user_prompt = _build_mda_user_prompt(company_name, form, filing_date, text, truncated)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": MDA_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )
    except RateLimitError as e:
        raise EdgarSummaryError(f"{engine_label} API 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.") from e
    except Exception as e:
        raise EdgarSummaryError(f"AI 요약 생성 중 오류가 발생했습니다: {e}") from e

    content = response.choices[0].message.content
    if not content or not content.strip():
        raise EdgarSummaryError("AI가 빈 응답을 반환했습니다.")
    return content
