# ADR — Gloss

> **Gloss** — 가리킨 화면 텍스트를 Snapdragon NPU에서 바로 번역해 게임자막처럼 띄우는 온디바이스 오버레이. 로컬·프라이빗, 추론은 NPU 전담.

각 결정은 MADR 형식(Status / Context / Decision / Consequences).

외부 런타임·모델 빌드·OS 기능처럼 빠르게 변하는 전제는 Phase 0 검증 노트에 검증일, 버전, 실행 명령, 로그/스크린샷, 실패 조건을 남긴다. 검증 노트 없이 Accepted 결정을 본 구현 착수 근거로 쓰지 않는다.

| 버전 | 작성일 | 작성자 |
|---|---|---|
| v0.3 | 2026-06-09 | 호크 (노상운) |

> v0.3: 프로젝트명 **Gloss** 확정 + tagline 추가. (v0.2: ADR-001/003/009/010 갱신, ADR-012(모델 사이징)·013(Visual 2-경로)·014(실시간 자막 비목표) 추가.)

---

## ADR-001 — NPU 추론 백엔드: npurun (primary), NexaSDK / ONNX+QNN (alternatives). LiteRT·GGUF 런타임 기각
**Status**: Accepted (Phase 0에서 직접 증거로 재확인)

**Context**: PC(Windows ARM)의 NPU LLM 경로는 모바일과 다르다.
- **GGUF 런타임**(Ollama/llama.cpp/LM Studio)은 ARM에서 CPU-only — NPU 미사용.
- **LiteRT-LM**의 NPU 가속은 **Qualcomm AI Engine Direct Delegate**를 통하는데, 이 delegate는 **Android(모바일 Snapdragon) 한정**이다. 화려한 LiteRT NPU 벤치(FastVLM 등)는 전부 폰(Snapdragon 8 Elite Gen 5)에서 나온 수치이고, Windows ARM에서는 CPU/GPU로만 동작(게다가 DirectML ARM64도 부실).
- PC에서 Hexagon을 실제로 때리는 길은 **ONNX Runtime + QNN execution provider**(QAIRT의 QnnHtp.dll) 또는 **Qualcomm Genie SDK**다. NexaSDK·AnythingLLM·npurun이 모두 Genie/QNN을 래핑한다. NexaSDK 등 일부는 SoC 마케팅 문자열로 게이팅해 X Plus에서 깨질 수 있으나, npurun은 `Win32_VideoController`로 Hexagon을 직접 프로빙해 회피하고 OpenAI/Ollama 호환 HTTP 서버를 제공한다.

**Decision**: 기본 백엔드는 **npurun(Genie)**. 대안은 **NexaSDK**(멀티모달 적재가 더 매끄러울 때)와 **ONNX Runtime + QNN EP**(폴백). 모두 **OpenAI 호환 엔드포인트로 추상화**해 설정으로 교체 가능하게 둔다. **LiteRT-LM·GGUF 런타임은 PC NPU 미지원이라 채택하지 않는다.**

**Consequences**: 백엔드 교체가 설정 한 줄. per-token 디코드 속도는 libGenie 의존이라 백엔드를 바꿔도 동일. NexaSDK 채택 시 X Plus 게이팅 사전 검증 필수. Phase 0에서는 백엔드/드라이버/모델 버전, 실행 명령, NPU 적재·실행 로그 또는 trace를 남긴다. LiteRT는 향후 Windows ARM NPU 지원이 오면 재평가(현재는 제외).

---

## ADR-002 — 이중 엔진 분리 (Visual / Text)
**Status**: Accepted

**Context**: surface의 입력 성질이 둘로 갈림 — 픽셀로만 접근 가능한 것(게임·영상)과 네이티브 텍스트가 있는 것(소설·텍스트 PDF). 후자를 OCR로 처리하면 불필요한 오인식·지연 발생.

**Decision**: **Visual 엔진**(캡처 → OCR+번역)과 **Text 엔진**(추출 → 텍스트 LLM 번역)을 분리. 동일 NPU 백엔드 위에서 프론트만 분기.

