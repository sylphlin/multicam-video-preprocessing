# 멀티카메라 영상 지능형 전처리 및 AI 편집 스위트 (Antigravity Native)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

---

> [!IMPORTANT]
> **🚀 Google Antigravity 네이티브 스킬 & 워크플로우**  
> 본 툴킷은 **Google Antigravity Agent 프레임워크(Gemini 3.8 Flash 1M 멀티모달 컨텍스트)** 및 전문 NLE 편집 소프트웨어(DaVinci Resolve, Adobe Premiere Pro, Final Cut Pro)를 위해 설계된 멀티카메라(2~6대) 지능형 전처리 파이프라인입니다.

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
│   ├── multicam_pipeline.py           # 1단계: 멀티캠 동기화, 음량 표준화, 마스터 출력, 전체 그리드 합성
│   ├── generate_edl.py                # 2단계: Gemini 3.8 Flash Agentic Video 무분할 가편집 결정
│   ├── export_fcp7_xml.py             # 3A단계: FCP7 XML 타임라인 내보내기 (추천)
│   ├── edl_to_video.py                # 3B단계: 원패스 하드웨어 가속 직접 렌더링
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

---

## 🔍 단계별 처리 상세 설명 (Detailed Pipeline Steps)

### 1단계: 멀티카메라 물리 전처리 (`multicam_pipeline.py`)
1. **MFCC 음향 특성 상호상관 및 서브프레임 정밀 보정 (<0.125ms 정밀도)**:
   - **MFCC 음향 특성 상호상관 도입 이유**: 사람의 음성과 순간적인 음향 특성을 가장 정확하게 포착하는 멜 주파수 켑스트럼 계수(MFCC)를 채택. 기존 원본 파형 상관에 비해 FFT 메모리 사용량을 **97.7% 절감**(1시간 오디오 기준 약 12.5MB), 1시간 분량의 소재를 0.3초 이내에 초고속 동기화하며, 마이크 주파수 응답 차이 및 주변 소음에 대한 강력한 내성을 제공.
   - **3단계 폴백 사다리 (3-Tier Fallback Ladder)**:
     1. *초고속 120s MFCC 탐색*: 시작 120초 오디오를 추출하여 BBC 기준 점수 $Z \ge 12.0$(높은 신뢰도) 달성 시 0.4초 이내에 동기화 완료.
     2. *전체 MFCC 스캔*: 초기 점수 $< 12.0$ 또는 `--full-scan` 지정 시 전체 구간 스캔 실행.
     3. *원본 파형 FFT 폴백*: 전체 MFCC 점수가 낮을 경우($Z < 7.0$), 기존 하이패스 원본 파형 1D FFT 상호상관으로 자동 안전 전환.
   - **서브프레임 물리 음향 미세 정렬 (<0.125ms)**: 두 카메라의 중첩 활성 구간 내에서 최대 에너지의 5초 음성을 탐색하고, $\pm 32\text{ms}$ 탐색 반경 내에서 시간 영역 상호상관을 수행하여 단일 오디오 샘플 수준(**8kHz 기준 0.125ms 물리 음향 정밀도**)으로 정확도를 끌어올림.
   - **BBC 방송 표준 신뢰도 점수 및 저신뢰도 즉각 경고**: 상관 피크와 노이즈 플로어의 표준편차 비율로 평가:
     - $Z \ge 12.0$ 높은 신뢰도: `✓ Aligned` 표시.
     - $7.0 \le Z < 12.0$ 중간 신뢰도: `ℹ Aligned (marginal)` 표시.
     - $Z < 7.0$ 낮은 신뢰도: `⚠️ LOW CONFIDENCE` 표시 및 stderr 경고와 원인 진단 힌트 즉각 출력.
   - **Summary Gate 경고 박스 및 `--strict-sync` 안전 중단**: 1단계 완료 시 저신뢰도 카메라가 감지되면 눈에 띄는 경고 박스를 터미널에 출력하며, `--strict-sync` 플래그 지정 시 종료 코드 1로 즉시 중단되어 오정렬 자동 렌더링을 미연에 방지.
   - **컨테이너 실제 재생 시간 자동 탐지 (`--sample-dur` 조기 절단 버그 수정)**: `ffprobe`를 통해 영상 컨테이너의 실제 길이를 조회하여, 샘플 테스트 모드에서도 마스터 출력 및 중첩 구간이 전체 길이로 온전히 유지되도록 보장.
