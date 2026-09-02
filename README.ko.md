# 멀티카메라 영상 지능형 전처리 및 AI 편집 스위트 (Antigravity Native)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

---

> [!IMPORTANT]
> **🚀 Google Antigravity 네이티브 스킬 & 워크플로우**  
> 본 툴킷은 **Google Antigravity Agent 프레임워크(Gemini 3.7 Flash 1M 멀티모달 컨텍스트)** 및 전문 NLE 편집 소프트웨어(DaVinci Resolve, Adobe Premiere Pro, Final Cut Pro)를 위해 설계된 멀티카메라(2~6대) 지능형 전처리 파이프라인입니다.

---

본 프로젝트는 Antigravity 대화창에서 자연어로 요청하기만 하면 터미널 명령어를 입력할 필요 없이 다중 카메라 동기화, 음량 표준화, AI 가편집, XML 타임라인 및 YouTube 자막 생성을 자동으로 수행합니다.

---

## 📦 Antigravity 설치 및 디렉토리 구조

```bash
git clone https://github.com/sylphlin/multicam-video-preprocessing.git ~/.gemini/config/skills/multicam-video-preprocessing
```

### 📁 디렉토리 구조
```text
multicam-video-preprocessing/
├── GEMINI.md                          # Antigravity 루트 워크스페이스 상주 규칙
├── .agent/
│   ├── rules/
│   │   └── multicam_rules.md          # 상주 정책 및 제약 조건
│   └── workflows/
│       └── multicam_workflow.md       # 공식 4단계 실행 워크플로우 (Stage-Gated Runbook)
├── skills/
│   └── multicam-video-preprocessing/
│       └── SKILL.md                   # Antigravity 스킬 기능 정의서
├── assets/                            # 프롬프트 템플릿 자산
│   ├── edl_interview_template.md      # Gemini 가편집 프롬프트 템플릿
│   └── subtitle_proofread_template.md # YouTube 자막 교정 템플릿
├── scripts/                           # 핵심 실행 도구 라이브러리
│   ├── multicam_pipeline.py           # 1단계: 멀티캠 동기화, 음량 표준화, 챕터 분할, 그리드 합성
│   ├── generate_edl.py                # 2단계: Gemini 3.7 Flash 멀티모달 가편집 결정
│   ├── export_fcp7_xml.py             # 3A단계: FCP7 XML 타임라인 내보내기 (추천)
│   ├── edl_to_video.py                # 3B단계: 하드웨어 가속 비디오 렌더링
│   ├── concat_videos.py               # 3B단계: 전체 챕터 무손실 병합
│   ├── generate_subtitles.py          # 4단계: YouTube 자막 생성 (Whisper + Gemini)
│   └── modules/                       # 음향 및 영상 핵심 알고리즘 모듈
└── README.ko.md
```

---

## 💬 사용 시나리오 및 프롬프트 예시 (User Scenarios & Prompt Examples)

### 시나리오 1: 편집용 XML 타임라인 내보내기 (DaVinci Resolve / Premiere Pro ⭐ 추천)
- **프롬프트 예시**:
  > *"2대의 인터뷰 영상 `CAM1.mp4`와 `CAM2.mp4`가 있습니다. 오디오 동기화와 음량 표준화를 진행하고 DaVinci Resolve에서 열 수 있는 XML 타임라인을 생성해주세요."*
- **결과물**:
  1. `final_cut_full.xml` (모든 컷 편집점 및 AI 판정 사유 마커 포함 타임라인)
  2. `CAM1_synced.mp4`, `CAM2_synced.mp4` (동기화 및 음량 표준화 마스터 영상)
- **DaVinci Resolve 가져오기**:
  1. DaVinci Resolve를 열고 새 프로젝트 생성.
  2. `./output/CAM1_synced.mp4` 및 `./output/CAM2_synced.mp4`를 **미디어 풀**에 드래그.
  3. **파일 $\\rightarrow$ 가져오기 $\\rightarrow$ 타임라인...** 을 클릭하고 `final_cut_full.xml` 선택.

---

### 시나리오 2: 완성 영상 및 YouTube 자막 직접 출력 (미리보기 🎬)
- **프롬프트 예시**:
  > *"멀티카메라 영상을 AI 가편집하여 미리보기용 MP4 영상과 교정된 YouTube 자막을 출력해주세요."*

---

### 시나리오 3: 챕터 분할 시간 지정 (사용자 지정 타이밍 ⏱️)
- **프롬프트 예시**:
  > *"챕터를 약 10분 내외의 자연스러운 무음 구간에서 분할하여 처리해주세요."*
- **Agent 동작**:
  - 설정 파일 수정 없이 자동으로 분할 파라미터를 조정하여 실행합니다.

---

## 🔍 단계별 처리 상세 설명 (Detailed Pipeline Steps)