**Consequences**: 각 경로가 자기 입력에 최적화. 한 파이프라인 강제 시의 정확도·복잡도 손실 회피. 대신 코드 표면이 둘로 늘어남.

---

## ADR-003 — Visual 모델: Qwen3-VL-4B 기본. OmniNeural-4B·Gemma 4 12B 기각
**Status**: Accepted

**Context**: 게임 폰트는 스타일라이즈드·반투명·저대비라 전통 OCR이 취약. VLM은 이미지를 통째로 이해해 더 강건. 소스가 일본어(라노벨·게임)면 CJK 강세 필요.
- **OmniNeural-4B**: NPU 네이티브 멀티모달이나 영어 위주 최적화로 한국어 출력이 약함.
- **Gemma 4 12B**: encoder-free + MTP로 아키텍처는 매력적이나 12B는 이 기기에 과대 — 상세는 ADR-012.

**Decision**: Visual 기본은 **Qwen3-VL-4B**(CJK 강세, OCR+번역 단일 패스). 속도가 급하면 ADR-013의 경량 경로로 다운시프트. OmniNeural·Gemma 4 12B 미채택. (Gemma를 꼭 쓰려면 엣지 사이즈 E4B/E2B가 NPU 결에 맞으나, CJK 번역엔 Qwen3가 우세.)

**Consequences**: CJK·스타일 텍스트에 강함. 별도 OCR 단계 제거로 CPU 부하↓. 단 저대비·산재 HUD에서 hallucination 가능(R2) → 구역 기반 캡처로 완화.

---

## ADR-004 — Text 모델: Qwen3 (짧은 번역은 소형 우선)
**Status**: Accepted

**Context**: 텍스트 경로는 비전 불필요. CJK·문학 번역 품질과 NPU 적재 가능성·속도가 우선. 디코드는 모델 크기에 반비례(ADR-012).

**Decision**: Text 엔진은 **Qwen3 계열**. 짧은 번역(게임 대사 등)은 **소형(≤1.7B)** 우선, 긴 문학 번역은 4B까지. Phase 0 측정으로 확정.

**Consequences**: 짧은 번역의 체감 속도 확보. 모델 다종 운용 → 스왑 비용 고려(ADR-010).

---

## ADR-005 — 화면 캡처: Windows.Graphics.Capture (WGC). GDI/BitBlt 기각
**Status**: Accepted

**Context**: GDI/BitBlt는 전체화면 DirectX 게임·하드웨어 디코딩 영상에서 검은 화면을 반환. WGC는 컴포지터 경로로 이들까지 캡처 가능.

**Decision**: 캡처는 **WGC**로 통일.

**Consequences**: 게임·유튜브 모두 정상 캡처. WGC는 ARM64 네이티브 WinRT라 호출 바인딩 필요(Python: winrt/pywinrt, 또는 .NET).

---

## ADR-006 — 게임 타겟팅: 구역 기반. 핀포인트 기각(모호 영역)
**Status**: Accepted

**Context**: 게임 텍스트는 경계가 모호한 경우가 많아 "커서 밑 한 줄" 정밀 타겟팅이 깨짐.

**Decision**: 모호 영역은 대사창/커서 둘레의 넉넉한 구역을 통째 캡처해 전량 번역. 핀포인트(OCR bbox + point-in-rect hit-test)는 **깔끔한 텍스트에 한해 선택적**.

**Consequences**: "어느 줄이냐" 문제 소거. 구역 내 무관 텍스트가 섞일 수 있음(트레이드오프).

---

## ADR-007 — 오버레이: PyQt6 layered / click-through window
**Status**: Accepted

**Context**: 게임자막식 출력 = 투명·항상 위·클릭 통과 창. 본인 PyQt6 오버레이 경험 보유. 입력 영역과 출력 영역은 분리.

**Decision**: PyQt6 프레임리스 창 + `WA_TranslucentBackground` + `WindowStaysOnTopHint` + `WA_TransparentForMouseEvents`(클릭 통과). 고정 출력 rect에 반투명 QWidget + QLabel.