2. **EBU R128 (-14 LUFS) 2-Pass 선형 음량 표준화**: Pass 1에서 null sink를 통한 초고속 음향 측정(`I`, `LRA`, `TP`, `target_offset`), Pass 2에서 `linear=true`를 적용하여 다이내믹 펌핑(음량 들쑥날쑥 현상)을 완전 근절하고 -14.0 LUFS로 100% 정밀 고정.
3. **동기화 마스터 비디오 프레임 단위 정밀 병렬 출력 (`*_synced.mp4`)**: 키프레임(I-frame) 흡착으로 인한 밀리초 단위 밀림 및 검은 화면 끊김을 방지하기 위해 기본적으로 프레임 정밀 하드웨어 재인코딩(`h264_videotoolbox` / `libx264 -crf 18`) 채택. 초고속 무손실 가편집을 위한 `--stream-copy`도 지원.
4. **무분할 전체 멀티캠 컴팩트 그리드 합성 (`multicam_merged_full.mp4`)**: 최대 1080p 이하, 각 화각 480p 이상의 그리드 비디오를 합성하여 Agentic Video를 통한 전체 영상 직접 이해를 가능케 함.
- **실행 명령어 예시**:
  ```bash
  # 표준 4-in-1 전체 전처리 파이프라인 (동기화, 음량 표준화, 마스터 재인코딩, 그리드 합성):
  python3 scripts/multicam_pipeline.py \
    --ref CAM1.mp4 \
    --targets CAM2.mp4 CAM3.mp4 \
    --normalize --merge -o output/

  # 초고속 동기화 테스트 (시작 60초만 추출하여 동기화, 컨테이너 길이는 ffprobe로 자동 탐지):
  python3 scripts/multicam_pipeline.py \
    --ref CAM1.mp4 --targets CAM2.mp4 \
    --sample-dur 60 --normalize --merge -o output/

  # 강제 전체 MFCC 스캔 (120초 고속 사다리 우회):
  python3 scripts/multicam_pipeline.py \
    --ref CAM1.mp4 --targets CAM2.mp4 \
    --full-scan --normalize --merge -o output/

  # 엄격 동기화 검증 모드 (신뢰도 7.0 미만 시 즉시 에러 중단):
  python3 scripts/multicam_pipeline.py \
    --ref CAM1.mp4 --targets CAM2.mp4 \
    --strict-sync --normalize --merge -o output/

  # 초고속 스트림 복사 모드 (-c copy, 키프레임 흡착):
  python3 scripts/multicam_pipeline.py \
    --ref CAM1.mp4 --targets CAM2.mp4 \
    --stream-copy --normalize --merge -o output/
  ```

### 2단계: Gemini 3.8 Flash Agentic Video 가편집 결정 (`generate_edl.py`)
1. **프롬프트 템플릿 로드**: `assets/edl_interview_template.md`를 통한 방송 품질 기준의 엄격한 가편집 규칙 적용.
2. **Phase 0: 앞뒤 불필요 구간 및 현장 카운트다운 완전 배제 (무관용 원칙 및 비대칭 안전 마진)**:
   - **현장 카운트다운 완전 배제**: 장비 점검, 잡담, 슬레이트, 현장 스태프 카운트다운("5, 4, 3, 2, 1", "3, 2, 1" 등)을 시스템적으로 감지하여 제거.
   - **비대칭 안전 마진 (`[Start, Start+2s]` 자체 검증)**: `Global_Start_Time`은 반드시 마지막 카운트다운 음성이 완전히 종료된 이후로 설정. 컷 시작 직후 첫 2초 구간(`[Global_Start_Time, Global_Start_Time + 2.0s]`)에서 사고 사슬(Chain-of-Thought) 자체 검증을 수행하여 카운트다운이 남아있을 경우 시작점을 뒤로 이동시켜 본편 첫 단어에 완벽히 정렬.
   - **종료 후 미정지 구간 배제**: 인터뷰 종료 인사를 식별하여 녹화 종료 후 잡담, 썸네일 촬영 등 불필요한 영상(`Global_End_Time`)을 배제.
