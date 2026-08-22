"""Stock Research 페이지 상단부의 섹션별 렌더 모듈 (파일 분할 라운드, 2026-08).

views/stock_research.py가 사이드바·게이팅(st.stop())·조립을 담당하고, 상단부(시장 스냅샷~
PER/PBR 밴드) 섹션 렌더링은 이 패키지 아래 파일별로 나뉜다. 하단부(간단 DCF 이하)는 아직
분할하지 않고 stock_research.py에 그대로 남아 있다(다음 라운드 범위).
"""