**Consequences**: 기존 경험 재사용. ARM64 PyQt6 휠 확인 필요(R4). 불가 시 .NET WPF layered window로 대안.

---

## ADR-008 — 소설/논문: 네이티브 텍스트 추출(OCR 미사용), JS 사이트는 Playwright
**Status**: Accepted

**Context**: 브라우저 소설·텍스트 PDF는 글자가 텍스트로 존재 → OCR은 불필요·부정확·느림. 다만 최신 웹소설 플랫폼은 본문을 클라이언트에서 렌더해 단순 fetch 시 빈 껍데기가 옴.

**Decision**: 본문 추출은 readability/trafilatura. **JS 렌더 사이트는 Playwright 헤드리스**로 렌더된 DOM 확보. 추출 텍스트를 Text 엔진으로 번역.

**Consequences**: 정확·고속. 로그인 월·인코딩(EUC-KR)·사이트별 셀렉터 예외 처리 필요. 개인 독서 한정(대량 스크래핑은 비목표).

---

## ADR-009 — 대시보드 메트릭: 클라이언트측 호출 계측 + psutil. NPU%는 선택 + fallback 감지
**Status**: Accepted

**Context**: 앱 자체가 추론 호출 클라이언트라 TTFT·tok/s·토큰·레이턴시를 호출 경로에서 직접 확보 가능(백엔드 스크래핑 불필요). NPU 사용률은 Windows perf counter가 GPU와 같은 원리지만 counter set 이름이 비공개라 PDH로 읽기 까다롭고, ARM·신규 빌드라 불안정. 한편 "생성 중인데 NPU% 0"은 silent CPU fallback의 신호다.

**Decision**: 핵심 메트릭은 **호출 경로 계측** + **psutil**(CPU%/RAM). 제품 대시보드의 NPU%는 PDH로 시도하되 실패 시 생략하고 **"tok/s 정상 + CPU 유휴"를 보조 지표**로 노출한다. 단, Phase 0 게이트에서는 tok/s + CPU 유휴만으로 합격시키지 않고, NPU counter 또는 Genie/QNN/HTP 로그·벤더 샘플 출력·ETW/perf trace 같은 직접 증거를 요구한다. NPU%를 읽을 수 있으면 **생성 중 0% = CPU fallback 경고**로 활용(FR-D6).

**Consequences**: 메트릭 대부분 무비용. 제품 런타임에서는 NPU% 누락 리스크를 우회 지표로 흡수하되, 최초 게이트는 직접 증거로 닫는다. 계측 훅은 Phase 1부터 심어 둠.

---

## ADR-010 — 모델 스왑: reload형 셀렉터. instant hot-swap 기각
**Status**: Accepted (optional feature)

**Context**: NPU 모델은 그래프 컴파일·적재가 무거워(수초) 즉시 교체 불가. 다중 모델 동시 적재도 NPU 메모리상 비보장. 또한 용례별로 사이즈 티어(빠른 소형 ↔ 품질 4B)를 바꾸고 싶을 수 있음(ADR-012).

**Decision**: 모델 변경은 **드롭다운 → 현재 언로드 → 새 모델 적재(로딩 상태 표시)**. 사이즈 티어 전환도 같은 방식. OpenAI `model` 필드 즉시 라우팅은 기대하지 않음.

**Consequences**: 구현 단순·안정. UX는 수초 지연 동반. instant 욕심을 배제해 범위 보호.

---

## ADR-011 — 구현 스택: Python + PyQt6, 백엔드는 OpenAI 호환 HTTP로 결합
**Status**: Accepted

**Context**: 오버레이·핫키·캡처·대시보드를 한 앱에 통합하고 본인 PyQt6 경험을 활용. 백엔드(npurun/Nexa)는 프로세스 분리 + HTTP 결합이 교체성·관측성에 유리.

**Decision**: 프론트는 **Python/PyQt6 단일 앱**(오버레이 + 대시보드 패널). 추론은 별도 백엔드 프로세스에 **OpenAI 호환 HTTP**로 요청. CPU/GPU는 캡처·렌더·계측 및 선택적 경량 OCR fallback만 담당.

