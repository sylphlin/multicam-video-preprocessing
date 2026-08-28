# 멀티카메라 영상 지능형 전처리 및 AI 편집 스위트 (Antigravity Native)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

---

> [!IMPORTANT]
> **🚀 Google Antigravity 네이티브 스킬 & 워크플로우**  
> 본 툴킷은 **Google Antigravity Agent 프레임워크(Gemini 3.7 Flash 1M 멀티모달 컨텍스트)** 및 전문 NLE 편집 소프트웨어(DaVinci Resolve, Adobe Premiere Pro, Final Cut Pro)를 위해 설계된 멀티카메라(2~6대) 지능형 전처리 파이프라인입니다.

---

## 📦 Antigravity 설치 및 구조

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
- 로컬 Whisper의 밀리초 단위 타임스탬프 + Gemini 1M 컨텍스트 기반 고유명사 및 동음이의어 교정을 결합하여 고품질 `.srt` / `.vtt` 생성.

---

## 🚀 빠른 시작 가이드

### 플랜 A: 전문 NLE 타임라인 워크플로우 (XML 내보내기 ⭐ 추천)

```bash
# 1. 멀티카메라 전처리 (동기화 + EBU R128 음량 표준화 + 30-40분 챕터 분할 + 마스터 출력 + 그리드 합성)
python3 scripts/multicam_pipeline.py \
  --ref CAM1.mp4 --targets CAM2.mp4 \
  --auto-split --split-min-dur 30 --split-max-dur 40 \
  --normalize --merge \
  -o ./output/

# 2. Gemini 3.7 Flash AI 멀티모달 가편집 결정 (각 Part마다 실행)
python3 scripts/generate_edl.py -v ./output/multicam_merged_part1.mp4
python3 scripts/generate_edl.py -v ./output/multicam_merged_part2.mp4

# 3A. FCP7 XML 타임라인 내보내기
python3 scripts/export_fcp7_xml.py -d ./output/ -o ./output/final_cut_full.xml
```

#### 🎬 DaVinci Resolve 타임라인 가져오기:
1. DaVinci Resolve를 열고 새 프로젝트를 생성합니다.
2. `./output/CAM1_synced.mp4` 및 `./output/CAM2_synced.mp4`를 **미디어 풀(Media Pool)** 로 드래그합니다.
3. **파일 $\\rightarrow$ 가져오기 $\\rightarrow$ 타임라인...** 을 클릭하고 `final_cut_full.xml`을 선택합니다.
4. 모든 컷 편집점, 메인 오디오 트랙 및 컬러 마커가 즉시 로드됩니다!
