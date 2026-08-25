# 멀티카메라 영상 파이프라인 및 AI 편집 스위트 (Multicam Video Pipeline & AI Editing Suite)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

---

> [!IMPORTANT]
> **🚀 Google Antigravity 전용 스킬 (Antigravity Exclusive Skill)**  
> 본 툴킷은 **Google Antigravity Agent** 전용으로 네이티브 설계된 스킬입니다(Antigravity의 1M 멀티모달 비디오 인식 기능 및 스킬 아키텍처 의존). **현재 Claude Code, OpenAI Codex, Cursor 등 다른 AI 코딩 도구 및 에이전트에서는 실행할 수 없습니다**. 현재 Antigravity만 전적으로 지원합니다.

---

대규모 멀티모달 AI 모델(Gemini 3.7 Flash 1M 컨텍스트 윈도우) 및 전문 NLE(DaVinci Resolve, Adobe Premiere Pro, Final Cut Pro)에 최적화된 고성능 모듈형 멀티카메라 영상 처리 파이프라인 및 AI 편집 툴킷입니다.

---

## 📦 Antigravity 스킬 임포트 및 설치

Antigravity Skill 표준 규격을 준수하며, Antigravity 스킬 디렉터리로 바로 복제하여 사용할 수 있습니다:

```bash
git clone https://github.com/sylphlin/multicam-video-preprocessing.git ~/.gemini/config/skills/multicam-video-preprocessing
```

### 📁 Skill 디렉토리 구조
```text
multicam-video-preprocessing/
├── SKILL.md                       # Antigravity 스킬 규격 및 분기 판단 규칙
├── assets/                        # Antigravity 프롬프트 에셋 (Prompt Assets)
│   └── edl_interview_template.md  # 2대 카메라 인터뷰 프롬프트 템플릿
├── scripts/                       # 실행 스크립트 및 처리 모듈
│   ├── multicam_pipeline.py       # Step 1: 오디오 동기화, EBU R128, 챕터 분할, 그리드 합성
│   ├── generate_edl_with_gemini.py# Step 2: Gemini 3.7 Flash EDL 편집 결정 생성
│   ├── export_fcp7_xml.py         # Step 3A: FCP7 XML 타임라인 내보내기 (⭐ 주요 경로)
│   ├── edl_to_video.py            # Step 3B: 하드웨어 가속 직접 영상 렌더링 (🎬 차선 경로)
│   ├── concat_videos.py           # Step 4B: 전체 무손실 스트림 결합 (🎬 차선 경로)
│   └── modules/                   # 내부 영상/오디오 핵심 알고리즘
└── README.md
```

---

## 🌟 엔드투엔드 전체 워크플로우

```mermaid
flowchart TD
    A["미처리 멀티카메라 원본 (2–6 CAMs)"] --> B["1단계: 멀티카메라 동기화 및 AI 그리드 전처리"]
    
    B --> C["【전체 동기화 마스터 영상】"]
    B --> D["【AI 분석용 그리드 영상】"]
    
    D --> E["2단계: AI 멀티모달 가편집 결정"]
    E --> F["【EDL 편집 결정 목록 (CSV)】"]
    
    C --> G{"출력 포맷 선택"}
    F --> G
    
    G -->|"주요: 전문 NLE 소프트웨어 (90%)"| H["3A단계: FCP7 XML 타임라인 내보내기<br/>(DaVinci / Premiere 직접 가져오기)"]
    G -->|"차선: 간이 프리뷰 영상 (10%)"| I["3B단계: MP4 영상 직접 렌더링<br/>(NLE 없이 즉시 출력)"]
```

---

## 💬 사용 시나리오 및 대화형 프롬프트 예시

Antigravity 채팅창에서 자연어로 요청하기만 하면 Agent가 백엔드 모듈을 자동 호출하여 처리합니다:

### 시나리오 1: 편집용 XML 내보내기 (전문가 워크플로우 ⭐ 추천)
- **적용 대상**: 가편집 결과를 DaVinci Resolve, Adobe Premiere Pro 또는 Final Cut Pro로 가져와 후속 색보정 및 오디오 믹싱을 진행할 때.
- **프롬프트 예시**:
  > "*2대의 멀티카메라 인터뷰 영상 `CAM1.mp4`와 `CAM2.mp4`가 있습니다. 타임코드 동기화와 음량 표준화를 진행하고 인터뷰 편집 규칙을 적용하여 DaVinci Resolve로 바로 가져갈 수 있는 XML 타임라인을 생성해 주세요.*"
