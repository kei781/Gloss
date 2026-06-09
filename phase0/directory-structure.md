# Phase 0 디렉토리 구조 설계

Phase 0은 본 구현을 시작하기 전 NPU 검증, 모델 교체 계약, 로그/설정 계약을 고정하는 단계다. 이후 Phase 1부터 앱 코드가 생겨도 이 구조를 기준으로 확장한다.

## 현재 Phase 0 구조

```text
Gloss/
├─ ADR.md
├─ PRD.md
├─ phase0/
│  ├─ .env.example
│  ├─ config.example.json
│  ├─ directory-structure.md
│  ├─ model-profiles.json
│  ├─ verification-note-template.md
│  ├─ runs/                         # ignored, 측정 산출물
│  └─ verification-notes/
│     └─ 2026-06-09-real-device.md
└─ scripts/
   └─ phase0/
      ├─ common.ps1
      ├─ collect_windows_env.ps1
      ├─ measure_openai_backend.py
      ├─ phase0_common.py
      └─ run_model_profile.ps1
```

## 확장 목표 구조

```text
Gloss/
├─ docs/                             # PRD/ADR 외 사용자 문서, 스크린샷 가이드
├─ phase0/                           # NPU 검증 게이트 산출물
├─ scripts/
│  ├─ phase0/                        # 검증/설치/측정 helper
│  └─ dev/                           # 포맷, 테스트, 패키징 helper
├─ src/
│  └─ gloss/
│     ├─ app/                        # PyQt6 앱 부트스트랩
│     ├─ config/                     # env/profile/runtime config loader
│     ├─ logging/                    # 제품용 log() 구현체
│     ├─ backend/                    # OpenAI 호환 backend client/process manager
│     ├─ models/                     # 모델 셀렉터와 profile 해석
│     ├─ engines/
│     │  ├─ text/                    # URL/PDF/native text 번역
│     │  └─ visual/                  # WGC 캡처, VLM, OCR fallback
│     ├─ overlay/                    # click-through overlay
│     └─ dashboard/                  # metrics, CPU/RAM/NPU evidence view
└─ tests/
   ├─ unit/
   └─ fixtures/
```

## 구조 원칙

- 로그는 각 런타임의 `log()` 함수만 통해 출력한다. Python은 `scripts/phase0/phase0_common.py`, PowerShell은 `scripts/phase0/common.ps1`이 Phase 0 로그의 단일 진입점이다.
- 주요 key, endpoint, profile, 모델명 override, 로컬 경로는 env에서 우선 관리한다. 실제 `phase0/.env`는 git에 올리지 않고, `phase0/.env.example`만 커밋한다.
- 모델 교체는 `phase0/model-profiles.json`의 profile 추가/수정과 `GLOSS_PHASE0_ACTIVE_MODEL_PROFILE` 변경으로 처리한다.
- 측정 산출물은 `phase0/runs/` 아래에 남기고 git에는 올리지 않는다.
- 실기 결과는 `phase0/verification-notes/`에 날짜별로 남긴다.

## 이번 수정에 포함된 내용

- `phase0/.env.example` 추가: 주요 key, base URL, active profile, 모델명 override, npurun/QNN/model dir, 측정 파라미터 관리.
- `scripts/phase0/phase0_common.py` 추가: Python용 `log()`, `.env` loader, env lookup.
- `scripts/phase0/common.ps1` 추가: PowerShell용 `log()`, `.env` loader, env lookup, path resolver.
- `scripts/phase0/measure_openai_backend.py` 수정: 모든 출력이 `log()`를 통과하고, CLI > env > profile/config 순서로 값을 결정.
- `scripts/phase0/run_model_profile.ps1` 수정: 모든 출력이 `log()`를 통과하고, active profile/model/path를 env로 override 가능.
- `scripts/phase0/collect_windows_env.ps1` 수정: 모든 출력이 `log()`를 통과하고, output dir/sample seconds를 env로 override 가능.
- `phase0/config.example.json` 수정: `env_file`을 명시하고 profile 중심 설정으로 유지.
