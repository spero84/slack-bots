"""Gov Funding Monitor - 정부 지원사업 공고 모니터링

엔트리포인트는 src.gov_funding.main (python -m src.gov_funding.main).
패키지 import 시 main을 불러오지 않는다 — 스케줄러/모든 크롤러를 즉시 import하면
하위 모듈 하나의 import 오류가 패키지 전체(모든 테스트)를 깨뜨린다.
"""