- **산출물**:
  1. `final_cut_full.xml` (98개 이상의 컷과 컬러 사유 마커가 포함된 단일 시퀀스 타임라인)
  2. `CAM1_synced.mp4`, `CAM2_synced.mp4` (완벽 동기화 및 -14 LUFS 표준화 마스터)
- **DaVinci Resolve 가져오기 3단계**:
  1. DaVinci Resolve를 열고 새 프로젝트를 생성합니다.
  2. `CAM1_synced.mp4`와 `CAM2_synced.mp4`를 **미디어 풀**로 드래그합니다.
  3. **파일 $\rightarrow$ 가져오기 $\rightarrow$ 타임라인...** (`Cmd + Shift + I`)을 클릭하여 `final_cut_full.xml`을 선택합니다!

---

### 시나리오 2: 영상 직접 출력 (간이 프리뷰 워크플로우 🎬)
- **적용 대상**: 편집 워크스테이션을 사용할 수 없거나 클라이언트에게 편집 템포를 빠르게 검토받아야 할 때.
- **프롬프트 예시**:
  > "*이 멀티카메라 영상들을 AI 가편집하여 합본 MP4 프리뷰 영상으로 직접 렌더링해 주세요.*"
- **산출물**:
  1. `final_cut_full.mp4` (전체 결합 완성 영상)

---

## 🔍 각 단계별 상세 실행 내용

### 1단계: 멀티카메라 동기화 및 AI 그리드 전처리 (`multicam_pipeline.py`)
1. **전체 구간 8kHz FFT 오디오 타임라인 동기화**:
   - 8kHz 다운샘플링 상호상관 연산으로 카메라 간 물리적 시간 오차 $\Delta t$를 수 초 만에 밀리초 단위로 산출.
2. **EBU R128 (-14 LUFS) 방송 표준 음량 표준화**:
   - 2-Pass 필터링으로 모든 트랙을 -14.0 LUFS, 11.0 LRA, -1.5 dBTP 표준 음량으로 일괄 보정.
3. **30~40분 자연스러운 멈춤 감지 챕터 분할 (Auto-Split)**:
   - 음성 에너지 최소 지점과 호흡 멈춤을 자동 감지하여 무손실 스트림 분할.
4. **전체 동기화 마스터 영상 출력 (`*_synced.mp4`)**:
   - NLE 타임라인이 직접 참조할 전체 길이 동기화 마스터 파일 즉시 생성.
5. **2~6대 카메라 멀티인원 컴팩트 화면 합성**:
   - 전체 $\le 1080P$, 대당 $\ge 640 \times 480$ 화질을 보장하며 멀티모달 **토큰 소모를 50%~83% 대폭 절감**.

---

### 2단계: Gemini AI 멀티모달 가편집 결정 (`generate_edl_with_gemini.py`)
1. **프롬프트 에셋 로드**: `assets/edl_interview_template.md` 템플릿 적용.
2. **Phase 0: 도입부/말미 무효 구간 트리밍**:
   - 촬영 시작 전 테스트 발성 및 준비 구간 제외 (`Global_Start_Time`);
   - 인터뷰 종료 후 잡담 및 마이크 탈거 등 미종료 구간 완전 절제 (`Global_End_Time`).
3. **Phase 1~4: 의미 구조 기반 컷 전환**:
   - 발화자를 음성 주도로 추적하여 어절 경계에 컷 포인트 정렬.
   - 듣는 이의 2~3초 리액션 샷(웃음, 끄덕임) 삽입.
   - 단일 컷 $\ge 2.5\text{s}$ 제한으로 깜빡임 방지.

---

### 3A단계 (주요 경로): FCP7 XML 타임라인 내보내기 (`export_fcp7_xml.py`)
1. **다중 챕터 타임코드 연속 누적**: 여러 Part를 하나의 연속 시퀀스로 매핑.
2. **1:1 타임코드 완벽 일치 (`start == in`)**: NLE에서 슬립/슬라이드 트리밍 완벽 지원.
3. **마스터 오디오 트랙 및 사유 마커 주입**: CAM1 주 오디오 연속 배치 및 AI 편집 사유를 컬러 마커로 주입.

---

### 3B단계 (차선 경로): 직접 렌더링 및 무손실 결합 (`edl_to_video.py` & `concat_videos.py`)
1. **하드웨어 가속 렌더링**: Apple Silicon `h264_videotoolbox` 기반 고속 렌더링.
2. **초고속 무손실 스트림 결합**: `-c copy` 방식으로 초당 수백 프레임 속도로 결합.
