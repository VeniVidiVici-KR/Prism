"""Macro Indicator 페이지의 섹션별 렌더 모듈 (파일 분할 라운드, 2026-08).

views/macro_indicator.py가 조립만 담당하고, 실제 섹션 렌더링은 이 패키지 아래 파일별로
나뉜다. 각 섹션 모듈은 render(country_code: str) -> None 하나를 공개 진입점으로 둔다.
"""