### 1단계: 멀티카메라 물리 전처리 (`multicam_pipeline.py`)
1. **8kHz FFT 오디오 시간 동기화**: 오디오를 8kHz로 다운샘플링하여 1D FFT 상호상관을 고속 계산하고, 각 카메라의 녹화 시작 편차 $\\Delta t$ 를 밀리초 단위로 정확히 보정.
2. **EBU R128 (-14 LUFS) 음량 표준화**: YouTube 권장 기준인 -14 LUFS(True Peak -1.5 dBTP)에 맞춰 2-Pass loudnorm 필터로 음량을 균일화.
3. **동기화 마스터 비디오 출력 (`*_synced.mp4`)**: XML 타임라인에서 직접 참조하는 동기화 및 음량 표준화 마스터 비디오 출력.
4. **30~40분 자연스러운 무음 포즈 기반 챕터 분할**: 오디오 RMS 에너지를 스캔하여 문장이 잘리지 않도록 30~40분 단위로 무손실 분할 (1M Token 컨텍스트에 최적화).
5. **2~6대 멀티캠 컴팩트 그리드 합성**: 최대 1080p 이하, 각 화각 480p 이상의 그리드 비디오를 합성하여 AI 토큰 소비를 50%~83% 절감.

### 2단계: Gemini AI 멀티모달 가편집 결정 (`generate_edl.py`)
- 화자의 음성을 주도적으로 추적하여 카메라 앵글을 결정하고 리액션 컷을 적절히 삽입하며, 단일 컷 2.5초 이상 점프컷 방지 규칙을 적용하여 `edl_part*.csv` 생성.

### 3A단계: FCP7 XML 타임라인 내보내기 (`export_fcp7_xml.py`)
- 모든 챕터의 타임스탬프를 누적 통합하여 DaVinci Resolve / Premiere Pro / Final Cut Pro에 직접 로드 가능한 `final_cut_full.xml` 생성.

### 4단계: YouTube 자막 생성 (`generate_subtitles.py`)
- **3단계 골든 자막 생성 파이프라인 (Three-Stage Pipeline)**:
  1. **전체 오디오 글로벌 용어집 추출**: Gemini 1M Context로 전체 에피소드 오디오를 청취하여 고유명사, 영문명, 전문 용어집(`final_cut_full_glossary.md`)을 자동 생성.
  2. **Whisper 음향 물리 밀리초 타임코드 및 단어 타임스탬프**: 로컬 Whisper(ARM NEON / AVX int8)로 단어 수준 음향 파형을 측정하여 0.000초 오차 없는 기준 타임라인 및 단어 캐시(`final_cut_full_raw_whisper.srt` & `final_cut_full_words.json`)를 생성 (재실행 시 수초 내 로드).
  3. **청크 단위 물리 음향 앵커 재투영 (Chunk-Scoped Reprojection)**: Gemini는 문맥 줄바꿈과 텍스트 교정에만 전념하고, 백엔드 SequenceMatcher 단조 정렬 알고리즘이 Whisper 단어 수준 물리 시간에 정밀 재투영 (음성과 100% 일치, 스포일러/지연 완전 근절).
- **🎯 방송 및 스트리밍 표준 자막 품질 감사 엔진 (자동 최적화)**:
  - **화자 의미 완결 및 분리**: 동일 화자의 질문/문장은 한 줄로 결합하고, 화자 전환 시 새로운 자막 줄을 강제 분리하여 혼란 방지.
  - **1줄 글자 수 제한**: 한국어 $\le 16$자, 중국어/일본어 $\le 15$자, 영어 $\le 37$ CPL (긴 문장은 구문 단위로 자동 분할).
  - **문장 끝 불필요 문장부호 100% 제거**: 문장 끝의 `。`, `，`, `；`를 완전 제거하여 깔끔한 화면 구성.
  - **음성 시작 0.000초 물리 동기화**: Whisper 음향 파형 시작점에 엄격 고정하여 스포일러 방지.
  - **가독 시간 보호**: $1.0\text{s} \le \text{Duration} \le 6.0\text{s}$ (짧은 문장은 여백을 활용하여 $\ge 1.0\text{s}$ 확보).
  - **플리커 방지 미세 간격 결합**: $< 0.6\text{s}$ 간격을 0s로 평활화, 진정한 휴지 시 $+0.4\text{s}$ 호흡 여백 후 화면을 깔끔히 클리어.
  - **단조 시간 연속성 보장**: 시간 역행, 블록 중첩, 0/음수 시간 자막 완전 배제.
- **출력**: `final_cut_full.srt`, `final_cut_full.vtt`, `final_cut_full_subtitle_report.json`(품질 감사 JSON), `final_cut_full_subtitle_report.md`(시각화 평가 Markdown), `final_cut_full_glossary.md`, `final_cut_full_words.json`.

---

## 🛠️ 필요 환경

- **Google Antigravity IDE / Agent Framework**
- **FFmpeg** (`h264_videotoolbox` 하드웨어 인코딩 및 `loudnorm` 지원)
- **Python 3.8+**
- **NumPy** (`pip install numpy`)