3. **차세대 아키텍처: Agentic Video Understanding (분할 불필요 전체 편집)**:
   - Gemini 3.8 Flash의 Agentic Video 기능(`processing="agentic"`)을 통해 1시간 이상의 무분할 그리드 영상을 직접 평가.
   - 목표 지향적 동적 희소 샘플링을 통해 입력 토큰 소비를 **99.7% 절감**(약 1,000,000 토큰에서 약 3,000 토큰으로 감소). 챕터 경계로 인한 발화 단절 위험을 원천 차단.
4. **표준 결과물 출력**:
   - 단일 표준 CSV 결정 목록(`edl_full.csv`) 및 Markdown 가편집 분석 보고서(`edl_full_report.md`) 출력.
5. **듀얼 백엔드 클라우드 아키텍처 (Vertex AI + GCS 기본, AI Studio 백업)**:
   - **기본 백엔드**: Google Cloud Vertex AI 및 Application Default Credentials(ADC, API 키 관리 불필요). 그리드 영상은 Google Cloud Storage(GCS)에 업로드되며, 로컬 SHA-256 및 파일 크기 캐시를 통해 중복 업로드를 완전히 방지합니다.
   - **백업 폴백**: `--fallback-studio` 옵션을 추가하면 Vertex AI / GCS 권한 또는 할당량 문제 발생 시 자동으로 Google AI Studio(`GEMINI_API_KEY`)로 원활하게 전환되어 중단 없이 안전하게 작업을 완료합니다.
   - **실행 명령 예시**:
     ```bash
     # 기본: Google Cloud Vertex AI (ADC + GCS 캐싱, 기본값):
     python3 scripts/generate_edl.py -v output/multicam_merged_full.mp4

     # Google AI Studio 자동 폴백 활성화:
     python3 scripts/generate_edl.py -v output/multicam_merged_full.mp4 --fallback-studio
     ```

### 3A단계: FCP7 XML 타임라인 내보내기 (`export_fcp7_xml.py`)
- 전체 동기화 마스터 비디오와 `edl_full.csv`를 직접 링크하여 DaVinci Resolve / Premiere Pro / Final Cut Pro에 직접 로드 가능한 `final_cut_full.xml` 생성.
- **NTSC 부동소수점 프레임레이트 (29.97 / 23.976 / 59.94) 및 Drop-Frame (`--drop-frame`) 완벽 지원**:
  - `--fps` 부동소수점 입력을 지원하여 FCP7 XML 규격에 맞는 정수 `<timebase>` 및 `<ntsc>TRUE</ntsc>`를 자동 생성. 모든 타임코드 계산은 부동소수점을 사용하여 장편 타임라인 프레임 누적 오차를 원천 차단.
  - `--drop-frame` 지정 시 `<displayformat>DF</displayformat>` 설정 지원.
- **실행 명령어 예시**:
  ```bash
  # 표준 30 fps XML 내보내기:
  python3 scripts/export_fcp7_xml.py -i output/edl_full.csv -o output/final_cut_full.xml

  # 방송용 29.97 fps NTSC Drop-Frame XML 내보내기:
  python3 scripts/export_fcp7_xml.py -i output/edl_full.csv -o output/final_cut_full.xml --fps 29.97 --drop-frame
  ```

### 3B단계: 원패스 직접 렌더링 (`edl_to_video.py`)
- 중간 챕터 비디오 출력 및 병합 단계 없이, Apple Silicon `h264_videotoolbox`를 활용하여 동기화 마스터에서 `final_cut_full.mp4`를 단일 패스로 직접 렌더링.
- **실행 명령어 예시**:
  ```bash
  python3 scripts/edl_to_video.py -i output/edl_full.csv -o output/final_cut_full.mp4
  ```

