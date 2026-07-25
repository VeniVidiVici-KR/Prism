"""yfinance longBusinessSummary(영문 사업 서술)를 OpenAI 호환 API로 한국어 요약한다.

valley.llm의 공통 엔진 헬퍼(학교 게이트웨이/Gemini)를 재사용한다. valley.edgar_summary와
같은 패턴(프롬프트/함수를 별도로 두어 서로 다른 요약 기능이 섞이지 않게 함)이지만, 이 모듈은
공시 원문이 아니라 "회사가 무엇을 하는 회사인지"에 대한 서술만 다룬다 — 재무 수치나 실적
전망은 원문(longBusinessSummary)에 애초에 없으므로 요약도 그 범위를 넘지 않는다.
"""

from openai import RateLimitError

from valley.llm import get_llm_client

SYSTEM_PROMPT = """당신은 개인 투자자를 위한 기업 리서치 보조 도구입니다.
아래에 주어진 미국 상장기업의 영문 사업 개요(longBusinessSummary)를 한국어로 요약하세요.

작성 규칙:
1. 문서에 실제로 있는 내용만 쓰세요 — 없는 내용을 지어내지(할루시네이션) 마세요.
2. 이 원문은 사업 서술일 뿐 재무 정보가 아닙니다 — 매출/이익/실적·향후 전망을 추측해서 넣지
   마세요.
3. 다음 구조로 작성하세요:
   ① 이 회사가 무엇을 하는 곳인지 한 줄 요약
   ② 주요 사업/제품/서비스 3~5개 불릿
4. 절대 "사세요"/"파세요" 같은 매매 추천 문구를 쓰지 마세요. 이 도구는 정보 제공 목적이며
   투자자문업이 아닙니다.
5. 마지막 줄에 반드시 "본 요약은 정보 제공 목적이며, 정확한 내용은 원문을 확인하세요." 문구를
   포함하세요.
6. 전체 8문장 이내로 간결하게 작성하세요.
"""


class CompanySummaryError(Exception):
    """회사 개요 요약 생성에 실패했을 때 발생 (API 키 없음/호출 실패/빈 응답 등)."""


def _build_user_prompt(company_name: str, business_summary: str) -> str:
    return f"""[회사명]
{company_name}

[영문 사업 개요 원문]
{business_summary}
"""


def summarize_company_overview(company_name: str, business_summary: str, engine: str | None = None) -> str:
    """longBusinessSummary(영문)를 받아 한국어 요약 문자열을 반환한다.

    Args:
        engine: valley.llm의 ENGINE_SCHOOL|ENGINE_GEMINI. None(기본값)이면 학교 게이트웨이.

    Raises:
        CompanySummaryError: API 키 없음, 요청 한도 초과, 호출 실패, 빈 응답 중 하나.
    """
    try:
        client, model, engine_label = get_llm_client(engine)
    except Exception as e:
        raise CompanySummaryError(str(e)) from e

    user_prompt = _build_user_prompt(company_name, business_summary)

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
        raise CompanySummaryError(f"{engine_label} API 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.") from e
    except Exception as e:
        raise CompanySummaryError(f"AI 요약 생성 중 오류가 발생했습니다: {e}") from e

    content = response.choices[0].message.content
    if not content or not content.strip():
        raise CompanySummaryError("AI가 빈 응답을 반환했습니다.")
    return content
