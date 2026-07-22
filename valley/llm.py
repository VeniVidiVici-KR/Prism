"""여러 LLM 엔진(학교 게이트웨이 / Gemini) 중 하나를 선택해 OpenAI 호환 클라이언트를 만드는
공통 헬퍼. ai_commentary.py(AI 해설)와 edgar_summary.py(8-K 요약) 둘 다 이 모듈을 통해
클라이언트를 얻는다.

⚠️ 자동 폴백이 없다 — 사용자가 명시적으로 고른 engine만 쓴다. 학교 크레딧이 떨어졌다고
조용히 Gemini로 바뀌는 동작은 절대 만들지 않는다(두 엔진은 응답 스타일이 달라 사용자가
어느 결과를 보고 있는지 혼란스러워지고, 실패가 숨겨진다).

engine을 안 넘기면(None) 기존 동작(학교 게이트웨이, OPENAI_* 환경변수)이 그대로 유지된다
— 기존 호출부(ai_commentary.py)가 이 모듈 도입 전과 동일하게 계속 동작한다(하위호환).
"""

import os

from openai import OpenAI

ENGINE_SCHOOL = "school"
ENGINE_GEMINI = "gemini"
DEFAULT_ENGINE = ENGINE_SCHOOL

ENGINE_LABELS = {
    ENGINE_SCHOOL: "학교 게이트웨이",
    ENGINE_GEMINI: "Gemini",
}

# ⚠️ 실측 확인(2026-07-22, 실제 GEMINI_API_KEY로 호출): Gemini는 OpenAI 호환 엔드포인트를
# 제공하며, 기존 OpenAI 클라이언트 코드를 그대로 재사용해 정상 응답을 받았다(추측 아님).
# 모델명은 실측으로 좁혔다 — "gemini-2.0-flash"는 이 프로젝트 키의 무료 티어 쿼터가 0이라
# HTTP 429(RESOURCE_EXHAUSTED)로 거부됐고, "gemini-2.5-flash"는 "신규 사용자 지원 종료"로
# HTTP 404가 났다. "gemini-flash-latest"(항상 최신 flash 모델을 가리키는 Google 공식 별칭)만
# 정상 응답했고, 연속 6회 요청도 문제없이 처리돼 기본 모델로 확정했다(이 앱은 버튼 클릭
# 건별 호출이라 RPM 제한에 걸릴 부담이 낮다).
_GEMINI_BASE_URL_DEFAULT = "https://generativelanguage.googleapis.com/v1beta/openai/"
_GEMINI_MODEL_DEFAULT = "gemini-flash-latest"


class LlmConfigError(RuntimeError):
    """선택한 엔진의 API 키가 없거나 engine 값을 알 수 없을 때 발생.

    RuntimeError를 상속한다 — ai_commentary.py의 기존 호출부가 이미 `except RuntimeError`로
    "API 키 없음"을 잡고 있었는데, 이 모듈 도입으로 그 예외처리가 깨지지 않게 하기 위함이다.
    """


def is_engine_available(engine: str) -> bool:
    """해당 엔진의 API 키가 .env에 설정돼 있는지만 확인한다.

    ⚠️ 키 값 자체는 절대 반환하지 않는다 — True/False만. UI가 선택지를 미리 비활성화할지
    판단하는 용도.
    """
    if engine == ENGINE_SCHOOL:
        return bool(os.environ.get("OPENAI_API_KEY"))
    if engine == ENGINE_GEMINI:
        return bool(os.environ.get("GEMINI_API_KEY"))
    return False


def get_llm_client(engine: str | None = None) -> tuple[OpenAI, str, str]:
    """engine에 맞는 (OpenAI 클라이언트, 모델명, 엔진 한글 라벨)을 반환한다.

    Args:
        engine: ENGINE_SCHOOL|ENGINE_GEMINI. None(기본값)이면 ENGINE_SCHOOL과 동일하게
            동작한다(기존 코드가 이 인자 없이 호출해도 학교 게이트웨이를 그대로 씀).

    Raises:
        LlmConfigError: 해당 엔진의 API 키가 없거나, engine이 알 수 없는 값일 때.
    """
    engine = engine or DEFAULT_ENGINE

    if engine == ENGINE_SCHOOL:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise LlmConfigError("OPENAI_API_KEY가 설정되어 있지 않습니다. .env 파일을 확인하세요.")
        base_url = os.environ.get("OPENAI_BASE_URL") or None
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        return OpenAI(api_key=api_key, base_url=base_url), model, ENGINE_LABELS[ENGINE_SCHOOL]

    if engine == ENGINE_GEMINI:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise LlmConfigError("GEMINI_API_KEY가 설정되어 있지 않습니다. .env 파일을 확인하세요.")
        base_url = os.environ.get("GEMINI_BASE_URL") or _GEMINI_BASE_URL_DEFAULT
        model = os.environ.get("GEMINI_MODEL") or _GEMINI_MODEL_DEFAULT
        return OpenAI(api_key=api_key, base_url=base_url), model, ENGINE_LABELS[ENGINE_GEMINI]

    raise LlmConfigError(f"알 수 없는 AI 엔진입니다: {engine}")