### 4단계: YouTube 자막 생성 (`generate_subtitles.py`)
- **3단계 골든 자막 생성 파이프라인 (Three-Stage Pipeline)**:
  1. **전체 오디오 매크로 이해, 듀얼 트랙 용어집 및 Whisper Initial Prompt 추출**: Gemini 3.8 Flash(1M Context)로 전체 에피소드 오디오를 청취(인터뷰 개요 `--outline` 또는 녹음 원고/대본 `--script` 지원). Gemini 교정용 Markdown 용어집(`final_cut_full_glossary.md`)과 함께, 상단에 200 토큰(약 100~140자) 이내 고밀도 핵심 키워드 목록(`> **Whisper Initial Prompt**: ...`)을 자동 생성.
  2. **Whisper 물리 음향 타임코드 및 프롬프트 바이어스 주입**: 1단계의 `initial_prompt`를 로컬 Whisper(`mlx-whisper` / `faster-whisper`)에 주입하여 고유명사의 초동 인식률을 대폭 향상. 단어 수준 물리 음향 파형을 측정(`word_timestamps=True`)하여 0.000초 오차 없는 기준 타임라인 및 단어 캐시(`final_cut_full_raw_whisper.srt` & `final_cut_full_words.json`)를 생성 (재실행 시 수초 내 로드).
  3. **무음 감지 시맨틱 청킹, 마이크로 음향 스냅 및 멀티모달 오디오 교정**:
     - **무음 감지 시맨틱 청킹 (Silence-Aware Semantic Chunking)**: 고정 행 수 기계적 분할을 폐지하고, 자연스러운 호흡 휴지(Gap $\ge 0.4\text{s}$) 및 문장 종결 부호/어미에서 안전하게 분할.
     - **마이크로 음향 스냅 (Micro-Acoustic Sub-clause Snapping)**: 긴 문장 분할 시 Whisper 단어 물리 타임스탬프(`all_words`)에 흡착시켜 비례 배분으로 인한 입모양 불일치 배제.
     - **일본어 한자/가나 발음 동기화 규칙**: 일본어 발음을 구술한 경우 "한자(히라가나)"(예: `改札（かいさつ）`), 문맥상 단순히 언급된 경우 순수 한자(예: `出改札`)로 처리하며, 괄호 제거 대체 매칭으로 음향 탈락 방지.
     - **Gemini API 지수 백오프 및 지터 자동 재시도 (Exponential Backoff & Jitter)**: 동시 요청이나 속도 제한으로 인한 HTTP 429 (`RESOURCE_EXHAUSTED`), 503 / 500 에러 시 최대 5회 자동 재시도(`Retry-After` 자동 분석 및 지터 적용). 워커 간 충돌을 방지하여 교정되지 않은 원본 자막으로 조기 강등되는 현상 방지.
     - **청크 단위 영구 캐시 (Chunk-Level Persistent Cache)**: 모델, 프롬프트, 용어집, 텍스트 청크로부터 고유 해시를 생성하여 `.<basename>_chunk_cache.json`에 즉시 저장. 중단 시에도 토큰 낭비 없이 100% 재개 가능.
     - **플리커 방지 미세 간격 결합**: 미세한 간격($< 0.6\text{s}$)을 0s로 평활화, 진정한 휴지 시 $+0.4\text{s}$ 호흡 여백 후 화면을 깔끔히 클리어.
