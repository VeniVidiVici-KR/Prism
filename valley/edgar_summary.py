"""SEC EDGAR 8-K 공시 원문을 OpenAI 호환 API로 한국어 요약한다.

ai_commentary.py와 동일한 OpenAI 클라이언트 패턴(OPENAI_API_KEY/OPENAI_BASE_URL/OPENAI_MODEL)을
재사용한다. 이번 라운드는 8-K만 다룬다 — 10-K/10-Q는 길이·비용 설계가 달라 다음 라운드.
"""

import os

from openai import OpenAI

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


def summarize_8k_filing(company_name: str, form: str, filing_date: str, text: str, truncated: bool) -> str:
    """8-K 원문 텍스트(get_8k_filing_text 결과)를 받아 한국어 요약 문자열을 반환한다.

    Raises:
        EdgarSummaryError: API 키 없음, 호출 실패, 빈 응답 중 하나.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EdgarSummaryError("OPENAI_API_KEY가 설정되어 있지 않습니다. .env 파일을 확인하세요.")

    base_url = os.environ.get("OPENAI_BASE_URL") or None
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    client = OpenAI(api_key=api_key, base_url=base_url)
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
    except Exception as e:
        raise EdgarSummaryError(f"AI 요약 생성 중 오류가 발생했습니다: {e}") from e

    content = response.choices[0].message.content
    if not content or not content.strip():
        raise EdgarSummaryError("AI가 빈 응답을 반환했습니다.")
    return content
