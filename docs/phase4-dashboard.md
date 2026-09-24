# Phase 4 Dashboard

Phase 4는 Phase 1부터 심어 둔 호출 경로 계측(메트릭 JSONL)을 가시화한다 (FR-D1~FR-D3, FR-D6). CLI 대시보드(`gloss-dashboard`)와 PyQt6 패널(`gloss-dashboard-gui`)이 같은 집계 모듈을 사용한다.

## 설치

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip install -e '.[gui]'
```

GUI 패널은 백엔드 상태, CPU/RAM, 지정된 NPU LUID의 compute 사용률, 요청 집계와 최근 디코드 속도 그래프를 갱신한다. GUI 갱신·프로브는 백그라운드 작업으로 수행한다.

```powershell
.\.venv\Scripts\gloss-dashboard-gui.exe --npu-luid 0x11b60
```

## 요약 모드 (`--once`)

phase 1~3 메트릭 파일을 집계해 한 번 출력하고 종료한다.

```powershell
.\.venv\Scripts\gloss-dashboard.exe --once
```

출력 내용:

- **backend** (FR-D1): `GET /v3/models` 프로브 — 가동 여부와 적재 모델명. 기본 주소는 `http://127.0.0.1:8000/v3`.
- **system** (FR-D3): CPU% / RAM. psutil이 아니라 **Win32 API 직접 호출**(ctypes, ADR-017)이라 외부 휠 의존이 없다. CPU 유휴는 NPU 가동의 **보조 증거**로만 표시된다 (ADR-009).
- **엔진/Phase별 그룹** (FR-D2): 요청수, truncated 수, 토큰 in/out, TTFT·디코드 tok/s·e2e tok/s·레이턴시의 avg/p50/p95, `token_count_source` 분포(usage 실측 vs char 추정 구분), 마지막 기록 시각.

기본 대상 파일: `runs/phase1/text-metrics.jsonl`, `runs/phase2/visual-metrics.jsonl`, `runs/phase3/watch-metrics.jsonl`. `--metrics <path>` 반복 지정으로 변경.

## 라이브 모드

메트릭 파일을 tail 하면서 1초 간격(기본)으로 CPU/RAM을 샘플링한다. 모든 라인은 `log()` 계약(ADR-015)대로 stderr로 나간다.

```powershell
.\.venv\Scripts\gloss-dashboard.exe --interval 1 --cpu-threshold 65
```

- 새 번역 요청이 기록되면 `request completed` 라인으로 디코드 tok/s, TTFT, 토큰, truncated 여부, **생성 구간 평균 CPU%** 를 보여준다.
- **FR-D6 silent CPU fallback 휴리스틱**: NPU 카운터가 지정되지 않았거나 생성 구간 샘플이 부족할 때, 평균 CPU%가 임계값(기본 65%) 이상이면 `possible silent CPU fallback` WARN을 띄운다. 이는 ADR-009의 보조 증거이며 `dry_run`은 제외한다.
- `--npu-luid 0x11b60`(또는 `GLOSS_NPU_LUID`)을 지정하면 Windows GPU Engine의 해당 LUID `engtype_compute` 카운터를 샘플링한다. 지정한 LUID에서 생성 구간에 2개 이상 샘플이 있고 평균이 1% 미만이면 NPU 미사용 경고를 낸다. 카운터가 없으면 NPU 값은 생략하고 CPU 휴리스틱을 사용한다. `0x11b60`은 2026-06-20 실기 검증 당시의 예시이므로 현재 OpenVINO NPU plugin 로그의 `DEVICE_LUID`를 확인해 값을 지정한다. 단일 `--once` 샘플은 추론 중 NPU 사용 증명이 아니다.
- `--probe-every`(기본 30초) 간격으로 백엔드 상태 라인을 남긴다.
- 종료는 Ctrl+C (또는 테스트용 `--max-ticks N`).

## 동작 구조

```
metrics JSONL (phase 1/2/3) ──tail──┐
                                    ├─ LiveDashboard ── log() (stderr)
GetSystemTimes/GlobalMemoryStatusEx ┘      │
        (1s 샘플 → ring buffer)            └─ 요청 elapsed 구간의 평균 CPU%로 FR-D6 판정
```

- JSONL tail은 **완성된 줄만** 소비한다(쓰는 중인 줄은 다음 틱에).
- 라이브 모드는 **시작 이후 추가된 행만** 보여준다(tail -f). 과거 누적은 `--once`로 본다. 파일 삭제·재생성은 파일 identity로 감지해 처음부터 다시 읽는다.
- CPU%는 연속 두 샘플 간 `GetSystemTimes` 델타로 계산 — 첫 샘플은 n/a.

## 한계

- FR-D6 CPU 휴리스틱은 시스템 전역 CPU%라 다른 프로그램(게임 등)의 부하와 구분하지 못한다. 게임 중에는 NPU 카운터와 백엔드 로그를 함께 본다.
- GPU Engine 카운터가 현 장비에 없거나 LUID가 달라지면 NPU 사용률은 표시되지 않는다. 백엔드 로그와 동시 부하 샘플로 다시 대조한다.
- 대시보드는 메트릭 파일 기반이라 요청 진행 중이 아닌 **완료 시점**에 행이 보인다.
- `--once`의 합계는 파일 전체 누적이다. 세션별로 보려면 메트릭 파일을 분리하라.
