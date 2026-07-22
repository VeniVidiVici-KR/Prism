"""계산된 재무/밸류에이션 결과를 OpenAI 호환 API로 자연어 해설한다."""

import pandas as pd
from openai import RateLimitError

from valley.formatting import format_money
from valley.llm import get_llm_client

SYSTEM_PROMPT = """당신은 개인 투자자를 위한 리서치 보조 도구입니다.
아래에 주어진 재무 데이터와 DCF 밸류에이션 계산 결과를 바탕으로 한국어로 해설을 작성하세요.

작성 규칙:
1. 매출/이익 추이가 개선되는지 악화되는지 간결하게 짚어주세요.
2. DCF 내재가치와 현재 주가를 비교해 설명하되, 성장률/요구수익률 가정이 바뀌면 결과가 크게
   달라질 수 있다는 점을 반드시 언급하세요.
3. 절대 "사세요"/"파세요" 같은 매매 추천 문구를 쓰지 마세요. 이 도구는 정보 제공 목적이며
   투자자문업이 아닙니다.
4. 마지막 줄에 반드시 "본 내용은 정보 제공 목적이며 투자 판단과 책임은 본인에게 있습니다."
   문구를 포함하세요.
5. 5~8문장 이내로 간결하게 작성하세요.
"""


def _build_user_prompt(snapshot: dict, financial_history: pd.DataFrame, dcf_result: dict) -> str:
    currency = snapshot["currency"]
    history_lines = []
    for year, row in financial_history.iterrows():
        history_lines.append(
            f"- {year}: 매출 {format_money(row['매출'], currency)}, 순이익 {format_money(row['순이익'], currency)}, "
            f"순이익률 {row['순이익률'] * 100:.1f}%"
        )

    assumptions = dcf_result["assumptions"]
    growth_initial = assumptions["growth_rate_initial"]
    growth_terminal = assumptions["growth_rate_terminal"]
    if growth_initial == growth_terminal:
        growth_desc = f"성장률 {growth_initial * 100:.1f}%"
    else:
        growth_desc = f"성장률 {growth_initial * 100:.1f}%(초기) → {growth_terminal * 100:.1f}%(말기, 선형 감쇠)"

    return f"""[회사 정보]
이름: {snapshot['name']} ({snapshot['ticker']})
현재가: {format_money(snapshot['price'], currency)}
섹터: {snapshot['sector']}

[최근 재무 추이]
{chr(10).join(history_lines)}

[DCF 밸류에이션]
가정: {growth_desc}, 요구수익률 {assumptions['discount_rate'] * 100:.1f}%, \
영구성장률 {assumptions['terminal_growth'] * 100:.1f}%, 투영기간 {assumptions['years']}년
주당 내재가치: {format_money(dcf_result['intrinsic_value_per_share'], currency)}
현재 주가: {format_money(snapshot['price'], currency)}
"""


def generate_commentary(
    snapshot: dict, financial_history: pd.DataFrame, dcf_result: dict, engine: str | None = None
) -> str:
    """OpenAI 호환 Chat Completions를 호출해 자연어 해설 문자열을 반환한다.

    Args:
        engine: valley.llm의 ENGINE_SCHOOL|ENGINE_GEMINI. None(기본값)이면 기존과 동일하게
            학교 게이트웨이를 쓴다(하위호환) — 호출부가 engine을 안 넘겨도 동작이 안 바뀐다.

    Raises:
        RuntimeError: API 키 미설정(LlmConfigError, RuntimeError의 하위클래스), 요청 한도
            초과, 그 외 호출 실패.
    """
    client, model, engine_label = get_llm_client(engine)
    user_prompt = _build_user_prompt(snapshot, financial_history, dcf_result)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
        )
    except RateLimitError as e:
        raise RuntimeError(f"{engine_label} API 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.") from e
    except Exception as e:
        raise RuntimeError(f"AI 해설 생성 중 오류가 발생했습니다: {e}") from e

    return response.choices[0].message.content