**Consequences**: 단일 프로세스 GUI로 자원 절약. 백엔드 독립 재시작·교체 가능. ARM64 휠 의존성 관리 필요(R4).

---

## ADR-012 — 모델 사이징 정책: ≤4B, 용례별 우선 소형. "클수록 좋다" 기각
**Status**: Accepted

**Context**: 디코드는 **메모리 대역폭 바운드**라 속도 ≈ 대역폭 ÷ (모델 토큰당 바이트). RAM 용량·연산유닛으론 안 빨라지고, 모델 크기에 거의 반비례한다(4B Q4 ~15 tok/s, 12B Q4 ~5~6 tok/s). Snapdragon X NPU 생태계는 **~4B 파라미터 예산**이 실효 한계. 번역은 12B의 추론력 이점이 거의 무의미한 작업이라 대형 모델의 품질 이득이 작다. **Gemma 4 12B**는 나온 지 얼마 안 돼 Hexagon NPU 빌드도 사실상 부재 → CPU/GPU로 떨어져 전제 붕괴.

**Decision**: 이 기기에서는 **≤4B만** 사용. 짧은 번역(게임 대사 등)은 **소형(0.6B~1.7B)** 우선, 정확도가 필요한 긴 문학은 4B. **양자화(Q4)** 를 속도 레버로 사용. 12B+ 는 채택하지 않고 필요 시 **dGPU(RTX 5080) 기기**로 분리.

**Consequences**: 체감 속도 확보, 목적(가볍고 빠른 온디바이스) 부합. 대형 모델 품질이 필요한 작업은 별도 장비로 위임. 본문의 tok/s 수치는 계획 가정이며, Phase 0 측정값으로 최종 사이즈와 레이턴시 예산을 확정한다.

---

## ADR-013 — Visual 엔진 2-경로: VLM 단일패스(기본) + 경량 OCR·소형 LLM(탈출구)
**Status**: Accepted

**Context**: VLM 단일패스는 정확·간편하나 vision encode + 긴 prefill로 캡처당 수초~10초. 게임처럼 빠른 반응이 필요하면 부담. 분리형(가벼운 OCR로 텍스트만 뽑고 → 소형 텍스트 LLM 번역)은 단계가 늘지만 각 단계가 가벼워 전체가 빠를 수 있다(대신 OCR이 CPU를 약간 사용).

**Decision**: 기본은 **VLM 단일패스(Qwen3-VL-4B)**. 게임 응답성이 부족하면 **경량 OCR + 소형 LLM 경로**로 전환 가능하도록 인터페이스를 둔다(엔진 내부 교체 가능 구조).

**Consequences**: 정확도와 속도 사이를 용례별로 택할 수 있음. 두 경로 유지 비용. 분리형은 OCR의 CPU 점유를 일부 감수(NFR-1과 트레이드오프)하므로, 이 경로를 켠 경우 CPU 점유와 VLM 대비 레이턴시 이득을 별도 측정한다.

---

## ADR-014 — 실시간 흐르는 자막(유튜브 등)은 비목표. Live Captions에 위임
**Status**: Accepted

**Context**: 캡처-번역 1회가 수초인데 유튜브 자막은 계속 흐른다 → continuous 재번역으로는 흐름을 못 따라간다. Windows는 Copilot+ PC에서 Live Captions 실시간 번역(다국어)을 NPU로 이미 제공한다.

**Decision**: **실시간 흐르는 자막은 본 도구의 비목표**로 둔다. 영역 감시(FR-V3)는 **느린 전환(슬라이드·정지 자막)** 에 한정. 실시간 영상 자막이 필요하면 **Windows Live Captions**로 위임.

**Consequences**: 달성 불가능한 목표를 제거해 범위·기대치 보호. 영상 자막 실시간성은 OS 기능에 의존하며, Live Captions의 지원 언어·Windows 빌드·사용 가능 여부는 필요 시 별도 검증 노트에 남긴다.
