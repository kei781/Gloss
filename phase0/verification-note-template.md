# Phase 0 검증 노트

## 요약

| 항목 | 값 |
|---|---|
| 검증일 |  |
| 검증자 |  |
| 장비 | Snapdragon X Plus / X1P-42-100 / 32GB |
| OS 빌드 |  |
| 드라이버/런타임 |  |
| 최종 판정 | Pass / Fail / Partial |

## 백엔드 후보

| 순위 | 백엔드 | 버전 | 모델 | 결과 | 비고 |
|---|---|---|---|---|---|
| 1 | npurun(Genie) |  |  |  |  |
| 2 | NexaSDK |  |  |  |  |
| 3 | ONNX Runtime + QNN EP |  |  |  |  |

## 환경 수집

| 항목 | 파일/값 |
|---|---|
| 환경 JSON |  |
| counter 후보 JSON |  |
| counter 샘플 JSON |  |
| 작업 관리자 스크린샷 |  |
| 백엔드 로그 |  |
| ETW/perf trace |  |

## 텍스트 모델 측정

| 항목 | 값 |
|---|---|
| 모델 |  |
| 양자화 |  |
| 실행 명령 |  |
| 결과 JSONL |  |
| 평균 TTFT |  |
| 평균 tok/s |  |
| token count source | usage / estimate |
| CPU/RAM 관찰 |  |
| NPU 직접 증거 |  |
| 판정 | Pass / Fail / Partial |

## VLM 측정

| 항목 | 값 |
|---|---|
| 모델 |  |
| 양자화 |  |
| 이미지 샘플 |  |
| 실행 명령 |  |
| 결과 JSONL |  |
| 평균 TTFT |  |
| 평균 tok/s |  |
| vision encode NPU 증거 |  |
| CPU fallback 여부 |  |
| 판정 | Pass / Fail / Partial |

## 직접 증거

NPU 직접 증거로 인정한 자료를 적는다.

- [ ] 작업 관리자 NPU% > 0
- [ ] PDH NPU counter sample
- [ ] Genie/QNN/HTP 로그
- [ ] 벤더 샘플 출력
- [ ] ETW/perf trace

증거 설명:

```text

```

## 결론

### 합격 여부

Pass / Fail / Partial

### 최종 모델/사이즈 결정

- Text:
- Visual:
- Vision encode:

### 다음 Phase에 넘길 결정

- Phase 1 Text 엔진에서 사용할 백엔드:
- Phase 2 Visual 온디맨드에서 사용할 경로:
- 경량 OCR + 소형 LLM fallback 필요 여부:

### 실패/위험 기록

```text

```
