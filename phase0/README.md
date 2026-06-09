# Phase 0 NPU 검증 가이드

Phase 0의 목적은 Gloss 본 구현 전에 Snapdragon X Plus 장비에서 후보 백엔드와 모델이 실제로 NPU에 적재되고 실행되는지 확인하는 것이다. 이 단계는 기능 구현이 아니라 **게이트 검증**이다.

## 산출물

- `phase0/verification-note-template.md`: 검증 결과를 남기는 노트 템플릿
- `phase0/config.example.json`: 측정 스크립트 입력 예시
- `scripts/phase0/collect_windows_env.ps1`: Windows/장치/NPU counter 후보 수집
- `scripts/phase0/measure_openai_backend.py`: OpenAI 호환 백엔드의 TTFT/tok/s 측정

## 검증 순서

1. 후보 백엔드를 실행한다.
   - 1순위: npurun(Genie)
   - 2순위: NexaSDK
   - 3순위: ONNX Runtime + QNN EP
2. Windows 환경과 NPU counter 후보를 수집한다.
3. 텍스트 모델에 짧은 번역 요청을 3회 이상 보낸다.
4. VLM 모델에 이미지 입력 요청을 3회 이상 보내 vision encode 경로를 확인한다.
5. 작업 관리자, PDH counter, Genie/QNN/HTP 로그, ETW/perf trace 중 하나 이상으로 NPU 직접 증거를 남긴다.
6. 결과를 `phase0/verification-note-template.md` 형식으로 정리한다.

## Windows 환경 수집

PowerShell에서 실행한다.
Counter 후보 탐색은 장비/권한 상태에 따라 몇 분 걸릴 수 있다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\phase0\collect_windows_env.ps1 `
  -OutputDir .\phase0\runs\2026-06-09-xplus
```

생성되는 파일:

- `environment.json`: OS/CPU/메모리/비디오 컨트롤러/Qualcomm 관련 장치 정보
- `performance-counter-candidates.json`: NPU/Neural/AI/GPU 관련 counter set 후보
- `counter-sample.json`: 읽을 수 있는 후보 counter의 짧은 샘플

## 텍스트 모델 측정

백엔드가 `http://127.0.0.1:8000/v1` 형태의 OpenAI 호환 엔드포인트를 제공한다고 가정한다.

```powershell
python .\scripts\phase0\measure_openai_backend.py `
  --base-url http://127.0.0.1:8000/v1 `
  --model qwen3-text-small `
  --runs 3 `
  --max-tokens 128 `
  --output .\phase0\runs\2026-06-09-xplus\text-benchmark.jsonl
```

## VLM vision encode 측정

이미지 파일을 함께 넘기면 OpenAI chat/completions의 `image_url` content payload로 요청한다.

```powershell
python .\scripts\phase0\measure_openai_backend.py `
  --base-url http://127.0.0.1:8000/v1 `
  --model qwen3-vl-4b `
  --image .\phase0\samples\dialog.png `
  --prompt "이미지에 보이는 외국어 텍스트만 한국어로 번역해줘." `
  --runs 3 `
  --max-tokens 128 `
  --output .\phase0\runs\2026-06-09-xplus\vlm-benchmark.jsonl
```

## 합격 기준

- ~4B 모델이 `> 5 tok/s`를 달성한다.
- NPU 사용 직접 증거가 최소 1개 있다.
- 텍스트 모델과 VLM 경로의 결과가 분리 기록되어 있다.
- vision encode가 CPU fallback이면 그 사실을 명시하고 Phase 2 경로 결정을 남긴다.

`tok/s + CPU 유휴`는 보조 증거다. 단독 합격 근거로 쓰지 않는다.

## 불합격 기준

- 응답은 나오지만 NPU 직접 증거가 없다.
- NPU%를 읽을 수 있는데 생성 중 계속 0%다.
- ~4B 모델이 5 tok/s 이하이며 모델 축소 외 개선 여지가 없다.
- 백엔드가 X Plus SoC 게이팅, 모델 포맷, 드라이버 문제로 재현 불가능하게 실패한다.