- **🎯 Netflix / YouTube 방송 표준 자막 품질 감사 엔진 (8대 핵심 검증 항목)**:
  - **1줄 글자 수 및 너비 제한**: 한국어 $\le 16$자, 중국어/일본어 $\le 15$자, 영어 $\le 42$ CPL (업계 표준). `--max-chars-cjk`, `--max-chars-korean`, `--max-chars-latin`으로 자유롭게 조정 가능.
  - **가독 속도 모니터링 (CPS)**: CJK $\le 6.0$ CPS, 영어 $\le 20.0$ CPS. 전체 평균 CPS 및 피크 CPS를 산출하고 Netflix 기준 초과 항목을 경고 목록에 등록.
  - **언어별 맞춤형 문장부호 정책**: CJK(한국어/중국어/일본어)는 쉼표를 공백으로 변환하고 문장 끝 부호를 100% 제거. Latin(영어/프랑스어/독일어 등)은 구문 내 쉼표, 마침표, 콜론, 세미콜론, 발화 중단 대시 `—`, 여운 말줄임표 `...`를 완벽 보존.
  - **다국어 폴백 메커니즘**: `zh-TW`, `zh-CN`, `ja`, `ko`, `en` 5대 언어 전용 어휘 프롬프트 제공. 미지원 언어는 영어 규칙으로 안전하게 폴백 (최초 1회 stderr 알림).
  - **타이포그래피 및 서식 정제**: 전각 `（）`, `【】`, `《》`, `「」` 및 반각 괄호 쌍 검증, 누출된 Markdown 태그(`**`, `_`, `` ` ``) 자동 제거.
  - **장시간 무음/무대화 구간 검사**: 10초 이상의 무음 구간(Gap $\ge 10.0\text{s}$)을 검출하여 B-roll, BGM 또는 ASR 음성 누락 확인용 전후 문맥 및 타임코드 기록.
  - **음성 시작 0.000초 물리 동기화**: Whisper 음향 파형 시작점에 엄격 고정(0.000s)하여 스포일러 방지.
  - **가독 시간 보호**: $1.0\text{s} \le \text{Duration} \le 6.0\text{s}$ (짧은 문장은 여백을 활용하여 $\ge 1.0\text{s}$ 확보).
  - **플리커 방지 미세 간격 결합**: $< 0.2\text{s}$ 간격을 0s로 평활화, $+0.4\text{s}$ 호흡 여백 확보.
- **실행 명령어 예시**:
  ```bash
  # 기본 실행 (Google Cloud Vertex AI & ADC 인증, 기본값):
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4

  # Google AI Studio 자동 폴백 활성화:
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 --fallback-studio

  # 녹음 원고/대본을 전달하여 용어 및 문맥 최적화:
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 --script manuscript.txt

  # 자막 한 줄 최대 글자 수 조정 (예: 영어 42자, CJK 15자):
  python3 scripts/generate_subtitles.py -i output/final_cut_full.mp4 --language en --max-chars-latin 42
  ```
- **출력 파일**:
  - `final_cut_full.srt`: YouTube 표준 SubRip 자막 파일.
  - `final_cut_full.vtt`: 웹 플레이어용 WebVTT 자막 파일.
  - `final_cut_full_subtitle_report.json`: 품질 감사 보고서 (JSON, 세부 통계 및 검토 필요 목록).
  - `final_cut_full_subtitle_report.md`: 품질 감사 시각화 카드 (Markdown, 적합 등급 및 무음 구간 목록).
  - `final_cut_full_glossary.md`: 에피소드 전체 용어집 (상단에 Whisper Initial Prompt 포함).
  - `final_cut_full_raw_whisper.srt`: Whisper 전사 초안.
  - `final_cut_full_words.json`: Whisper 단어 수준 물리 타임스탬프 캐시.

---

## 🛠️ 환경 요구사항 및 클라우드 설정

- **Google Antigravity IDE / Agent Framework**
- **FFmpeg** (`h264_videotoolbox` 하드웨어 인코딩 및 `loudnorm` 지원)
- **Python 3.8+** (`numpy`, `google-genai`, `google-cloud-storage`)

### 클라우드 인증 및 듀얼 백엔드 설정

본 도구는 **듀얼 백엔드 클라우드 아키텍처**를 지원합니다:

1. **Google Cloud Vertex AI (기본 백엔드, 권장)**:
   - Google Cloud ADC를 통한 간편 인증 (API 키 노출 방지):
     ```bash
     gcloud auth application-default login
     ```
   - `.env.example`을 `.env`로 복사하여 프로젝트 ID와 GCS 버킷 설정:
     ```bash
     cp .env.example .env
     ```
     ```env
     GOOGLE_CLOUD_PROJECT=sylph-demo-505906
     GCS_BUCKET=video-preprocessing-sylph-demo-505906
     GOOGLE_CLOUD_LOCATION=us-central1
     GEMINI_API_KEY=your_gemini_api_key_here
     ```
   - 스마트 SHA-256 로컬 해시 캐시로 대용량 비디오의 중복 업로드를 방지합니다.
2. **Google AI Studio (백업 / 단독 사용)**:
   - `--backend studio`를 지정하거나 `--fallback-studio`를 사용하여 Vertex AI 권한 부족 시 자동으로 Google AI Studio(`GEMINI_API_KEY`)로 전환합니다.
