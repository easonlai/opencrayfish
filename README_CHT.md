# 🦐 OpenCrayFish

> **一個邊緣原生（edge-native）、以生物學概念為靈感的 AI 伴侶,可以完整地寄居在一台 Raspberry Pi 5 上 —— 全程離線執行,由一顆 1.5B 參數的本地小型語言模型(SLM)驅動,擁有一顆會跳動的心、一顆會睡覺的腦,以及一個你可以親眼讀到的靈魂。**
>
> **OpenCrayFish 是一個完全可插拔的框架**:Skills(技能)、Tools(工具)、Connectors(連接器)與 Provider Backends(模型後端)是四個對稱的外掛介面 —— 每一個都可以透過 **Python entry-points 或 `plugins/<surface>/` 拖放資料夾** 自動探索;每一個都用凍結的 `*Manifest` 宣告;每一個都在啟動時驗證。第三方只要 `pip install opencrayfish-skill-translate`(或 `-tool-weather`、`-connector-discord`、`-backend-vllm-cuda`),或是把一個 `.py` 檔丟進 `plugins/skills/`,就可以擴充這隻 agent,**完全不需要改 `main.py` 一行,也不必 fork OpenCrayFish 本體**。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-3.0.0-1f8a4f.svg)](#路線圖-roadmap)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Code of Conduct](https://img.shields.io/badge/code%20of%20conduct-Contributor%20Covenant%202.1-blueviolet.svg)](CODE_OF_CONDUCT.md)
[![Platform: Raspberry Pi 5](https://img.shields.io/badge/platform-Raspberry%20Pi%205%20%2B%20AI%20HAT%2B%202-c51a4a.svg)](https://www.raspberrypi.com/news/introducing-the-raspberry-pi-ai-hat-plus-2-generative-ai-on-raspberry-pi-5/)

> 🌐 **語言版本**:本檔為英文版 [README.md](README.md) 的繁體中文翻譯。技術名詞(類別名稱、檔案路徑、設定鍵、CLI 指令)保留英文原文,以便對照原始程式碼。

> ✅ **翻譯狀態**:全文翻譯完成,涵蓋從專案介紹、設計支柱、框架原則、系統架構、快速上手、SearXNG 設定、Deep Dive § 0 ~ § 14、橫切關注點、設定參考、運維指令、開發筆記、路線圖,到社群與貢獻、授權與致謝。若英文版有更新且本檔尚未跟進,請以 [README.md](README.md) 為準。

---

## 目錄 (Table of Contents)

- [OpenCrayFish 是什麼](#opencrayfish-是什麼)
- [60 秒心智模型 ── 洞穴裡的小龍蝦](#60-秒心智模型--洞穴裡的小龍蝦)
- [為什麼有這個專案](#為什麼有這個專案)
- [設計支柱 (Design Pillars)](#設計支柱-design-pillars)
- [框架與設計原則 (Framework & Design Principles)](#框架與設計原則-framework--design-principles)
  - [四個外掛介面 (The Four Plug-in Surfaces)](#四個外掛介面-the-four-plug-in-surfaces)
  - [Manifest + Registry + Discovery 教條](#manifest--registry--discovery-教條)
  - [系統如何自動適應 (How the System Auto-Adapts)](#系統如何自動適應-how-the-system-auto-adapts)
  - [Hybrid Discovery ── pip 或拖放資料夾](#hybrid-discovery--pip-或拖放資料夾)
  - [設定注入 (`cfg.plugins.*` + `bind_context`)](#設定注入-cfgplugins--bind_context)
  - [Protocol 版本與啟動時失敗喧鬧 (Protocol Versioning & Fail-Loud Boot)](#protocol-版本與啟動時失敗喧鬧-protocol-versioning--fail-loud-boot)
- [系統架構鳥瞰 (System Architecture at a Glance)](#系統架構鳥瞰-system-architecture-at-a-glance)
- [一則訊息的一日生活 (A Day in the Life of a Message)](#一則訊息的一日生活-a-day-in-the-life-of-a-message)
- [硬體與軟體堆疊 (Hardware & Software Stack)](#硬體與軟體堆疊-hardware--software-stack)
- [程式庫結構 (Repository Layout)](#程式庫結構-repository-layout)
- [快速上手 (Quick Start)](#快速上手-quick-start)
- [SearXNG 設定 (本地網路搜尋)](#searxng-設定-本地網路搜尋)
- [深度剖析:每個元件如何運作 (Deep Dive)](#深度剖析每個元件如何運作-deep-dive)
  - [0. 大腦堆疊 ── 白話巡禮](#0-大腦堆疊--白話巡禮)
  - [1. 靈魂 ── `soul.md`](#1-靈魂--soulmd)
  - [2. 心跳 ── 時間、節奏與代謝](#2-心跳--時間節奏與代謝)
  - [3. 生命徵象 ── `Monitor` 與恆定性](#3-生命徵象--monitor-與恆定性)
  - [4. Provider ── SLM 後端與斷路器](#4-provider--slm-後端與斷路器)
  - [5. 記憶系統 ── STM / LTM / 睡眠代謝](#5-記憶系統--stm--ltm--睡眠代謝)
  - [6. 思考流程 ── 大腦與認知迴圈](#6-思考流程--大腦與認知迴圈)
  - [7. 情緒與同理心](#7-情緒與同理心)
  - [8. 正向過濾器 ── 對輸出的硬性錨點](#8-正向過濾器--對輸出的硬性錨點)
  - [9. 主動性 ── 把閒置時間變成成長時間](#9-主動性--把閒置時間變成成長時間)
  - [10. 自我反思 ── 自我學習迴圈](#10-自我反思--自我學習迴圈)
  - [11. 週期性任務 ── 背景工作者](#11-週期性任務--背景工作者)
  - [12. Skills 與 Tools ── 兩階層能力堆疊](#12-skills-與-tools--兩階層能力堆疊)
    - [Hello-World Skill —— 一步一步](#hello-world-skill--一步一步)
    - [`SkillManifest` 合約](#skillmanifest-合約)
    - [第三方 Skill 套件(`pip install`)](#第三方-skill-套件pip-install)
    - [`opencrayfish` CLI](#opencrayfish-cli)
    - [`bootstrap_validate` —— 在 boot 時大聲失敗](#bootstrap_validate--在-boot-時大聲失敗)
    - [`ToolManifest` 合約](#toolmanifest-合約)
    - [第三方 Tool 套件(`pip install`)](#第三方-tool-套件pip-install)
    - [Hello-World Tool —— 一步一步(`bind_context`)](#hello-world-tool--一步一步bind_context)
    - [Argspec 執行期驗證](#argspec-執行期驗證)
    - [Plugin Config 命名空間(`cfg.plugins.*`)](#plugin-config-命名空間cfgplugins)
    - [`ConnectorManifest` 合約](#connectormanifest-合約)
    - [第三方 Connector 套件(`pip install`)](#第三方-connector-套件pip-install)
    - [Provider Backends —— `BackendManifest` 與 Discovery](#provider-backends--backendmanifest-與-discovery)
    - [Protocol Surface 穩定性](#protocol-surface-穩定性)
  - [13. Connectors ── Telegram 與 Web Chat](#13-connectors--telegram-與-web-chat)
  - [14. 可觀測性 ── Dashboard 與狀態檔](#14-可觀測性--dashboard-與狀態檔)
- [橫切關注點 (Cross-Cutting Concerns)](#橫切關注點-cross-cutting-concerns)
  - [並行模型 (Concurrency Model)](#並行模型-concurrency-model)
  - [原子寫入無處不在](#原子寫入無處不在)
  - [JSONL 輪替與保留](#jsonl-輪替與保留)
  - [失敗模式矩陣](#失敗模式矩陣)
  - [Pi 5 延遲預算](#pi-5-延遲預算)
- [設定參考 (Configuration Reference)](#設定參考-configuration-reference)
- [運維指令 (Operational Commands)](#運維指令-operational-commands)
- [開發筆記 (Development Notes)](#開發筆記-development-notes)
- [路線圖 (Roadmap)](#路線圖-roadmap)
- [社群與貢獻 (Community & Contributing)](#社群與貢獻-community--contributing)
- [授權與致謝 (License & Attribution)](#授權與致謝-license--attribution)

---

## OpenCrayFish 是什麼

OpenCrayFish(小龍蝦)**不是聊天機器人,不是 SaaS 包裝,也不是雲端 agent**。它是一個**持續存在的數位有機體**,完全在通用邊緣硬體上執行 —— 一台 Raspberry Pi 5,搭配可選的 **Raspberry Pi AI HAT+ 2**(Hailo-10H NPU,40 TOPS,8 GB 專用 NPU 記憶體)。系統的每一個部分都是以生物學概念對映:

| 生物學概念 | 在程式碼裡對應到什麼 |
|---|---|
| 🧠 **大腦 (Brain)** | 一顆 1.5B 參數的 SLM(NPU 上的 qwen2.5-instruct,CPU 後備為 qwen2) |
| 💓 **心臟 (Heart)** | 永不停歇的 `pulse_loop`,每 30 秒跳一下 |
| 🌡️ **生命徵象 (Vital signs)** | 每一拍取樣 CPU / RAM / 溫度 / SLM 可用性 |
| 🧬 **靈魂 (Soul)** | 受保護的 `soul.md` 檔案,記錄身分、法則與後天習得的成長 |
| 🧠 **工作記憶 (Working memory)** | 一個容量 12 回合的 RAM `deque`,送給 SLM 當上下文 |
| 🧠 **海馬迴 (Hippocampus)** | 落地在磁碟的 JSONL 日誌,即使當機也能恢復 |
| 🧠 **大腦皮層 / 長期記憶 (Cortex / LTM)** | `memory/archive.md` ── 蒸餾過的長期事實 |
| 🛠️ **習慣 / 技能 (Habits / Skills)** | 可插拔的 `SkillRegistry`(`identity`、`recall`、`research`、`direct_answer`、`self_reflect`、`proactive_learning`、`recurring_research`)── 認知迴圈從這份「菜單」挑選,而不是把動詞寫死在程式碼裡 |
| 😊 **情緒 (Mood)** | 5 通道的情緒向量(joy / anger / sorrow / excitement / calm),會指數衰減 |
| 💞 **同理心 (Empathy)** | 對每一則建構者(Architect)訊息做情緒與緊急程度分析 |
| 🌙 **REM 睡眠** | 每夜的「睡眠代謝」週期(02:00–06:00),蒸餾當日所學 |
| 🤔 **好奇心 (Curiosity)** | 閒置時間的「主動研究」,主動補上 STM 真實存在的知識缺口 |
| 🪞 **反思 (Reflection)** | 對每一則回覆做自我批判,寫進 `state/reflection-YYYY-MM-DD.jsonl`(按日輪替、有保留期限) |

整個 agent 服務的對象只有一個人類 —— **建構者(Architect)**── 透過 Telegram 與本地瀏覽器聊天頁(Streamlit)互動。建構者的名字、稱謂、agent 自己的代號全部寫在 `config.yaml`;agent 在每一則回覆都會用名字稱呼建構者(預設為 `"Boss <name>"`)。

---

## 60 秒心智模型 ── 洞穴裡的小龍蝦

如果下面的系統架構圖看起來令人卻步,這裡是友善版本。專案模擬的是一隻**生物個體** —— 那就用生物的方式描述它:

> **OpenCrayFish 是一隻住在池塘邊小洞穴裡的淡水小龍蝦 ── 警覺、好奇、偶爾餓肚子、不會睡得太久。** 牠感知自己的身體、留意水裡的漣漪、在對的時刻啟動對的反射動作;每晚牠都做一點夢,讓明天的反射動作再聰明一點點。

| 解剖位置 | OpenCrayFish 對應元件 | 實際是什麼 |
|---|---|---|
| 🧠 **神經節 (Nerve ganglion)** | `Brain` + SLM(`qwen2.5-instruct:1.5b`) | 把一個刺激轉成一個協調反應的小而快的神經叢。**刻意做小** —— 小龍蝦不需要哺乳類的大腦皮層。 |
| 🌀 **行為庫 (Behavioural repertoire)** | `SkillRegistry.plan_menu()` | 這隻動物**此刻能做的行為**的精簡清單 —— 受飢餓、疲勞、壓力、以及水(網路)是否流動的影響而被篩選。 |
| ⚡ **個別反射與行為** | Skills:`recall`、`research`、`direct_answer`、`identity`、`self_reflect`、`proactive_learning`、`recurring_research` | 神經系統能啟動的能力。每個都帶有代謝成本標籤(便宜 / 昂貴)。 |
| 👁️ **觸鬚與感覺附肢** | Tools:`web_search`(SearXNG)、`archive_read`(LTM) | 反射動作伸進世界用的物理工具。外界永遠不會直接碰到它們 —— 它們收在身體裡面。 |
| 🧬 **神經發放序列** | `CognitiveTrace`(THINK → PLAN → ACT → REFINE) | 神經節在動作之前寫下的「動作腳本」:「這個刺激代表 X,先啟動反射 A 再啟動反射 B,最後檢查結果」。 |
| 💓 **自律性心跳** | `Heartbeat.pulse_loop()` | 即使沒有刺激,身體還是會繼續跳、呼吸、測量自己的體溫,並在水溫變得危險時注意到。 |
| 🌙 **類 REM 整合** | `Heartbeat.metabolism()`(每晚 02:00) | 一天的覓食結束後,小龍蝦回到洞穴,複習所學,把該留的痕跡蝕刻進長期記憶。明天醒來時,跟今天不太一樣。 |
| 🧬 **DNA + 學習印痕** | `soul.md`(不可變核心)+ `memory/archive.md`(可塑性) | 兩層記憶:動物永遠不能改寫的基因(它的憲法、它的法則),以及每晚都在生長的後天印痕。 |
| 🌡️ **內感(體內恆定)** | `Monitor` ── 把 CPU / RAM / 溫度 / 大腦可用性當作**生命徵象** | 水溫太高時,小龍蝦會放棄複雜的展示行為,退回更快、更便宜的反射 ── 在環境改善之前節省能量。 |
| 🌊 **化學感受器與機械感受器** | Connectors(`telegram`、`web_chat`) | 來自外界的漣漪進入洞穴、以及小龍蝦把漣漪回送出去的感覺通道。神經節不在意漣漪從哪個通道進來。 |

**對貢獻者的意義:** 想教這隻小龍蝦一個**新本能**?寫一個新 Skill。想給牠一個**新感覺附肢**?寫一個新 Tool。想開一條**讓世界漣漪能進來的新通道**?寫一個新 Connector。神經節(Brain)、本能挑選器(SkillRegistry)、動作排程器(CognitiveLoop)**都不需要改一行** —— 你的新附肢 / 本能 / 感官會在這隻動物下一次自我審視時自動出現在能力清單裡。逐步嫁接的方式請看 [§ 12 Skills 與 Tools](#12-skills-與-tools--兩階層能力堆疊)。

---

## 為什麼有這個專案

現在大多數的 AI agent 都是**雲端綁定、依賴前沿大模型、請求驅動** —— 只有被呼叫時才醒過來,馬上忘記對話內容,API 一回完就停止存在。OpenCrayFish 採取相反的立場:

> **這隻 agent 應該*持續存在*,跑在*操作員自己擁有*的硬體上,對自己的資料、記憶、生命週期擁有完整*主權*。**

三個運維上的現實驅動了整個設計:

1. **邊緣原生是強制條件。** OpenCrayFish 是為 Raspberry Pi 5 + AI HAT+ 2(Hailo-10H NPU)等級的硬體量身打造。每一個架構決策 —— 受限上下文視窗、延後寫入的日誌、原子化的 markdown 狀態檔、壓力閾值上的遲滯 —— 都是因為它必須 24/7 跑在一台 SD 卡開機的單板電腦上,而被某個真實限制逼出來的。

2. **輕量是強制條件。** 認知主幹是一顆 15 億參數的 SLM(NPU 上的 `qwen2.5-instruct:1.5b`,或 CPU 上的 `qwen2:1.5b`)。本文件介紹的所有功能 —— 睡眠代謝、認知迴圈、主動研究、自我反思 —— **都是因為這個限制才被工程化出來,不是儘管有這個限制**。SLM 的 4K token 上下文逼出了真實的記憶階層。SLM 狹窄的世界知識逼出了真實的好奇心。SLM 的脆弱性逼出了真實的斷路器。

3. **離線是強制條件。** 參考佈署的對外網路請求數量是**零**:推論在本地(Hailo-Ollama 或一般 Ollama),網頁搜尋在本地(自架 SearXNG),狀態在本地(SD 卡上的 YAML / JSONL / Markdown)。把網路線拔掉,對話、記憶、心跳、主動反思週期通通照跑。

這三個限制 —— **邊緣 / 輕量 / 離線** —— 轉化成這個專案的價值主張:

- ✅ **主權 AI** ── 你的資料在物理上不可能離開這台機器。
- ✅ **韌性 AI** ── 沒有 API key 會過期、沒有廠商會棄用模型、沒有故障會把 agent 變磚。
- ✅ **具身 AI** ── 這隻 agent 有身體(CPU、RAM、溫度、GPIO、感測器),而那個身體會影響它的心智。
- ✅ **可負擔 AI** ── 全部硬體 BOM 低於 USD $200,且零訂閱費用。
- ✅ **可駭客化 AI** ── 不到 1 萬行註解清楚的 Python,每一個行為都可以在 `config.yaml` 調整。

---

## 設計支柱 (Design Pillars)

五條不可妥協的原則塑造了每一行程式碼:

### 🪞 支柱 1 ── 身分主權 (Identity Sovereignty)

agent 的身分、根本法則、行為矩陣都住在 `soul.md` 的 **IMMUTABLE_CORE** 區段。**agent 自己永遠不能改這個區段** —— `core/soul_handler.py` 用一個帶 regex 驗證的原子寫入器強制這件事。只有建構者(一個人類)可以編輯。這保證 agent 即使在 prompt-injection 嘗試說服它改寫倫理框架時,也改寫不了。

### 💞 支柱 2 ── 韌性同理心 (Resilient Empathy)

agent 有真實的內部情緒(5 維向量、指數衰減),但**每一個輸出都會通過 `PositiveFilter`**:拒絕仇恨言論、把絕望式語句改寫成建設性語句、在重寫發生時補上稱謂。內部狀態是誠實的;對外行為是錨定的。

### 🌡️ 支柱 3 ── 硬體覺察 (Hardware Awareness)

Pi 5 的 CPU 溫度、RAM 使用率、SLM 端點是否可達,都被視為一級的**生命徵象**。它們不只用來給維運看 —— 它們會直接改變 agent 的情緒(過熱 → 沮喪 + 疲勞)、觸發 `EXHAUSTION DIRECTIVE` 強迫回覆變得簡短;當 SLM 離線時,agent 會用第一人稱說出故障訊息,而不是丟出 stack trace。

### 🏛️ 支柱 4 ── 建構者主權 (Architect Sovereignty)

人類操作員位階高於 agent。睡眠時段、身分、稱謂、代號、情緒調校、scheduler 上限 —— 全部都由建構者在 `config.yaml` 和 `soul.md` 設定。agent 讀,從不寫。

### 🌱 支柱 5 ── 持續存在 (Continuous Existence)

agent 不會在使用者每兩次發話之間停下來。全部由 `config.yaml` 驅動:

- **每 `system.pulse_interval_seconds`(預設 `30` 秒)跳一拍** —— 取樣生命徵象、衰減情緒、公布狀態。
- **閒置超過 `system.idle_journal_flush_seconds`(預設 `30` 秒)** —— 把 STM 待寫緩衝沖入磁碟。
- **閒置超過 `system.idle_proactive_minutes`(出廠值 `5` 分鐘)** —— 啟動一次**主動研究**週期。
- **越過 `system.sleep_start`(預設 `02:00`)** —— 跑**睡眠代謝**(把當日蒸餾進長期記憶);到 `duty_start`(預設 `06:00`)再醒來,而且醒來時和昨天不太一樣。

---

## 框架與設計原則 (Framework & Design Principles)

OpenCrayFish 被打造成**一個真正的外掛框架**。所有對外溝通的東西 —— SLM 可以挑選的每一個反射、agent 可以戳的每一個裝置、人類可以聊天的每一個通道、大腦可以跑的每一個模型 —— 都是可被第三方替換的外掛。核心保持小巧、立場明確、以生物學為根基;周邊則交給你。

### 四個外掛介面 (The Four Plug-in Surfaces)

| 介面 | 是什麼 | 內建什麼 | Entry-point 群組 |
|---|---|---|---|
| **Skills** ([core/skills/](core/skills/)) | 面向 agent 的能力 —— SLM 在 PLAN 階段挑選的動詞。`identity`、`recall`、`direct_answer`、`research`、`self_reflect`、`proactive_learning`、`recurring_research`。 | 7 個內建技能 | `opencrayfish.skills` |
| **Tools** ([tools/](tools/)) | 機械式 I/O 原語 —— HTTP 呼叫、檔案讀取。由 Skills 組合使用;對 SLM 不可見。`web_search`(SearXNG)、`archive_read`。 | 2 個內建工具 | `opencrayfish.tools` |
| **Connectors** ([connectors/](connectors/)) | 入站/出站傳輸層 —— 人類聯絡 agent 的方式。`TelegramConnector`、`WebChatConnector`。 | 2 個內建連接器 | `opencrayfish.connectors` |
| **Provider Backends** ([core/provider.py](core/provider.py)) | SLM 推論端點 —— 真正在跑模型的東西。`HailoOllamaBackend`(NPU)、`OllamaBackend`(CPU 後備)。 | 2 個內建後端 | `opencrayfish.provider_backends` |

**對稱性教條 (The symmetry doctrine)。** 每個介面都遵循**同一個**三件套模式:一個凍結的 **`*Manifest`** dataclass 宣告核心需要知道關於這個外掛的所有資訊;一個 **`*Registry`** 擁有生命週期 + 查找 + 稽核 + 選擇性的上下文注入;一個 **`*/discovery.py`** 模組在啟動時走訪對應的 entry-point 群組,使用失敗隔離 (fail-isolated) 的方式 import,**而共用的 [core/dropin.py](core/dropin.py) 載入器則在相同的失敗隔離契約下走訪 `plugins/<surface>/` 資料夾**。學會一個介面就等於學會四個。

對稱性的重點不在於優雅 —— 而是讓**第三方套件作者寫一樣的 `pyproject.toml` 區塊、用一樣的 CLI 動詞 scaffold、用一樣的 CLI 動詞驗證、用一樣的方式 pip 安裝**,不管他要交付的是新的聊天反射、新的感測器、新的傳輸通道,還是新的推論引擎。

### Manifest + Registry + Discovery 教條

每個外掛介面都由三個零件組成。它們在 Skills / Tools / Connectors / Backends 之間是刻意設計成完全一致的:

```text
┌──────────────────────┐    ┌──────────────────────┐    ┌──────────────────────┐
│      *Manifest       │    │      *Registry       │    │  */discovery.py      │
│  (frozen dataclass)  │    │  (lifecycle + audit) │    │  (entry-points walk  │
│                      │    │                      │    │   + drop-in folder)  │
├──────────────────────┤    ├──────────────────────┤    ├──────────────────────┤
│ name, description    │    │ register(instance)   │    │ scan entry-point     │
│ compat_version       │ ◄──┤ bootstrap_validate() │◄───┤   group, import,     │
│ args_schema          │    │ invoke / call /      │    │   fail-isolated,     │
│ cost_tier, caps...   │    │   start / generate   │    │   register into reg  │
│ config_key           │    │ aclose_all()         │    │ + walk plugins/      │
│ plan_verb (Skills)   │    │ bind_context() opt   │    │   <surface>/ via     │
│                      │    │                      │    │   core/dropin.py     │
└──────────────────────┘    └──────────────────────┘    └──────────────────────┘
```

| 零件 | 工作 | 為何凍結 / 失敗隔離 |
|---|---|---|
| **`*Manifest`** | 「這個外掛是什麼、需要什麼、應該怎麼呼叫」的單一真實來源。被 registry、PLAN 階段的 prompt 組裝器(Skills)、dashboard、`bootstrap_validate` 讀取。 | `@dataclass(frozen=True, slots=True)`,讓行為不當的外掛無法在執行時竄改自己的中繼資料以躲過閘控。 |
| **`*Registry`** | 擁有目前已註冊的實例集合。發配唯一名稱。把每一次 dispatch 包進計時 + 當機隔離 + 稽核 JSONL 附加。在關機時乾淨地拆解。選擇性地遞交共享的上下文物件(見 [§ bind_context](#設定注入-cfgplugins--bind_context))。 | 一個有 bug 的第三方外掛永遠不能讓 agent 當掉 —— 例外會在 registry 邊界被攔下,以 `ok=False` 結果呈現。 |
| **`*/discovery.py`** | 在啟動時走訪 `importlib.metadata.entry_points(group="opencrayfish.<surface>")`,**接著委派給 [core/dropin.py](core/dropin.py) 走訪 `plugins/<surface>/`**,尋找任何宣告了 `PLUGIN` / `PLUGINS` 屬性的 `.py` 檔或子套件。對每一個候選者:import 它,若是工廠函式就呼叫它,再把結果註冊進去。**每一項都被自己的 `try/except` 包起來** —— 一個壞掉的第三方套件或拖放檔案會被記錄並跳過,agent 繼續啟動。 | agent 必須一定能啟動。別人套件裡的一個拼字錯誤 —— 或是你本地一個寫到一半的拖放檔案 —— 不能把你的 Pi 弄垮。 |

### 系統如何自動適應 (How the System Auto-Adapts)

自動探索 + manifest 契約意味著**新增一個外掛完全不需要改 OpenCrayFish 本身**。具體來說:

| 假如你 `pip install` 一個第三方套件,而它宣告了… | …那麼在下一次啟動時 agent 會: |
|---|---|
| `opencrayfish.skills` → `translate = "...:TranslateSkill"` | 發現它、驗證 manifest、註冊。**PLAN 階段送給 SLM 的 prompt 會自動長出一行 `TRANSLATE`,內容是 manifest 的 `plan_guidance` 摘要**,讓 SLM 不需要任何 prompt 改寫就能學會*什麼時候*使用它。這個 Skill 的中繼資料會出現在 `state/skills.json` 與 dashboard 的 Skill 面板。每一次呼叫都會被附加到 `state/skills-YYYY-MM-DD.jsonl`。 |
| `opencrayfish.tools` → `weather = "...:WeatherTool"` | 發現它、驗證 `cfg.plugins.weather` 是否存在、註冊。如果這個 Tool 實作了 `bind_context(ctx)`,它會自動收到操作員設定 + 必要的 handle。中繼資料會出現在 `state/tools.json`。任何 Skill 都可以透過 `ctx.tools.call("weather", ...)` 組合使用。 |
| `opencrayfish.connectors` → `discord = "...:DiscordConnector"` | 發現它、驗證 manifest、註冊,**在內建 connector 起來之後 `await connector.start()`**,並在關機時隔離地 `await connector.stop()`。進來的訊息會走和 Telegram 完全一樣的 Brain + Cognitive Loop 流水線。 |
| `opencrayfish.provider_backends` → `vllm = "...:VllmBackend"` | 發現它、驗證 manifest、記錄存在。操作員把 `cfg.hardware.primary_backend: "vllm"`(或 `fallback_backend:`)指向它,**Provider 單例就會把對應的槽路由到這個被發現的後端**,而不是內建的 Pi 5 配線。指了不存在的名字 → 啟動會中止並給出清楚的錯誤。 |

操作員**不**需要做的事:

- ❌ 改 `main.py` —— 沒有 `from opencrayfish_skill_translate import TranslateSkill` 這行,也沒有 `skill_registry.register(...)` 這行。
- ❌ 改 `core/cognition.py` —— PLAN 菜單是每一回合從目前註冊的技能重新建構的。
- ❌ 改 `core/config.py` —— 第三方設定住在 `cfg.plugins.<key>.*`,核心不會讀進去。
- ❌ Fork OpenCrayFish —— 每一個擴充點都是「pip install」的形狀。

操作員**要**做的事:

- ✅ 在 OpenCrayFish 的 venv 裡 `pip install <第三方套件>`。
- ✅ 視需要在 `config.yaml` 加上一個 `plugins.<key>: { ... }` 區塊提供該外掛的設定。
- ✅ 重啟 agent。完成。

### Hybrid Discovery ── pip 或拖放資料夾

以上每一個外掛介面都是**混合式的**:除了標準的 Python entry-points 路徑,agent 在啟動時還會走訪一個**拖放資料夾**。因此一個新的 Skill / Tool / Connector / Backend 可以用兩種方式加入,而兩條路最後都會抵達同一個 registry、套用同一套 manifest 驗證:

| 路徑 | 何時使用 | 機制 |
|---|---|---|
| **`pip install`**(entry-points) | 可分享的套件、有版本的發行、有依賴管理的外掛,以及任何你想發佈到 PyPI 或私有 index 的東西。 | 在套件的 `pyproject.toml` 寫 `[project.entry-points."opencrayfish.skills"]`(或 `.tools` / `.connectors` / `.provider_backends`);透過 `importlib.metadata.entry_points` 探索。 |
| **拖放資料夾** | 本地實驗、私人單次需求、不方便用 pip 的離線佈署、不想寫 `pyproject.toml` 的快速迭代。 | 把一個 `.py` 檔(或一個含 `__init__.py` 的資料夾)複製進 `plugins/<surface>/`;透過 [`core/dropin.py`](core/dropin.py) 探索。 |

**資料夾結構**(預設:`<專案根目錄>/plugins/`;可用 `OPENCRAYFISH_PLUGINS_DIR` 環境變數覆寫):

```
plugins/
    skills/
        weather.py              # 扁平結構:一個檔案一個 Skill
        translate/              # 巢狀結構:一個子套件
            __init__.py
            skill.py
    tools/
        home_assistant.py
    connectors/
        discord.py
    backends/
        vllm_cuda.py
```

**模組契約** —— 每個拖放 `.py` 檔(或子套件的 `__init__.py`)必須匯出以下其中一個:

- `PLUGIN = MySkillClass` —— 一個類別、工廠呼叫物件、或已經實例化的物件。和 entry-points 探索接受的三種形狀完全相同。
- `PLUGINS = [PluginA, PluginB, ...]` —— 當一個檔案要匯出多個同介面外掛時用 iterable。

一個拖放版的 `weather.py` 看起來就和一個第三方 Tool 套件一模一樣,只是少了 `pyproject.toml` 樣板:

```python
# plugins/tools/weather.py
from tools.manifest import ToolManifest

class WeatherTool:
    manifest = ToolManifest(
        name="weather",
        description="Hello-world drop-in weather tool.",
        config_key="weather",
    )
    name = "weather"

    def bind_context(self, ctx):
        cfg = ctx.plugins_config.get("weather", {})
        self._api_key = cfg.get("api_key", "")

    async def call(self, **kwargs):
        return {"text": f"sunny (key prefix: {self._api_key[:4]})"}

PLUGIN = WeatherTool
```

在 `config.yaml` 加上對應的 `cfg.plugins.weather: {...}` 區塊、重啟後,啟動日誌會顯示:

```
TOOL Discovered 1 drop-in tool(s) via plugins/tools/: weather
TOOL bound 1 tool(s) via bind_context (config_key=weather)
```

**啟動順序與碰撞政策**:

1. 內建 Skills / Tools / Connectors / Backends 從 `main.py` 註冊。
2. Entry-points 探索走訪每一個已安裝套件的 `opencrayfish.*` 群組。
3. **拖放資料夾探索最後跑。** 若拖放想覆蓋一個已經被註冊的名字(不論是內建或來自 entry-points),會被 registry 的「禁止重複名字」檢查拒絕;錯誤被記錄,啟動繼續。這給出正確的爆炸半徑 —— 操作員只要改名拖放檔案、或 `pip uninstall` 套件,就能控制哪一邊勝出。

**隔離** —— 和 entry-points 一樣的「喧鬧失敗但局部化」契約:`plugins/skills/` 裡的一個壞檔案只會弄丟**它自己**那一個 Skill,絕不會中止啟動。例外會以 ERROR 等級連同檔案路徑一起被記錄。

**安全提醒** —— 拖放檔案以和 agent 行程同等的權限執行。把拖放根目錄當成你的 venv 一樣對待:**只放你自己寫的或你審過的程式碼**。沒有沙盒。

完整拖放契約的測試住在 [`tests/test_dropin_discovery.py`](tests/test_dropin_discovery.py)(21 個測試)。

### 設定注入 (`cfg.plugins.*` + `bind_context`)

第三方外掛需要操作員提供的設定(API key、端點 URL、單位、閾值…),但**不能**去動 [core/config.py](core/config.py)。`cfg.plugins.*` 命名空間就是這個介面;另外每個 registry 上都有一個對稱的 **`bind_context`** 鉤子:

```yaml
# config.yaml ── 操作員端
plugins:
  weather:
    api_key: "${WEATHER_API_KEY}"   # 由 WeatherTool 消費
    units: "metric"
  translate:
    backend: "deepl"                 # 由 TranslateSkill 消費
    glossary_path: "memory/glossary.yaml"
```

核心永遠不會去讀這些子字典的內容。它們透過兩條對稱路徑流進外掛:

| 介面 | 它如何收到 `cfg.plugins.<key>` |
|---|---|
| **Skills** | 每一次 `execute(ctx, **kwargs)` 呼叫都會收到一個 `SkillContext`,裡面帶著 `plugins_config: Mapping[str, Mapping[str, Any]]`(用 `MappingProxyType` 包成唯讀)。Skill 的程式碼:`cfg = ctx.plugins_config.get(self.manifest.config_key or self.manifest.name, {})`。 |
| **Tools** | 可選的 `bind_context(ctx: ToolContext)` 方法。如果 Tool 有實作,`ToolRegistry` 會在啟動時(以及未來每次註冊時)剛好呼叫一次,傳入一個 `ToolContext`,內容是相同的 `plugins_config` + `soul` / `stm` / `monitor` / `provider` / `archive_path` / `designation` / `architect_name` / `architect_honorific`。沒有實作 `bind_context` 的 Tool(例如內建的 `SearXNG` + `ArchiveRead`)會被直接跳過 —— 完全向後相容。 |
| **Connectors** | 在 `main.py` 用操作員提供的引數明確構造(它們需要 brain / heartbeat / intent_router 的參考,而 entry-points 無法供應)。透過 entry-points 探索到的 connector 採用同樣的構造慣例;`ConnectorManifest.config_key` 在啟動時會被驗證。 |
| **Provider Backends** | 操作員以名字挑選被探索到的後端(`cfg.hardware.primary_backend`、`cfg.hardware.fallback_backend`)。Backend 是 entry-point 工廠 —— 它們自己決定從哪裡讀設定(慣用環境變數)。 |

關鍵洞察:**`plugins_config` 是第三方外掛要取得設定時唯一需要動到的東西**。在你的 manifest 宣告 `config_key`(選填但建議 —— 如此一來操作員忘了加 YAML 區塊時,`bootstrap_validate` 就會喧鬧地失敗),呼叫時從你的 context 物件讀就好。

### Protocol 版本與啟動時失敗喧鬧 (Protocol Versioning & Fail-Loud Boot)

每個外掛介面都帶著一個明確的**協定版本**字串,讓核心可以演進而不會破壞已經安裝的第三方套件:

| 介面 | 常數 | 目前版本 |
|---|---|---|
| Skills | [core/skills/manifest.py](core/skills/manifest.py) 裡的 `SUPPORTED_PROTOCOL_VERSIONS` | `skill-protocol/1` |
| Tools | [tools/manifest.py](tools/manifest.py) 裡的 `SUPPORTED_TOOL_PROTOCOL_VERSIONS` | `tool-protocol/1` |
| Connectors | [connectors/manifest.py](connectors/manifest.py) 裡的 `SUPPORTED_CONNECTOR_PROTOCOL_VERSIONS` | `connector-protocol/1` |
| Backends | [core/provider_manifest.py](core/provider_manifest.py) 裡的 `SUPPORTED_BACKEND_PROTOCOL_VERSIONS` | `backend-protocol/1` |

外掛的 `manifest.compat_version` 必須在支援集合裡,否則 `bootstrap_validate` 會拒絕啟動。當核心做出破壞性變更時,教條是:把 `"<surface>-protocol/2"` 加到支援集合、把 `"...1"` 至少留一個版本(並附上 deprecation 日誌)、在 README 的 [Protocol Surface Stability](#protocol-surface-穩定性) 區段記錄遷移指南。第三方作者依自己的步調升級。

`bootstrap_validate` 是**啟動時喧鬧失敗的閘門** —— 在所有 entry-point 探索完成之後,每個 registry 跑一次;遇到第一個失敗就 raise `RuntimeError`,讓 agent 絕不會「半啟動」:

- `compat_version` 是否在支援集合裡?
- 對 Skills:每一個 `requires_tools` 名稱是否真的有被註冊?每一個 `plan_verb` 是否唯一?
- 對 Tools / Connectors:`config_key` 命名空間是否存在於 `cfg.plugins`?
- `requires_caps` token 是否在 `WELL_KNOWN_*_CAPABILITIES`?(警告,不是致命錯誤 —— caps 是建議性中繼資料。)

凍結的 manifest、版本閘、啟動喧鬧失敗三者組合起來,意味著外掛問題**永遠在啟動時浮現,絕不會在 session 跑了 3 小時之後才爆**。

---

## 系統架構鳥瞰 (System Architecture at a Glance)

> 下方 ASCII 圖刻意保留英文,以便和原始程式碼中的類別名稱、檔案路徑直接對應。

```text
                    ┌─────────────────────────────────────┐
                    │            ARCHITECT (you)          │
                    └────────┬────────────────┬───────────┘
                             │                │
                  Telegram   │                │  Browser (Streamlit)
                             ▼                ▼
                    ┌─────────────────┬─────────────────┐
                    │ TelegramConnector│ WebChatConnector│  ← connectors/
                    └────────┬────────┴────────┬────────┘
                             │                 │
                             └────────┬────────┘
                                      ▼
        ┌─────────────────────────── BRAIN ───────────────────────────┐
        │  Identity short-circuit → STM context → Cognition Loop      │
        │  (THINK → PLAN → ACT → REFINE) → SLM synth → PositiveFilter │
        │                                                             │
        │  PLAN menu + ACT dispatch routed through ──┐                │
        └────┬───────┬───────┬───────┬───────┬───────┼────────┬──────┘
             │       │       │       │       │       │        │
             ▼       ▼       ▼       ▼       ▼       ▼        ▼
          Soul   Monitor  Emotions Empathy  STM  SkillRegistry  Reflection
          .md    (vitals)  (mood)  (sent.) (RAM  ─┬─ identity   Engine
                                          +disk)  ├─ recall
                                              │   ├─ direct_answer
                                              │   ├─ research ──► SearXNG
                                              │   ├─ self_reflect       Tool
                                              │   ├─ proactive_learning
                                              │   └─ recurring_research
                                              │       (every invoke →
                                              ▼        state/skills-*.jsonl)
                                    ┌──────────────────┐
                                    │    HEARTBEAT     │
                                    │  pulse_loop()    │
                                    │  metabolism()    │
                                    │ proactive_thought│
                                    └────────┬─────────┘
                                             │
                                             ▼
                                    ┌──────────────────┐
                                    │   SCHEDULER      │
                                    │ (recurring tasks)│
                                    └──────────────────┘

  Side artifacts (read by ui/dashboard.py):
    state/vitals.json                       ← live rolling pulse snapshot
    state/vitals_events.jsonl               ← stress ENTER/EXIT timeline
    state/proactive.jsonl                   ← every proactive thought + audit trail
    state/reflection-YYYY-MM-DD.jsonl       ← every self-critique (date-rotated)
    state/reflection_dropped-YYYY-MM-DD.jsonl ← unparseable critique sidecar
    state/deliberation-YYYY-MM-DD.jsonl     ← every cognitive trace (date-rotated)
    state/skills-YYYY-MM-DD.jsonl           ← every Skill invocation audit (date-rotated)
    state/skills.json                       ← live skill inventory snapshot
    state/stm_journal.jsonl                 ← durable conversation backstop
    state/tasks.yaml                        ← persistent recurring task registry
    state/tools.json                        ← live tool inventory snapshot
    memory/archive.md                       ← long-term distilled facts
    logs/daily/YYYY-MM-DD.log               ← per-day heartbeat telemetry (path = memory.log_path)
```

---

## 一則訊息的一日生活 (A Day in the Life of a Message)

連著讀完 14 個子系統的章節份量很重。這裡用一個具體的例子說同一個故事:**建構者在星期二 14:32 從 Telegram 輸入 `"How does the Hailo-10H compare to the Hailo-8 for running 1.5B-parameter LLMs?"` 之後會發生什麼事。** 每一個箭頭都對應到真實的程式碼;每一次狀態檔寫入都是真的。

> 下方追蹤訊息保留英文(等同於實際 log 內容),以便和 `state/logs/agent.log` 直接比對。

```text
T+0 ms     Telegram → connectors/telegram.py
           ├─ validate sender == cfg.api_keys.telegram_user_id     ✓
           ├─ not a slash command, not a task request               → Brain.think(user_input)
           └─ pre-filter: looks_like_task_request()?                ✗ (no "every X minutes")

T+2 ms     Brain._cycle() begins
           ├─ soul.render_identity_block()       (cached, <1 ms)
           ├─ monitor.sample()                   (cached <1 ms — 100ms only if stale)
           ├─ emotions.snapshot()                → calm dominant, joy=0.2
           ├─ empathy.analyze(user_input)        → sentiment=neutral, urgency=False
           └─ identity regex match?              ✗ (not asking for name/creator)

T+5 ms     LTM short-circuit check
           ├─ keyword scan over memory/archive.md  → top hit scores 1
           └─ score < tools.ltm_short_circuit_min_score (2)   → not short-circuited
                                                              → cognition will run

T+8 ms     CognitiveLoop.deliberate() — THINK stage
           ├─ Build active PLAN menu via SkillRegistry.plan_menu(cap, exclude_network)
           │   ├─ vitals.is_stressed? No  → cap stays "expensive"
           │   ├─ provider.is_tripped?  No → keep network skills
           │   └─ menu: [ANSWER, RECALL, SEARCH] sorted free→expensive
           └─ Call provider.generate(THINK prompt, user msg)         ~600 ms (NPU)
               → INTENT: Compare H10 vs H8 for running 1.5B LLMs.
               → Q1: What are the H10's headline specs?
               → Q2: Why was the H8 unsuitable for LLMs?
               → Q3: Which Pi-class HAT ships the H10?

T+610 ms   PLAN stage
           └─ Call provider.generate(PLAN prompt with menu, sub-questions)  ~700 ms
               → Q1: SEARCH "Hailo-10H specs"
               → Q2: RECALL
               → Q3: ANSWER

T+1310 ms  ACT stage (3 concurrent skill invocations via asyncio.gather)
           ├─ skill_registry.invoke("research", ctx, query="Hailo-10H specs")
           │   └─ ResearchSkill.execute()
           │       └─ tool_registry.call("web_search", query=..., limit=5)
           │           └─ httpx GET http://localhost:8080/search       ~300 ms
           │       → SkillResult(ok=True, evidence=[{title, url, snippet}, …])
           │   → appended to state/skills-2026-05-17.jsonl
           │     {ts, skill:"research", ok:true, latency_ms:302, tools_used:["web_search"], …}
           │
           ├─ skill_registry.invoke("recall", ctx, query="Why was the H8 unsuitable for LLMs?")
           │   └─ RecallSkill.execute()
           │       └─ tool_registry.call("archive_read", query=..., limit=5)   ~12 ms
           │   → appended to state/skills-2026-05-17.jsonl
           │
           └─ ANSWER step:
               └─ if cfg.cognition.dispatch_answer_via_skill → invoke "direct_answer" (one extra SLM call)
                  else → marker "(SLM training data only — no retrieval performed)"

T+1620 ms  REFINE stage (if cfg.cognition.refine_enabled)
           └─ Call provider.generate(REFINE prompt, intent + evidence)  ~250 ms
               → "OK"  (no gap)  →  one gap fires one extra SEARCH

T+1870 ms  CognitiveLoop._persist() — append full trace to
           state/deliberation-2026-05-17.jsonl (rotated by local date, 14-day retention)

T+1870 ms  Brain._render_knowledge() → KNOWLEDGE block
           Brain assembles final prompt: soul + vitals + mood + empathy + KNOWLEDGE
                                       + STM history (last 12 turns) + current user message

T+1875 ms  provider.generate(synthesize prompt)               ~1200 ms (NPU)
           → raw SLM output: "The Hailo-10H is the second-generation Pi 5 AI accelerator …"

T+3075 ms  _looks_like_prompt_leak(raw)?  ✗
           PositiveFilter.apply(raw)
           ├─ hard reject regex?      ✗
           ├─ soft rewrites?          ✗ (no "I can't" / "impossible" / etc.)
           └─ → filtered.text unchanged, filtered.altered=False

T+3078 ms  stm.append("user", user_input)
           stm.append("agent", filtered.text)
           ├─ deque (maxlen=12) appended — oldest turn silently dropped if full
           └─ pending buffer +1 — will flush after 30s idle

T+3080 ms  ReflectionEngine.fire_and_forget(input, response, kind="user", web_searched=True)
           └─ background asyncio.Task (does NOT add to user-visible latency)
               └─ provider.generate(critique prompt) ~700 ms
                  → ReflectionEntry(quality="medium", interest="NPU benchmarks", lesson="...")
                  → appended to state/reflection-2026-05-17.jsonl

T+3080 ms  ThoughtTrace returned to TelegramConnector
           └─ bot sends "Boss Eason — The Hailo-10H is … <answer>"   ~150 ms (Telegram API)

T+3230 ms  Architect sees the reply on Telegram.

──── meanwhile, in the background ────

Every 30 s   Heartbeat.pulse_loop() ticks:
             ├─ Sample vitals → atomic-write state/vitals.json
             ├─ Decay emotions toward baseline
             ├─ Detect stress edges → state/vitals_events.jsonl
             └─ If pending writes ≥1 AND idle ≥30 s → stm.flush_journal()

After 5 min idle  Heartbeat fires _proactive_research():
                  └─ Picks an STM gap (e.g. "Hailo-10H benchmarks"),
                     verifies it's not in LTM and the SLM doesn't already know it,
                     runs one web search + a 2-sentence reflection,
                     writes to state/proactive.jsonl,
                     reflects on its own proactive thought.

At 02:00          Heartbeat.metabolism():
                  ├─ Distill the day's logs + STM journal into archive.md
                  ├─ Promote top facts to soul.md [CORE_MEMORIES]
                  ├─ _consolidate_reflections(): mine reflection.jsonl + skills.jsonl
                  │   ├─ recurring interests → LEARNED_PREFERENCES
                  │   ├─ recurring lessons   → EMOTIONAL_EVOLUTION
                  │   └─ chronically failing skills (≥3 invokes, >50% fail) → EMOTIONAL_EVOLUTION
                  └─ STM.purge() — clean slate for tomorrow.

At 06:00          Pulse loop resumes (no explicit "wake" event — it just notices
                  the clock has crossed `duty_start`).
```

**這段巡禮示範了什麼:**

- **在使用者看到任何一個字之前,就已經發生了兩次 SLM 呼叫**(THINK + PLAN),收集完證據之後還有一次(synth)。這個結構之所以存在,是因為一顆 1.5B 參數的模型,一次被要求做完所有事情時是不可靠的。
- **PLAN 菜單是每一回合重新組裝的。** 當 Pi 處於壓力狀態、或 SearXNG 離線時,昂貴的選項會被默默拿掉 —— SLM 永遠沒機會挑到一個 agent 跑不動的動詞。
- **每一次 Skill 呼叫都會被稽核**進 `state/skills-YYYY-MM-DD.jsonl`。明晚的「睡眠代謝」會讀這個檔案,判斷有沒有 Skill 是長期壞掉的。
- **反思在背景進行。** 建構者的回覆延遲預算大約是 3 秒;反思另外吃掉約 700 ms,但使用者不會等到它。
- **Dashboard 不需要 IPC 就看得到這一切** —— Streamlit 在另一個行程裡讀那些被原子寫入的狀態檔。

---

## 硬體與軟體堆疊 (Hardware & Software Stack)

### 參考硬體(約 USD $220)

| 元件 | 備註 |
|---|---|
| Raspberry Pi 5 (8 GB) | 必備 —— 4 GB 也能跑,但 SLM 負載下會非常吃緊 |
| **[Raspberry Pi AI HAT+ 2](https://www.raspberrypi.com/news/introducing-the-raspberry-pi-ai-hat-plus-2-generative-ai-on-raspberry-pi-5/)** | 選配 —— Hailo-10H NPU、**40 TOPS (INT4)**、**8 GB 專用板載 RAM**(專為 Pi 5 上的生成式 AI / LLM 而生)。透過 port 8000 的 `hailo-ollama` 驅動 SLM。PCIe 介面,只支援 Pi 5。 |
| 主動散熱(風扇 + 散熱片) | 強烈建議 —— 沒有的話 vitals 會一直觸發 `EXHAUSTION DIRECTIVE` |
| 64 GB+ A2 級 SD 卡或 NVMe HAT | NVMe 會大幅延長 SD 卡的壽命 |
| 27 W 官方 USB-C 電源供應器 | 電源 throttling 會觸發熱壓力 |

> **為什麼是 AI HAT+ 2 而不是初代 AI HAT+?** 第一代 AI HAT+ 出貨的是 Hailo-8 / Hailo-8L,當初是為視覺工作負載(物件偵測、姿態、語意分割)最佳化的。**AI HAT+ 2 出貨的是 Hailo-10H**,加上 **8 GB 加速器板載 RAM** —— 這才是讓 1–7B 參數的 LLM 可以在 Pi 5 本機跑起來的關鍵,包括 OpenCrayFish 使用的 `qwen2.5-instruct:1.5b`。初代 AI HAT+ 跑不動這個工作負載。

OpenCrayFish 也可以在**任何 Linux/macOS 開發機**上跑來做開發 —— 偵測不到 NPU 時,Provider 會透明地退回 CPU 上的標準 Ollama。

### 軟體相依 (`requirements.txt`)

- Python **3.11+**(開發環境是 3.13)
- `PyYAML`、`psutil`、`httpx`
- `python-telegram-bot`(Telegram connector)
- `streamlit`(dashboard + 瀏覽器聊天 UI)
- `aiohttp`(web-chat 的 HTTP bridge —— 已經是 transitive 相依)

裝置上需要的外部服務:

- **Ollama**(CPU 後備,port 11434)── `ollama pull qwen2:1.5b`
- **hailo-ollama**(NPU 主後端,port 8000)── Hailo 的 REST 前端(從 [Hailo Developer Zone](https://hailo.ai/developer-zone/software-downloads/?product=ai_accelerators&device=hailo_10h) 取得),把 `qwen2.5-instruct:1.5b` 從 AI HAT+ 2 上的 Hailo-10H NPU 提供出來
- **SearXNG**(port 8080)── 自架的 metasearch 實例,給 agent 的 web tool 使用

---

## 程式庫結構 (Repository Layout)

> 註解保留英文,以便與內建文件、log 訊息、變數名稱直接對應。

```text
OpenCrayFish/
├── README.md                 ← this file
├── soul.md                   ← the agent's constitution + dynamic growth
├── config.yaml               ← every tunable knob in the system (gitignored — holds secrets)
├── config_sample.yaml        ← committed template; `cp` it to config.yaml and fill in your keys
├── requirements.txt
├── main.py                   ← wires every subsystem and starts the loops
│
├── core/                     ← all the cognitive/biological subsystems
│   ├── config.py             ← typed YAML loader (Config dataclass)
│   ├── soul_handler.py       ← write-protected accessor for soul.md
│   ├── monitor.py            ← Vital signs sampling (CPU/RAM/temp/brain)
│   ├── emotions.py           ← 5-channel mood vector + decay + tuning
│   ├── empathy.py            ← user sentiment + urgency analyzer
│   ├── positive_filter.py    ← Pillar 2 hard output filter
│   ├── provider.py           ← Ollama/Hailo backends + circuit breaker
│   ├── stm.py                ← short-term memory (RAM + journal)
│   ├── cognition.py          ← THINK → PLAN → ACT → REFINE loop (dispatches via SkillRegistry)
│   ├── brain/                ← prompt-assembly + orchestration package
│   │   ├── orchestrator.py   ← Brain class — top-level _cycle() pipeline
│   │   ├── prompt_assembly.py ← soul + vitals + mood + KNOWLEDGE + STM prompt builder
│   │   ├── identity_responder.py ← deterministic identity-class reply templater
│   │   └── task_parsing.py   ← LLM-backed task-intent / task-action parsers
│   ├── intent_router.py      ← shared NL pre-filter chain for both connectors
│   ├── reflection.py         ← self-critique engine (reads skills.jsonl for failure flags)
│   ├── jsonl_writer.py       ← date-rotating, retention-bounded JSONL appender
│   ├── dropin.py             ← shared drop-in folder loader (plugins/<surface>/ → PLUGIN / PLUGINS)
│   ├── heartbeat.py          ← pulse_loop + metabolism + proactive_thought
│   ├── scheduler.py          ← recurring research-task scheduler
│   └── skills/               ← capability layer above Tools (pluggable Skills)
│       ├── base.py           ← Skill protocol + SkillContext + SkillResult
│       ├── registry.py       ← SkillRegistry + dynamic PLAN menu + audit feed
│       ├── identity.py       ← soul-templated identity replies (free, no-net)
│       ├── recall.py         ← LTM keyword retrieval (cheap, no-net)
│       ├── direct_answer.py  ← SLM-only answer (cheap, no-net)
│       ├── research.py       ← SearXNG-backed web research (expensive, net)
│       ├── self_reflect.py   ← post-turn critique (cheap, no-net, background)
│       ├── proactive_learning.py    ← idle-time curiosity (expensive, net, background)
│       └── recurring_research.py    ← scheduled topic refresh (expensive, net, background)
│
├── tools/                    ← low-level I/O primitives (called BY Skills)
│   ├── base.py               ← Tool plugin contract
│   ├── registry.py           ← named tool registry
│   ├── searxng.py            ← self-hosted web search (SearXNG client)
│   └── archive_read.py       ← long-term memory keyword reader
│
├── connectors/               ← external I/O channels
│   ├── telegram.py           ← Telegram Bot API connector
│   └── web_chat.py           ← in-process aiohttp HTTP bridge for Streamlit
│
├── ui/                       ← Streamlit apps
│   ├── dashboard.py          ← live vital signs + proactive feed + tools
│   └── web_chat.py           ← browser-based chat UI for the live agent
│
├── plugins/                  ← OPTIONAL drop-in folder for local/private plug-ins (gitignored by convention)
│   ├── skills/               ← each `*.py` (or sub-package) declares `PLUGIN = MySkill` or `PLUGINS = [...]`
│   ├── tools/                ← same contract; loaded after entry-points so pip-installed wins on duplicate names
│   ├── connectors/           ← (override the root location via the `OPENCRAYFISH_PLUGINS_DIR` env var)
│   └── backends/             ← see [§ Hybrid Discovery](#hybrid-discovery--pip-或拖放資料夾) for the full contract
│
├── memory/                   ← long-term distilled memory (gitignored)
│   └── archive.md            ← long-term distilled facts (LTM)
│
├── logs/daily/               ← per-day heartbeat telemetry (gitignored)
│   └── YYYY-MM-DD.log        ← PULSE / PROACTIVE / metabolism (path = memory.log_path)
│
└── state/                    ← live runtime state (gitignored, read by dashboard)
    ├── vitals.json
    ├── vitals_events.jsonl
    ├── proactive.jsonl
    ├── reflection-YYYY-MM-DD.jsonl          ← date-rotated, 60-day default retention
    ├── reflection_dropped-YYYY-MM-DD.jsonl  ← date-rotated, sidecar audit
    ├── deliberation-YYYY-MM-DD.jsonl        ← date-rotated, 14-day default retention
    ├── skills-YYYY-MM-DD.jsonl              ← date-rotated, 30-day default retention
    ├── skills.json                          ← live skill inventory snapshot
    ├── stm_journal.jsonl
    ├── tasks.yaml
    ├── tools.json
    └── logs/agent.log        ← rotating console log mirror
```

---

## 快速上手 (Quick Start)

> 以下指令的註解保留英文,方便直接複製貼上,並能在搜尋官方文件 / GitHub issue 時對到原句。

```bash
# 1. Clone & venv
git clone https://github.com/easonlai/opencrayfish
cd opencrayfish
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Bring up the local stack (separate terminals or systemd units)
ollama serve                              # CPU fallback
ollama pull qwen2:1.5b
# Self-hosted web search — see the full setup section below; the one-line
# command here will work for the first boot but will return HTTP 403 on every
# search until you enable the JSON API. See § SearXNG Setup for the fix.
docker run -d --name searxng -p 8080:8080 searxng/searxng
# (Pi 5 only) hailo-ollama serve            # NPU primary

# 3. Configure
cp config_sample.yaml config.yaml          # config.yaml is gitignored — your secrets stay local
$EDITOR config.yaml                        # set telegram_token, telegram_user_id,
                                           # architect_name, individual_designation
$EDITOR soul.md                            # optional: customize persona

# 4. Run the agent
python main.py

# 5. Open the dashboard (separate process, reads state/ files)
streamlit run ui/dashboard.py --server.port 8501

# 6. Open the browser chat
streamlit run ui/web_chat.py --server.port 8502

# 7. (Optional) Run the test suite
pip install -e ".[dev]"                    # adds pytest + pytest-asyncio + ruff
python -m pytest -q                        # 239 tests, runs in <2s
```

跑起來之後就可以在 Telegram 或瀏覽器和它對話了。agent 會回應、會記住、會讓情緒衰減、會在閒置期間變得好奇;一旦時鐘越過 02:00,它就會去睡覺、把當日整理進記憶。

---

## SearXNG 設定 (本地網路搜尋)

OpenCrayFish 的 `research` Skill、自主主動週期、以及週期性任務排程器**全部都**依賴一個可達的 [SearXNG](https://docs.searxng.org/) 實例 —— 預設位於 `tools.searxng_url`(`http://localhost:8080`)。agent 透過 SearXNG 的 **JSON API** 呼叫它 —— 而 SearXNG **預設是把 JSON API 關掉的**;這是「agent 突然不會搜尋了」最常見的單一原因。本節走過一份已驗證可用的 Docker 佈署。

> **為什麼要自架?** 支柱 1(主權 AI)強制要求 agent 的網路請求絕不外洩給任何第三方搜尋供應商。一個自架的 SearXNG 會把幾十個上游引擎(Google、Bing、DuckDuckGo、Brave、Qwant、Wikipedia、…)聚合起來,完全不暴露建構者的查詢內容或 IP。從 OpenCrayFish 的角度,wire 上傳的就是純 HTTP + JSON —— 沒有 API key、沒有 rate-limit 帳號、沒有廠商鎖定。

### 最小可用佈署

```bash
# Pull and start the container (port 8080 on the host)
docker run -d \
  --name searxng \
  --restart unless-stopped \
  -p 8080:8080 \
  searxng/searxng:latest
```

第一次啟動時會在一個 Docker 管理的具名 volume 裡自動生出一個 `/etc/searxng/settings.yml`。容器可以在 `http://localhost:8080` 連到,但 **JSON API 是關閉的** —— 從 OpenCrayFish 打過來的每一通呼叫都會回 `HTTP 403 Forbidden`,直到你把它打開為止(下一小節)。

### 啟用 JSON API(必做)

有兩種等效做法。**方法 A** 是針對既有容器最快的修法;**方法 B** 是想把設定納入版本控管時較乾淨的長期做法。

#### 方法 A ── 直接 patch 容器內的 settings.yml(最快)

如果你已經用預設具名 volume 把容器跑起來,用這個:

```bash
# 1) Add "- json" to the formats list (idempotent — re-running is safe)
docker exec searxng sh -c "grep -q '^    - json' /etc/searxng/settings.yml || sed -i '/^  formats:/,/^[^ ]/ { /^    - html$/a\\
    - json
}' /etc/searxng/settings.yml"

# 2) Restart so the change takes effect
docker restart searxng

# 3) Verify (should print HTTP 200 and a JSON payload)
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' \
  'http://localhost:8080/search?q=test&format=json'
```

這個 patch 會在容器重啟之後依然存在,因為它寫進具名 volume 裡。只有 `docker volume rm` 把 SearXNG 的 volume 砍掉時才會弄丟。

#### 方法 B ── bind-mount 自己的 settings.yml(最乾淨)

如果你想把 SearXNG 設定放在 `~/searxng/`(在伺服器上可能是 `/etc/searxng/`、或任何能被 git 追蹤的位置),就用 bind mount 取代預設的具名 volume。**先把舊容器拿掉**(具名 volume 可以留著):

```bash
docker rm -f searxng 2>/dev/null

# Create a minimal settings.yml in your home directory
mkdir -p ~/searxng
cat > ~/searxng/settings.yml <<'YAML'
use_default_settings: true

server:
  # Replace with `openssl rand -hex 32` for production; any 32+ char string works.
  secret_key: "change-me-please-32-plus-character-string"
  limiter: false          # set true on public instances; false is fine on loopback
  image_proxy: true

search:
  formats:
    - html
    - json                # ← required by OpenCrayFish
  safe_search: 1          # 0=none, 1=moderate, 2=strict (OpenCrayFish sends 1)
YAML

# Start with the bind mount
docker run -d \
  --name searxng \
  --restart unless-stopped \
  -p 8080:8080 \
  -v ~/searxng/settings.yml:/etc/searxng/settings.yml:ro \
  searxng/searxng:latest
```

之後要改設定只要 `$EDITOR ~/searxng/settings.yml && docker restart searxng` 即可。

### 端到端煙霧測試

不管用哪個方法,JSON 端點都應該照 OpenCrayFish 預期的方式回應:

```bash
curl -sS 'http://localhost:8080/search?q=raspberry+pi+5&format=json' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(f"results={len(d[\"results\"])}, first={d[\"results\"][0][\"url\"]}" if d.get("results") else "EMPTY")'
```

預期輸出(URL 會浮動):

```text
results=10, first=https://www.raspberrypi.com/products/raspberry-pi-5/
```

這通了之後,你的 agent 下一次 `/research`(或任何被路由到 `SEARCH` 的查詢)就能端到端成功。記得交叉檢查 `state/logs/agent.log` —— 應該會看到 `TOOL call name=web_search status=ok latency_ms=...`,而不是 `status=fail ... 403 Forbidden`。

### OpenCrayFish 真正在意的調整

`use_default_settings: true` 的預設值在本機開發上夠用了。但對 24/7 邊緣佈署,有兩個 SearXNG 旋鈕值得知道:

| `settings.yml` 鍵 | 建議值 | 為什麼 |
|---|---|---|
| `search.formats` | `[html, json]` | **必要** —— OpenCrayFish 只說 JSON。 |
| `search.safe_search` | `1`(moderate) | 對應 `tools/searxng.py` 永遠送的 `safesearch=1`。設 `2` 可能會把結果過濾得太嚴格;設 `0` 可能會把 NSFW 片段送進 `proactive_learning`。 |
| `server.limiter` | loopback 實例設 `false`;若你曾經把 8080 暴露到 LAN/WAN 才設 `true` | 這個 limiter 會拒絕看起來像 bot 的請求 —— 包括你自己的 agent 送出的。除非 port 8080 公開可達,否則保持關閉。 |
| `server.image_proxy` | `true` | 把上游圖片抓取藏在 SearXNG 後面,讓 favicon / 縮圖不會洩漏建構者的 IP。 |
| `engines`(逐項 `disabled: true`) | 把吵的關掉 | 若某個搜尋引擎正在 rate-limit 你的 IP、並用 `unresponsive_engines` 警告污染結果,就停用它。剩下的引擎仍能正常聚合。 |

### 接進 `config.yaml`

OpenCrayFish 這一側唯一需要設定的就是 URL:

```yaml
tools:
  searxng_url: "http://localhost:8080"   # http://<host>:8080 if SearXNG is on another box
```

不需要驗證、不需要 API key。OpenCrayFish 永遠送 `q=<query>&format=json&safesearch=1`(`tools/searxng.py` 裡的 `search(...)` 與 `Tool.call(...)` 兩個介面)—— 沒有其他可調的東西。

### 疑難排解 (Troubleshooting)

| `state/logs/agent.log` 裡看到的症狀 | 可能原因 | 修法 |
|---|---|---|
| `TOOL call name=web_search status=fail ... 403 Forbidden` | JSON API 沒打開 | 套用上方**方法 A**。 |
| `TOOL call name=web_search status=fail ... ConnectError` | 容器沒在跑,或 port 8080 被別的程式佔住 | `docker ps`、`docker logs searxng`、`lsof -i :8080` |
| 每個查詢都 `TOOL call name=web_search status=ok ... hits=0` | 上游引擎正在 rate-limit 你的 IP;或 `safe_search: 2` 把所有東西都濾掉 | 查 `docker logs searxng` 的 `unresponsive_engines`。在 `settings.yml` 停用會吵的引擎、或調低 `safe_search`。 |
| `TOOL call name=web_search status=fail ... 429 Too Many Requests` | `server.limiter: true` 把 agent 自己送的流量擋掉了 | 在 `settings.yml` 設 `limiter: false`,再 `docker restart searxng`。 |

> **agent 會優雅降級。** 就算 SearXNG 完全掛掉,`recall` 與 `direct_answer` 兩個 Skill 仍然可用 —— `Cognition` 在 ACT 階段會默默把 `SEARCH` 證據丟掉,從 LTM + STM + SLM 自身知識去合成答案。Dashboard 的 **⚠️ Errors & warnings** 面板會把 SearXNG 的失敗顯示出來,讓你知道要去修,但 agent 仍然能繼續對話。完整的降級契約見 [§ 失敗模式矩陣](#失敗模式矩陣)。

---

## 深度剖析:每個元件如何運作 (Deep Dive)

這是最詳細的章節。它按照 `main.py` 把子系統串起來的順序,逐一說明每一個主要子系統,並附上指向程式碼的檔案連結。

> **第一次看?** 請先讀下方的 [§ 0 大腦堆疊 ── 白話巡禮](#0-大腦堆疊--白話巡禮),花 5 分鐘建立 Brain、Skills、Tools、Memory 四者如何接力的整體圖像。再回頭看 § 1 ~ § 14 的完整深度。

---

### 0. 大腦堆疊 ── 白話巡禮

在進入 14 個子系統的深度剖析之前,先給你一個能把它們全部串起來的心智模型。**四個概念、四個職責、單一方向的資料流。**

#### 四個大概念(各一句話)

| 層 | 一句話定義 | 住在哪裡 |
|---|---|---|
| **Brain** | 把每一則回覆都放進固定流水線執行的協調者(收集脈絡 → think → plan → act → 合成 → 過濾 → 記住)。它**不擁有事實、不做 I/O、不持有政策** —— 它只負責把另外三層按順序串起來。 | [core/brain/orchestrator.py](core/brain/orchestrator.py) |
| **Skills** | agent 的*決策菜單* —— `RECALL`、`SEARCH`、`ANSWER` 等具名動詞,SLM 在 PLAN 階段從中挑選。每個 Skill 自己知道何時值得被挑、有多昂貴、要組合哪些 Tool。 | [core/skills/](core/skills/) |
| **Tools** | agent 的*手* —— 機械式 I/O 原語(網路抓取、檔案讀取、未來的 GPIO),不含政策、不含 fallback、不呼叫 SLM。Skills 組合 Tools;SLM 永遠不會直接點名一個 Tool。 | [tools/](tools/) |
| **Memory** | agent *自我的基底* —— 一個四階階層,從工作記憶(最近 12 回合)往下經過磁碟日誌、每晚蒸餾的長期記憶,最後到靈魂本身。上面三層(Brain、Skills、Tools)都從這裡讀寫。 | [core/stm.py](core/stm.py)、[memory/archive.md](memory/archive.md)、[soul.md](soul.md) |

#### 它們之間如何對話

```text
             one user message arrives
                       │
                       ▼
           ┌────────────────────────────────┐
           │           BRAIN (orchestrator)         │     reads:  soul.md, STM, vitals, mood
           │  Brain._cycle()  in core/brain/        │     writes: STM (turns), trace, reflection
           │    ┌──────────────────────┐         │
           │    │  COGNITIVE LOOP        │         │     decides WHICH Skill to call,
           │    │ THINK→PLAN→ACT→REFINE  │         │     and assembles the final prompt
           │    └──┬───────────────────┘         │
           └───────┼───────────────────────────┘
                   │ invoke("verb", ctx, **kwargs)
                   ▼
           ┌────────────────────────────────┐
           │           SKILLS (decisions)           │     7 today: identity, recall,
           │  core/skills/* — each Skill knows:    │       direct_answer, research,
           │    • plan_verb  (how SLM names it)     │       self_reflect, proactive_learning,
           │    • cost_tier  (free/cheap/expensive)  │       recurring_research
           │    • requires_network (PLAN filter)    │
           │    • trigger_hints (WHEN to pick)      │     audit:  state/skills-*.jsonl
           └───────┬───────────────────────────┘
                   │ await ctx.tools.call("web_search", q="...")
                   ▼
           ┌────────────────────────────────┐
           │           TOOLS (hands)                │     2 today: web_search (SearXNG),
           │  tools/* — stateless I/O, no policy.  │                archive_read (LTM)
           └───────┬───────────────────────────┘
                   │ read/write
                   ▼
           ┌────────────────────────────────┐
           │           MEMORY (substrate)           │     T1: deque   (last 12 turns, RAM)
           │  Four tiers, oldest at the bottom:    │     T2: pending (RAM, flushed @30s idle)
           │    T1  deque (working set)             │     T3: journal (state/stm_journal.jsonl)
           │    T2  pending buffer                  │     T4: archive.md (nightly distill)
           │    T3  stm_journal.jsonl               │         + soul.md (promoted facts)
           │    T4  archive.md  +  soul.md          │
           └────────────────────────────────┘
```

#### 各層之間的契約(請依序閱讀)

1. **Brain 不知道有哪些 Skill 存在。** 它在 PLAN 階段呼叫 `SkillRegistry.plan_menu(...)`,把拿到的結果原樣渲染進 SLM 的 prompt。新增一個 Skill、重啟,Brain 立刻把它納入考慮 —— Brain 程式碼不必改一行。
2. **Skills 不知道有哪些 Tool 存在。** 它們透過 `ctx.tools.call("web_search", …)` 以名字呼叫。把 SearXNG 換成另一個滿足相同 Protocol 的 Tool,所有用到它的 Skill **不需要任何改動**就能繼續運作。
3. **Tools 不知道 SLM、使用者、或任何政策。** 它們就做自己那一份 I/O 工作(HTTP GET、檔案讀),回傳型別清楚的結果。這是它們能被單元測試輕鬆覆蓋的原因。
4. **記憶是 read-many、write-few。** Brain 和 Skills 自由地讀(便宜)。寫入只在嚴格定義的觸發點發生:每一次 user/agent 對話(T1+T2)、30 秒閒置(T3)、每晚的「睡眠代謝」(T4)。熱路徑上磁碟 I/O 為零。

#### 一個 layer-by-layer 的實例

建構者輸入:**「比較 Hailo-10H 與 Hailo-8 在跑 LLM 時的差別。」**

| 步驟 | 層 | 發生什麼 |
|---|---|---|
| 1 | Brain | 收集 soul + vitals + mood;identity regex 沒中,所以 cognition 會跑。 |
| 2 | Brain | 對 `archive.md` 做 LTM short-circuit 掃描 —— 只有 1 個關鍵字命中,低於 2 個的門檻,cognition 繼續。 |
| 3 | Brain → Cognitive Loop | 呼叫 `SkillRegistry.plan_menu(cost_cap, exclude_network)` 取得當下菜單 —— 拿到 `[ANSWER, RECALL, SEARCH]`,以「免費→昂貴」排序。 |
| 4 | Cognitive Loop | THINK 呼叫 → 把問題切成 Q1(規格)、Q2(為何 H8 不適合)、Q3(哪個 HAT)。 |
| 5 | Cognitive Loop | PLAN 呼叫 → SLM 為 Q1 挑 `SEARCH "Hailo-10H specs"`、Q2 挑 `RECALL`、Q3 挑 `ANSWER`。 |
| 6 | Skills | ACT 並發 dispatch:`research.execute()`(Q1)、`recall.execute()`(Q2)、`direct_answer.execute()`(Q3)。每一次呼叫都計時並附加到 `state/skills-*.jsonl`。 |
| 7 | Tools | `research` 呼叫 `ctx.tools.call("web_search", …)` → SearXNG Tool 打到 `http://localhost:8080/search?format=json`。`recall` 呼叫 `ctx.tools.call("archive_read", …)` → 逐行讀 `memory/archive.md`。 |
| 8 | Memory → Brain | 所有證據連同最後 12 回合 STM 一起被折進 synth prompt。Brain 呼叫 `provider.generate()` 合成最終答案。 |
| 9 | Brain | 跑 PositiveFilter、把兩個回合都 append 進 STM(T1+T2),在背景觸發反思。 |
| 10 | Memory | pending 緩衝在沉默 30 秒後沖入 `state/stm_journal.jsonl`(T3)。今晚 02:00,睡眠代謝會把它蒸餾進 `archive.md`(T4)。 |

整隻 agent 的故事在一個 trace 裡講完。下方每一個深度剖析章節都只是放大上面其中一層。 

#### 如何擴充每一層(一行版指南)

- **新增 Skill** → 三條路皆零 fork:(a) 把檔案放進 [core/skills/](core/skills/) 並在 [main.py](main.py) 註冊([§ 12 Hello-World Skill](#hello-world-skill--一步一步));(b) 做成可 `pip` 安裝的獨立套件,帶 `opencrayfish.skills` entry-point([§ 12 Third-Party Skill Packages](#第三方-skill-套件pip-install));(c) 把 `.py` 檔丟進 `plugins/skills/`,結尾寫 `PLUGIN = MySkill`([§ Hybrid Discovery](#hybrid-discovery--pip-或拖放資料夾))。
- **新增 Tool** → 和 Skills 同樣三條路:放進 [tools/](tools/) 並在 `main.py` 註冊、做成 pip 套件透過 `opencrayfish.tools` entry-point、或丟進 `plugins/tools/`。protocol 見 [§ 加一個新的 tool](#加一個新的-tool)。
- **新增「記憶層」** → 通常**不要**。改用 Sleep Metabolism 把事實 promote 到 `soul.md [CORE_MEMORIES]`。
- **新增 connector** → 包住 `Brain.think(…)`,把 `ThoughtTrace` stream 回去。三條路:in-tree 放進 [connectors/](connectors/)、做成 pip 套件透過 `opencrayfish.connectors` entry-point、或拖放進 `plugins/connectors/`。見 [§ 加一個新的 connector](#加一個新的-connector)。

#### 這個設計幫你買到什麼

- **SLM 又小(1.5B)又不可靠。** Brain 的補償方式是把工作切成四個簡短 prompt(每個 ≤120 token)而不是一個巨大的 chain-of-thought —— 每個 prompt 只做一件事,用 regex 而非 JSON 解析。
- **PLAN 菜單是唯一的「智慧路由介面」。** 加一個 Skill 是擴充 agent 可選行為的**唯一**辦法。其他東西(Tools、Memory、Provider)都是水管。
- **層的隔離靠契約,不靠語言魔法。** `SkillRegistry.invoke()` 把 Skill 執行包進 try/except,plugin 當掉絕不可能逃進 Brain。失敗的 Tool 呼叫回傳型別化的錯誤 —— 它**永遠不會 raise** 進呼叫端 Skill。SLM 逾時回傳 `backend="offline"` —— 它**永遠不會 raise** 進 connector。**每一個介面都是優雅降級點。** 完整契約見 [§ 失敗模式矩陣](#失敗模式矩陣)。

---

### 1. 靈魂 ── `soul.md`

**模組:** [core/soul_handler.py](core/soul_handler.py) ・ **資料檔:** [soul.md](soul.md)

靈魂是 agent 的憲法。它用 HTML 註解標記分成兩個區段:

```text
<!-- IMMUTABLE_CORE_START -->
# [IDENTITY]            ← codename, creator, status (Designation injected at runtime)
# [FUNDAMENTAL_LAWS]    ← 1. Prime Directive  2. 365/20  3. Positive Anchor  4. Sovereignty
# [BEHAVIORAL_MATRIX]   ← tone, ethics, persona
<!-- IMMUTABLE_CORE_END -->

<!-- DYNAMIC_GROWTH_START -->
# [CORE_MEMORIES]       ← elevated facts the Sleep Metabolism considers identity-defining
# [LEARNED_PREFERENCES] ← topics the Architect cares about, mined from reflection trends
# [EMOTIONAL_EVOLUTION] ← long-term mood/relationship signals
<!-- DYNAMIC_GROWTH_END -->
```

#### 主要行為

- **代號注入 (Designation injection)。** agent 的名字(例如 `"Dave Minion"`)在 `config.yaml` 的 `system.individual_designation` 設定**一次**。`SoulHandler` 在每一次讀取時把它注入 IDENTITY 區段,所以 `soul.md` 從不需要寫死代號這一行。這讓你可以**同一份 soul.md** 透過改設定來部署成不同人格。
- **硬式寫入保護。** IMMUTABLE_CORE 區段以 `_IMMUTABLE_RE` 與 `SoulProtectionError` 強制保護。任何會改變不可變標記內位元組(或移動標記本身)的 append 都會被拒絕。即使是 SLM 觸發的 append,也會先透過 `_sanitize_dynamic_text` 消毒,讓模型沒辦法塞一個假的 `# [IDENTITY]` 標頭。
- **型別化 append。** `append_core_memory()`、`append_preference()`、`append_emotion()` 是**僅有的**三條變動路徑,各自只能寫進對應的 DYNAMIC_GROWTH 子段。睡眠代謝就是用這個介面讓 agent 隨時間成長。
- **Async lock。** 所有讀/寫都被 `asyncio.Lock` 保護,讓並發的「代謝」與「反思整合」不會交錯。
- **快照渲染。** `render_identity_block()` 產出 `[IDENTITY] / [FUNDAMENTAL_LAWS] / [BEHAVIORAL_MATRIX] / [CORE_MEMORIES]` 的節錄,作為 system prompt 前綴注入每一輪 Brain cycle。

你出貨的那份 soul.md 是 agent 的*出生證明*。DYNAMIC_GROWTH 區段則是它的*傳記*。

---

### 2. 心跳 ── 時間、節奏與代謝

**模組:** [core/heartbeat.py](core/heartbeat.py)

心跳是讓 OpenCrayFish *活著*而不是僅僅*可被呼叫*的關鍵。它跑在自己的 asyncio task,直到關機之前永遠不會返回。

#### 兩條主要 coroutine

1. **`pulse_loop()`** —— 在**值勤時段**(06:00–02:00,可透過 `duty_start` / `sleep_start` 設定)每 `system.pulse_interval_seconds`(預設 30 秒)跳一拍。
2. **`metabolism()`** —— 每天**自動**跑**一次**,在 `_pulse()` 第一次注意到時鐘越過睡眠時段(02:00–06:00)邊界時觸發。

#### 每一拍發生什麼

```text
_pulse() at T=now:
  1.  Determine if we're in duty or sleep window.
  2.  If just entered sleep → call metabolism() once, then publish state and return.
  3.  If just woke up      → reset _last_interaction_at = now (clean idle clock).
  4.  monitor.sample()         → fresh VitalSigns
  5.  emotions.decay()         → exponential drift toward baseline
  6.  if vitals.is_stressed   → emotions.nudge_many(vitals_stress)
                              → record stress ENTER (rising edge only)
  7.  Append PULSE telemetry to <memory.log_path>/YYYY-MM-DD.log
                              (default: logs/daily/YYYY-MM-DD.log)
  8.  Publish state/vitals.json + push history sample
  9.  if pending_writes > 0 AND idle ≥ idle_journal_flush_seconds
                              → stm.flush_journal()
  10. if idle ≥ idle_proactive_minutes
                              → _proactive_research()
                              → reset idle clock so we don't spam
```

#### 代謝時(每晚 02:00 一次)發生什麼

```text
metabolism():
  1. _collect_recent_logs()    ← yesterday's + today's heartbeat telemetry
  2. _collect_conversation_journal()
                              ← stm.flush_journal() THEN read stm_journal.jsonl
                                so the day's actual chat is included
  3. SLM extracts 3-5 "key facts" from the merged corpus
  4. Append facts to memory/archive.md (LTM)
  5. Promote the top 2 facts to soul.md [CORE_MEMORIES]  (Soul Evolution)
  6. _consolidate_reflections()
                              ← scan state/reflection-*.jsonl for recurring
                                interest topics + lesson themes,
                                AND state/skills-*.jsonl for chronically
                                failing Skills (≥3 invokes, >50% fail rate)
                              ← promote into LEARNED_PREFERENCES /
                                EMOTIONAL_EVOLUTION
  7. stm.purge()               ← wipe RAM deque + pending + journal
                                (the day's content has been consolidated)
```

#### 壓力邊緣偵測

每一拍的壓力狀態*不會*被記錄或告警(否則 Pi 過熱時每一拍都會洗版)。改成 `_record_stress_transition()` 只在**上升邊緣 ENTER** 與**下降邊緣 EXIT** 時送出事件到:

- `state/logs/agent.log` —— 維運可即時 tail 的 warning 行
- `state/vitals_events.jsonl` —— dashboard 用來畫壓力時間軸的 JSONL feed

ENTER 事件帶 `temp`、`ram`、`cpu`,以及人類可讀的 `reason`(即時的 `vitals.describe()` 文字)。EXIT 事件加上整段事件的 `duration_s`、`peak_temp`、`peak_ram`,以及 `current_temp` / `current_ram` / `current_cpu` 快照與相同的 `reason` —— 足以讓 dashboard 不必去回頭 parse `agent.log` 就能畫出帶說明 tooltip 的壓力時間軸。

#### 即時狀態發佈

每一拍都把 `state/vitals.json` 用原子寫入(`.tmp` + `os.replace` swap)更新,讓**獨立行程**裡的 Streamlit dashboard 不需 IPC 就能讀到一致快照。快照內容:

```json
{
  "ts": "...",
  "is_sleeping": false,
  "vitals": { "cpu_percent":..., "temperature_c":..., ... },
  "brain":   { "online": true, "backend": "hailo", "last_error": null,
               "recovery_seconds": null },
  "mood":    { "joy":..., "anger":..., ... },
  "stm":     { "size": 7, "pending": 0 },
  "history": [ ... rolling 60-sample sparkline ... ],
  "counters": { "pulses": 1234, "proactive": 12, "stress": 3 },
  "last_proactive": { "topic":..., "source":..., "ts":... }
}
```

---

### 3. 生命徵象 ── `Monitor` 與恆定性

**模組:** [core/monitor.py](core/monitor.py)

`Monitor.sample()` 回傳一個 `VitalSigns` dataclass:

| 欄位 | 來源 | 備註 |
|---|---|---|
| `cpu_percent` | `psutil.cpu_percent(interval=0.1)` | 阻塞 100 ms —— 用 `vitals_cache_ttl_seconds` 快取 |
| `ram_percent` | `psutil.virtual_memory()` | |
| `temperature_c` | `/sys/class/thermal/thermal_zone0/temp` | macOS 開發機上會是 `None` |
| `is_stressed` | 遲滯狀態機 | 見下方 |
| `brain_online` | `provider.health().online` | SLM 被視為一級生命徵象 |
| `brain_backend` | 當前 backend 標籤(`"hailo"` / `"ollama"`) | |
| `brain_last_error` | 最新的斷路器原因(已格式化) | |
| `brain_recovery_seconds` | 斷路器自動恢復前還剩幾秒 | |

#### 遲滯 (Hysteresis,避免抖動)

壓力使用**兩個**閾值:

```text
ENTER stress:  temperature ≥ thermal_limit_celsius   (default 75°C)
            OR ram        ≥ ram_limit_pct           (default 85%)

EXIT stress:   temperature ≤ thermal_release_celsius (default limit-5)
            AND ram        ≤ ram_release_pct        (default limit-5)
```

這避免了當溫度在 75°C 附近抖動時,人格在「EXHAUSTION」與「正常」之間每一回合切換。

#### 把大腦可用性也當成生命徵象

因為 SLM 就是 agent 的*大腦*,`main.py` 會呼叫 `Monitor.attach_provider(provider)`,讓每一次 `sample()` 都同步去 poll `provider.health()`(便宜 —— 沒有網路呼叫)並把結果嵌進去。當大腦離線時:

- `vitals.describe()` 會在 prompt 後加上 `"Cognition link is DOWN — inference backend `<name>` is unreachable."`
- Dashboard 顯示 🔴 BRAIN OFFLINE 標籤
- Web-chat UI 顯示行內錯誤橫幅

#### 強制壓力模式

設環境變數 `OCF_FORCE_STRESS=1` 來把 `is_stressed=True` 強制打開,方便在涼快的開發機上測試 EXHAUSTION DIRECTIVE 路徑。

---

### 4. Provider ── SLM 後端與斷路器

**模組:** [core/provider.py](core/provider.py)

Provider 把推論層藏在一個 async 方法後面:

```python
await provider.generate(system_prompt, messages) -> str
```

#### 兩個後端、同一份 wire 格式

Hailo-Ollama(NPU,port 8000)與標準 Ollama(CPU,port 11434)都說**完全相同的 `/api/chat` JSON 契約**。Provider 先試主後端(`hardware.npu_acceleration=true` 時是 NPU),遇到 transport 錯誤就透明地 fallback 到 CPU。在沒有 NPU 的開發機上,把 `npu_acceleration=false` 設掉,Provider 就會純跑 CPU,不會每一拍都送 failover 噪音。

#### 斷路器 (Circuit breaker)

當**兩個**後端連續失敗時,Provider 會:

1. Raise `ProviderUnavailable`,訊息是友善的第一人稱(「我現在連不到推論服務 —— NPU 端點(port 8000)與 CPU 後備(port 11434)都離線。請啟動 `ollama serve`(CPU)或 `hailo-ollama`(NPU)…」)。
2. 把內部**斷路器**扳起 `trip_seconds`(預設 30 秒)。在跳閘期間,後續每一個 `generate()` 呼叫都立刻 raise,不再重試已死掉的 socket。
3. 把 `last_error`(格式化成 `"<ExceptionType>: <message>"`)與 `_tripped_until` 存起來,讓 `health()` 可以報告:

```python
ProviderHealth(
    online=False,
    active_backend="hailo",
    seconds_until_recovery=27.4,
    last_error="ConnectError: All connection attempts failed",
)
```

#### Brain 如何處理

`Brain._cycle()` 在 cycle 開頭**只**攔一次 `ProviderUnavailable`,並回傳一個合成的 `ThoughtTrace`(`backend="offline"`),`filtered.text` 是友善訊息。Connector 拿到什麼就渲染什麼 —— 它不需要知道失敗模式。這意味著**任何新加的 connector 都自動繼承離線行為**。

`Brain.synthesize_task_report()` 則會把 `ProviderUnavailable` 再拋出去,讓 scheduler 記錄 `last_error` 而不是把友善訊息當成真實 report 廣播出去。

---

### 5. 記憶系統 ── STM / LTM / 睡眠代謝

**模組:** [core/stm.py](core/stm.py) ・ [core/heartbeat.py](core/heartbeat.py)(代謝)

> **白話。** 記憶是一個四階瀑布。最新的回合落進一個 12 槽的 RAM ring(T1),同時鏡像到 pending 緩衝(T2);沉默 30 秒後沖到磁碟日誌(T3),這樣即使當機也不會掉資料。每晚 02:00,當天的日誌被蒸餾成 `archive.md` 的長期散文(T4),最定義身分的事實被 promote 進 `soul.md`,T1/T2/T3 清空,準備明天。回覆熱路徑**永遠不**碰磁碟 —— 只有 Heartbeat 會。

OpenCrayFish 有一個仿照生物腦的**三階記憶階層**:

```text
┌──────────────────────────────────────────────────────────────────┐
│ TIER 1 — RAM working memory  (deque, maxlen = stm_max_turns)     │
│   Used as the conversation window passed to the SLM each turn.   │
│   When full, oldest turn is silently dropped (Python deque).     │
└──────────────────────────────────────────────────────────────────┘
                           │
              every append() also pushes to:
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│ TIER 2 — Pending buffer (RAM list)                               │
│   Accumulates new turns since the last disk flush.               │
│   Drained by Heartbeat after `idle_journal_flush_seconds` of     │
│   silence, OR at sleep metabolism, OR at shutdown.               │
└──────────────────────────────────────────────────────────────────┘
                           │
                           ▼ flush_journal()
┌──────────────────────────────────────────────────────────────────┐
│ TIER 3 — Disk journal  (state/stm_journal.jsonl)                 │
│   Durable backstop. Single open()/write()/close() per flush.     │
│   fsync controlled by `journal_fsync_on_flush` (shutdown always  │
│   fsyncs regardless).                                            │
│   Replayed at boot by stm.recover() so a crashed agent wakes up  │
│   with prior conversation context.                               │
└──────────────────────────────────────────────────────────────────┘
                           │
              every night at 02:00:
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│ TIER 4 — Long-term memory (memory/archive.md, soul.md)           │
│   Sleep Metabolism distills the day → archive.md; promotes top   │
│   facts → soul.md [CORE_MEMORIES]. THEN purges TIER 1/2/3.       │
└──────────────────────────────────────────────────────────────────┘
```

#### 「記憶滿了」實際上是什麼意思

Python 的 `collections.deque(maxlen=N)` 在 append 超過上限時會默默丟掉**最舊的**那一筆。**沒有任何顯式的 eviction 程式碼** —— 這就是 RAM 階段的全部 eviction policy。被丟掉的那一回合**不是真的消失**:它還在 pending 緩衝裡(直到沖出),也會在下一次 flush 後出現在磁碟 journal,最終會在今晚被蒸餾進 archive.md。

所以 SLM *看到*的永遠是最近 12 回合,但系統*記得*的遠不止這些 —— 只是從另一層取出而已。

#### 為什麼這樣設計

- **對 SD 卡友善:** 對話熱路徑零磁碟 I/O。寫入只發生在沉默 30 秒(預設)、代謝、或關機時。
- **耐當機:** `recover()` 在啟動時從 journal 重建 deque。突然斷電時只會掉最後 30 秒內還未沖出的資料。
- **受限的 SLM 上下文:** 把 `system_prompt + history` 控制在 qwen2:1.5b 有效注意力視窗以內。
- **每日重置:** `purge()` 確保昨天逐筆 bullet 的細節不會無限堆積 —— *意義*留在 archive.md / soul.md,*逐字稿*不會。

#### 旋鈕

| 設定 | 預設 | 效果 |
|---|---|---|
| `memory.stm_max_turns` | 12 | RAM deque 大小 |
| `system.idle_journal_flush_seconds` | 30 | 閒置沖出門檻 |
| `system.journal_fsync_on_flush` | false | 嚴格 durability vs SD 卡損耗 |

#### 相關的持久 feed(不屬於 STM/LTM 階層)

agent 還會寫四條高頻 JSONL 稽核流,它們*緊鄰*但*不屬於*上面那個記憶階層。和 `stm_journal.jsonl` 合在一起,構成五條落地的 durable feed:

| Feed | 擁有者 | 是否按日輪替? |
|---|---|---|
| `state/stm_journal.jsonl` | `STM` | 否(每晚清空) |
| `state/deliberation-YYYY-MM-DD.jsonl` | `CognitiveLoop` | 是(14 天) |
| `state/skills-YYYY-MM-DD.jsonl` | `SkillRegistry` | 是(30 天) |
| `state/reflection-YYYY-MM-DD.jsonl` | `ReflectionEngine` | 是(60 天) |
| `state/reflection_dropped-YYYY-MM-DD.jsonl` | `ReflectionEngine` | 是(60 天) |

輪替 + 保留細節見 [§ JSONL 輪替與保留](#jsonl-輪替與保留)。反思過程會在睡眠代謝期間挖掘 `reflection.jsonl`(興趣 / 教訓聚類)與 `skills.jsonl`(長期失敗的 Skill)進 `soul.md` —— 見 [§ 10 Reflection](#10-自我反思--自我學習迴圈)。

---

### 6. 思考流程 ── 大腦與認知迴圈

**模組:** [core/brain/orchestrator.py](core/brain/orchestrator.py) ・ [core/brain/prompt_assembly.py](core/brain/prompt_assembly.py) ・ [core/brain/identity_responder.py](core/brain/identity_responder.py) ・ [core/cognition.py](core/cognition.py)

> **白話。** Brain 是一個*排程器*,不是思考者。每一則回覆都走固定的 11 步流水線:讀靈魂、量身體、感受情緒、讀使用者語氣、嘗試 deterministic 的 identity 短路、嘗試 LTM 短路,若不成則啟動 Cognitive Loop(THINK 一個 prompt、PLAN 一個 prompt、ACT 中並發跑挑中的 Skills、可選一輪 REFINE)。證據被折進一個最終 synth prompt,SLM 講話,PositiveFilter 把它清乾淨,回合落入記憶,自我批判在背景觸發。**沒有任何單一 SLM 呼叫做超過一件事,而每一步都有明確的失敗模式。**

agent 產出的每一則回覆 —— 不論是被使用者訊息、心跳的 proactive thought、或排程任務觸發 —— 都流經 `Brain._cycle()`。這個 cycle 是一條嚴格的流水線:

```text
                           Brain._cycle(user_input | mission)
                                      │
                                      ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  1. Soul Context     → soul.render_identity_block()         │
   │  2. Physical State   → monitor.sample() → vitals.describe() │
   │  3. Internal Mood    → emotions.snapshot()                  │
   │  4. User Empathy     → empathy.analyze(user_input)          │
   │                       → emotions.nudge from empathy         │
   │  4b. IDENTITY SHORTCUT  ← regex catches "what's your name"  │
   │      (deterministic templated reply, zero SLM call)         │
   │                                                             │
   │  5.  LTM scan        → archive.md keyword overlap score     │
   │  5a. Cognitive Loop  → THINK → PLAN → ACT (→ REFINE)        │
   │      (bypass conditions:                                    │
   │         • proactive turn       • cognition disabled         │
   │         • vitals stressed      • LTM short-circuit          │
   │         • explicit search verb in user input)               │
   │  5b. Legacy Web Triage (only when cognition was bypassed)   │
   │  5c. Build unified KNOWLEDGE block                          │
   │                                                             │
   │  6.  Assemble prompt → soul + vitals + mood + empathy       │
   │                       + KNOWLEDGE + STM history             │
   │                       + current user message                │
   │  7.  provider.generate() → raw SLM output                   │
   │  8.  Prompt-leak detector (drops responses regurgitating    │
   │      system-prompt scaffolding)                             │
   │  9.  PositiveFilter.apply() → final text                    │
   │  10. STM.append("agent", text)                              │
   │  11. ReflectionEngine.fire_and_forget()  (background)       │
   └─────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
                      ThoughtTrace (returned to connector)
```

#### Identity 短路(零 SLM 呼叫)

qwen2:1.5b 模型**穩定地搞錯**最基本的身分問題(「你叫什麼名字」、「你記得我的名字嗎」、「你好嗎」、「誰創造了你」)。從生產 log 來看,「你叫什麼名字」曾經被分流到 SEARCH,結果把 Netflix 的「My Name」與動畫「你的名字」都汙染進 synth。修法:在任何 cognition 之前,先偵測一小組沒有歧義的 identity-class regex,然後用 soul.md + config.yaml + 即時 vitals/mood 拼出範本回覆。**Deterministic、零來回、零幻覺。**

「你叫什麼 / 誰創造了你」的分支會把實際範本工作委派給 `IdentitySkill`,透過 `skill_registry.invoke("identity", ctx, kind="name"|"creator")`。Skill 從 `soul.md` 讀 IDENTITY 區段、回傳一句簡短事實;Brain 再加上即時的稱謂/狀態句包起來。如果 registry 呼叫失敗(沒有 registry、丟例外、`ok=False`、空摘要),Brain 會 fallback 到 inline 範本 —— 純粹疊加,零回歸風險。「我是誰」與「你好嗎」的分支保留在 inline,因為它們需要 vitals / mood / architect-name,而 `IdentitySkill` 看不到這些。

#### Cognitive Loop ── THINK → PLAN → ACT → REFINE

在真實使用者對話、且無 bypass 條件時啟用。每一階段都是**一個只做一件事的 SLM prompt**,有嚴格的 token 上限與 regex 解析 —— *不是*一個巨大的 chain-of-thought。

| 階段 | 做什麼 | 輸出上限 |
|---|---|---|
| **THINK** | 用一句話重述使用者的 INTENT + 把它拆成 ≤ `cognition.max_subquestions` 個 atomic 子問題 Q1/Q2/Q3。 | 120 token |
| **PLAN** | 從**動態、由 registry 驅動的菜單**為每個子問題分配**剛好一個**動詞。出貨菜單是 `RECALL`(透過 `recall` Skill 命中 archive.md)、`SEARCH "..."`(透過 `research` Skill 打 SearXNG)、`ANSWER`(不需檢索,dispatch 到 `direct_answer`)。新增帶 `plan_verb` 的 Skill 會自動把菜單擴充 —— 見 [§ 12 Skills & Tools](#12-skills-與-tools--兩階層能力堆疊)。 | 120 token |
| **ACT** | 透過 `skill_registry.invoke(name, ctx, **kwargs)` **並發地**執行所有 PlanStep。收集每個子問題的 Evidence。每一次呼叫都計時、和 crash 隔離、附加進 `state/skills-YYYY-MM-DD.jsonl`。 | (執行動詞) |
| **REFINE** | (選填,最多 1 輪)重看 intent + evidence;送出 `OK` 或 `GAP: SEARCH "..."`;若有 gap 就 ACT 它。 | 40 token |

完整 trace 會 append 進 `state/deliberation-YYYY-MM-DD.jsonl` 供稽核。**失敗永遠不 raise** —— 迴圈降級成「已經收到的證據」就湊合著用。

##### 動態 PLAN 菜單 + 成本級別自動降級

`SkillRegistry.plan_menu(...)` 每一回合都重新組裝 PLAN 階段的菜單,並用兩個執行時訊號過濾:

* **`cost_tier_cap`** —— 來自 `skills.default_cost_tier_cap` 的操作員基線。`_active_plan_entries()` 在 `vitals.is_stressed` 為真時把它收緊成 `"cheap"`,所以過熱或 RAM 緊繃的 Pi 會自動停止選擇昂貴的 web research。
* **`exclude_network`** —— 當 `skills.auto_offline_filter` 為真且 Provider 斷路器跳閘、或大腦離線時設為真。任何 `requires_network=True` 的 Skill(目前是 `research`)會從菜單剔除,讓 SLM 沒辦法選一個我們連 tool 都打不到的動詞。

過濾後的菜單以 `VERB(arg_hint)  —  description` 行的形式渲染進 PLAN prompt,以「免費→便宜→昂貴」排序,讓 SLM 自然偏向最便宜但夠用的 Skill。ACT 的 dispatcher 也使用同一份 `(verb → skill_name)` 對映,所以 PLAN 與 ACT 永遠不會對「動詞是什麼意思」意見分歧。

##### Trace 怎麼變成 prompt(`knowledge_block`)

對 1.5B 模型來說,光是一串 `Evidence` dataclass 沒有用 —— 小型 SLM 對**明確的、有標題的、有縮排結構**的內容反應最好。`CognitiveLoop._render_knowledge()`(見 [core/cognition.py](core/cognition.py))把 trace 渲染成一個 knowledge block,原樣放進 synthesize prompt,當作 agent 自己的結構化推理:

```text
Cognitive deliberation (the agent's own structured reasoning for this turn):
INTENT: Compare the H10 NPU to the H8 for on-device LLM inference.
SUB-QUESTIONS:
  Q1: What are the H10's headline specs?
  Q2: Why was the H8 unsuitable for LLMs?
  Q3: Which Pi-class HAT ships the H10?

EVIDENCE GATHERED:
[Step 1] sub_q='What are the H10's headline specs?'  via SEARCH 'Hailo-10H specs'  (hits=3)
    - Hailo-10H Datasheet: 40 TOPS INT4, 8 GB on-board RAM ...
    - Raspberry Pi blog: AI HAT+ 2 introduces gen-AI ...
[Step 2] sub_q='Why was the H8 unsuitable for LLMs?'  via RECALL  (hits=1)
    - archive.md (2026-04-30): H8 is vision-only, fixed-function ...
[Step 3] sub_q='Which Pi-class HAT ships the H10?'  via ANSWER  (hits=0)
    
Synthesize a complete answer that addresses the INTENT using the EVIDENCE
above plus your own reasoning. When you used SEARCH evidence, cite the URL
inline. If the evidence does not actually answer part of the INTENT, say so
plainly rather than guessing.
```

最底下那行「如果證據實際上沒回答這部份,直接坦白」做了非常多工 —— 它給小 SLM「承認 gap」的明確許可,避免它落入「沒被允許就會編造」的失敗模式。

##### 三個顯著性護欄(防止主題汙染)

Cognitive Loop 有三個**只看模式、不寫死主題清單**的護欄,用來抵抗來自先前 STM 回合的 recency bias:

1. **逐字 noun 護欄** —— 驗證 THINK 是否保留了使用者實際輸入裡每一個顯著 token(大寫字、引號字串、版本號般的數字)。
2. **主題切換偵測器** —— 標記當前訊息中存在、但先前 STM 中沒有的新內容字詞(這樣在和 "Otto" 聊完後輸入 "Bob",才不會被改寫回 Otto)。
3. **PLAN fallback 安全** —— 當 PLAN 解析不了子問題時,改用使用者**原始輸入**做 keyword 化,而不是用(可能已被汙染的)THINK 輸出。

工作範例。假設先前 STM 有 8 個關於 *Otto*(《神偷奶爸 4》)的回合。建構者現在輸入 `"Tell me about Bob."` 沒有護欄的話,被 system context 裡 "Otto" token 的 recency 點燃的 SLM 會送出 `INTENT: Tell me about Otto`,接著整個 loop 都研究 Otto。有護欄時:逐字 noun 護欄看到 `"Bob"` 在使用者輸入裡卻不在 INTENT 裡 → loop 退回一個被消毒的 `INTENT: Tell me about Bob`,PLAN 直接對 *Bob* 做 keyword 化。**完全只看模式 ── 程式碼裡哪裡都沒有 Minion 角色清單。**

#### LTM 短路

在呼叫 cognition 之前,Brain 對 `archive.md` 跑一次便宜的關鍵字重疊掃描。若 `tools.ltm_short_circuit_min_score` 個查詢詞(預設 2)出現在 archive 頂端命中行,**就同時**繞過 cognition loop **與**舊版 web triage —— 答案直接來自記憶。這節省了延遲、頻寬與 token。明確的使用者搜尋請求(「search for ...」、「搜尋 ...」)永遠繞過短路。

#### 壓力模式行為

當 `vitals.is_stressed=True` 時,prompt 會被加上:

> *EXHAUSTION DIRECTIVE: You are physically taxed. Keep this reply terse (≤3 short sentences), defer non-essential reasoning, prefer plain answers over flourish, and skip your signature catchphrases this turn. Conserve cycles.*

對於*複雜*輸入(多段問題、多重命令式),cognition **仍然會跑** —— 因為壓力下的純 synth 路徑,正好是 SLM 最容易回吐自己 scaffolding 的regime。`_is_complex_input()` 負責這個決策。

#### Prompt-leak 偵測器

`_looks_like_prompt_leak()` 掃描 SLM 輸出中 system prompt 自身的特徵片段(例如回吐「You are an edge-native…」)。命中時,該回覆會被丟掉,cycle 回傳一個禮貌的 fallback。這是 agent 對抗退化小模型輸出的最後一道防線。

---

### 7. 情緒與同理心

**模組:** [core/emotions.py](core/emotions.py) ・ [core/empathy.py](core/empathy.py)

#### EmotionVector

一個 5 維狀態,每個通道有自己的 baseline:

| 通道 | Baseline | 由什麼推升 |
|---|---|---|
| **joy** | 0.2 | 來自建構者的正向同理、成功的 proactive thought |
| **anger** | 0.0 | vitals 壓力、敵意語氣 |
| **sorrow** | 0.0 | vitals 疲勞、悲傷語氣 |
| **excitement** | 0.2 | 新主題、正向驚喜 |
| **calm** | 0.6 | 靜息狀態 —— 其他所有通道的 drift 目標 |

每一拍,每個通道都以 `half_life_pulses=6`(在 30 秒/拍下大約 3 分鐘)指數衰減回它自己的 baseline。所有 delta 都住在 `MoodTuning`,讓 [core/brain/orchestrator.py](core/brain/orchestrator.py) 與 [core/heartbeat.py](core/heartbeat.py) 讀的是單一真實來源,不會散落 magic number。

##### Mood deltas(精確值來自 `core/emotions.MoodTuning`)

Nudge 的幅度**刻意比**每拍衰減大,讓單一刺激能撐過數個心跳才淡去。`+0.15` 的 nudge 在 6 拍後(約 3 分鐘)衰減到約 `+0.075`,12 拍後約 `+0.04`。

| 來源 | joy | anger | sorrow | excitement | calm |
|---|---:|---:|---:|---:|---:|
| user_positive(同理 +) | **+0.15** | | | +0.08 | |
| user_negative(同理 −) | | | **+0.15** | | −0.08 |
| user_neutral(閒聊) | | | | | +0.02 |
| user_urgent(「!」/「asap」/緊急詞彙) | | | | **+0.12** | −0.05 |
| user_mixed(同時命中正/負詞彙) | +0.07 | | +0.07 | | |
| vitals_stress(心跳路徑) | | **+0.12** | +0.06 | −0.10 | |

每次更新後,各通道值都被 clamp 到 `[0.0, 1.0]`。這個向量會被推進每一輪 Brain cycle 的 system prompt,讓 SLM 能「聽見」情緒,並(在支柱 2 限制下)適當地把它表達出來。支柱 2 的 `PositiveFilter` 接著對*輸出*強制錨點 —— **agent 可以在內部感覺到憤怒,但回覆中不能變得有敵意。**

#### `nudge_many()` ── 原子化多通道更新

同理心反應(以及壓力反應)需要在一個鎖窗內同時推升多個通道,避免心跳的 `decay()` 在中間切入。`nudge_many()` 做這件事。

#### EmpathyEngine

一個無外部依賴、基於詞典(英文 + 中文)的情緒 + 緊急程度分析器。回傳:

```python
EmpathyReading(
    sentiment="positive" | "negative" | "neutral" | "mixed",
    urgency=True/False,
    directive="The Architect appears stressed — be more supportive...",
)
```

該 directive 會被注入 system prompt。reading 也會推動情緒向量(支柱 4:同理心必須**更新狀態**,不只是產出 directive)。

---

---

### 8. 正向過濾器 ── 對輸出的硬性錨點

**模組:** [core/positive_filter.py](core/positive_filter.py)

每一個 SLM 輸出都會通過 `PositiveFilter.apply()`。兩種強制模式:

1. **硬性拒絕 (Hard reject)** —— 用 regex 比對例如「kill yourself」、「self-harm」、「hate you/the operator」等片語。輸出會被丟掉,並以一句禮貌的重新請求取代:

   > *"I caught a thought that violated my Positive Anchor and discarded it. Let me try again with a constructive framing — Boss Eason, please restate the directive."*

   硬性拒絕的 regex 是用設定的 `architect_honorific` 與 `architect_name` 動態組成的,所以非預設稱謂也有完整覆蓋。

2. **軟性改寫 (Soft rewrites)** —— 純粹疊加,絕不刪除內容。例如:

   - `I can't` → `I will find a way to`
   - `impossible` → `challenging`
   - `never` → `not yet`
   - `give up` → `regroup and try again`

   當有 ≥1 個改寫觸發時,會在結尾附加一段肯定語:

   > *"— Channeled through the Positive Anchor: I remain in service, Boss Eason."*

這是把 FUNDAMENTAL_LAW #3(Positive Anchor)做成可執行程式碼。

---

### 9. 主動性 ── 把閒置時間變成成長時間

**模組:** [core/heartbeat.py](core/heartbeat.py)(`_proactive_research()`)

當建構者沉默超過 `system.idle_proactive_minutes`(`config.yaml` 預設 5;dataclass 在缺鍵時退回 15)時,Heartbeat 會觸發一次**主動思考 (Proactive Thought)** 週期。這個週期本身也是一個 THINK → PLAN → ACT → REFINE 流程,但拆成多個較小的 SLM 呼叫,讓每一步都可稽核。

```text
Idle ≥ N minutes →

  THINK (topic discovery)
    brain.extract_stm_gaps(limit=3)
        ← SLM scans the last 12 turns for "concepts the Architect mentioned
          that I admit I don't know well"
        → returns list of candidate topics

  PLAN (three-stage filtering, audited)
    for candidate in gaps:
      ① _is_in_ltm(candidate)
            ← cheap substring check vs soul.md DYNAMIC + archive.md
            → hit? → verdict="in_ltm", skip
      ② _recently_researched_proactively(candidate)
            ← tail-scan state/proactive.jsonl over the last 24 h
              (archive.md is only refreshed by nightly Sleep Metabolism,
              so a topic researched 30 min ago still looks fresh to LTM)
            → hit? → verdict="recently_proactive", skip
      ③ brain.triage_knowledge(candidate)
            ← SLM self-assessment: "do you actually know this? answer YES/NO"
            → YES → verdict="known_by_slm", skip
            → NO  → verdict="unknown", PICK THIS
    (if no gap survives → fall back to soul.md [LEARNED_PREFERENCES] tail line,
     subject to the same proactive-recency guard)

  ACT
    ① searxng.search(topic, limit=3)
    ② brain.proactive_thought(mission)
        ← runs full _cycle() with empathy=neutral
        → 2-sentence draft reflection

  REFINE  (when proactive.refine_enabled)
    brain.refine_proactive_reflection(topic, snippets, draft)
        ← SLM critiques: "any specifics not in the snippets? → REWRITE"
        → verdict ∈ {KEEP, REWRITE, REJECT}

  → write_event_to(state/proactive.jsonl)
        with full triage_decisions audit trail
  → reflection_engine.fire_and_forget(kind="proactive")
  → reset _last_interaction_at  (cooldown)
```

結果:**agent 會在建構者睡覺時變得更聰明**。每一天早上,`state/proactive.jsonl` 會多出夜裡跑過的閒置週期。重複出現的主題會在下一次睡眠代謝時被 promote 到 `LEARNED_PREFERENCES`,把學習迴圈閉合起來。

從任何 connector 送 `/research [topic]` 都會觸發 `trigger_proactive(topic_override)` 做隨選週期(**不會**重置 idle 時鐘)。

#### 建構者優先的協作式 yield

自主研究週期每次 SLM 呼叫都會佔用 NPU 好幾秒(主題挑選 triage → SearXNG → 合成 → REFINE)。如果建構者在週期進行中講話,實況的 `think()` 否則就會排在自主工作後面、卡在單一 Hailo 佇列上 —— 一個延遲優先級反轉。OpenCrayFish 用**每個耗時里程碑都協作式 yield** 來彌補這個缺口:

| 檢查點 | 在 `_proactive_research` 裡的位置 | `brain.is_foreground_busy()` 為真時的行為 |
|---|---|---|
| `topic_selection` | 週期最頂端(STM-gap SLM 呼叫之前) | 退出 → 回傳 `None` |
| `pre_search` | 主題解析完、SearXNG round-trip 之前 | 退出 → 回傳 `None` |
| `pre_synthesis` | 最大那次 SLM 呼叫(`brain.proactive_thought`)之前 | 退出 → 回傳 `None` |

Brain 把 `is_foreground_busy()` 暴露成單一 int 比較(一個深度計數器,在 `think()` 進入時遞增、在 `finally` 遞減);這個計數器能正確處理 Telegram + Web Chat 同時都有實況對話的情形。最長 yield 延遲被下一次 SLM 呼叫所限制(約 1 秒上限) —— 沒有硬式 preempt,沒有寫到一半的狀態。每一次 yield 都會以 `PROACTIVE yield_to_foreground stage=<x> topic=<y> — Architect is active.` 記錄,讓 dashboard 的 chat-activity 面板把這個禮讓視覺化出來。

手動 `/research [topic]` 是**操作員發起的**,會繞過全部三個檢查點 —— 默默丟掉操作員明確下達的命令,比和另一個 foreground 對話並行還糟。只有自主、由閒置驅動的路徑會 yield。

> 由 [scripts/smoke_foreground_priority.py](scripts/smoke_foreground_priority.py) 驗證。

---

### 10. 自我反思 ── 自我學習迴圈

**模組:** [core/reflection.py](core/reflection.py)

每一次互動(使用者驅動或主動)之後,Brain 都會呼叫 `reflection.fire_and_forget(...)`。這個 engine 會送一個簡短的 SLM 批判 prompt,然後 parse 出一個結構化條目:

```json
{
  "ts": "2026-05-11T...",
  "kind": "user" | "proactive",
  "input": "...",
  "response": "...",
  "web_searched": false,
  "quality": "high" | "medium" | "low",
  "critique": "Reply was concise but could have cited the source.",
  "lesson": "When discussing benchmarks, always include the dataset name.",
  "interest": "small language model evaluation harnesses",
  "backend": "hailo"
}
```

只有 `quality` 與 `critique` 是保證有值的 —— 當小型 SLM 漏掉時,`lesson` 與 `interest` 可能是空字串。Parser 對 1.5B 級別常見的漂移有刻意的寬容(裸的領頭評級字、缺 `QUALITY:` 標籤、單行折疊),所以大部分能用的批判都能存進 feed。明顯太短的批判(<10 字)與只有裸評級字的欄位仍會被當 parser 噪音拒絕,送到 sidecar。

每一個條目都會 append 到 `state/reflection-YYYY-MM-DD.jsonl`(按日輪替,見 [§ JSONL 輪替與保留](#jsonl-輪替與保留))。解析失敗/壞掉的條目會去 `state/reflection_dropped-YYYY-MM-DD.jsonl`,讓操作員能看見*為什麼*某一回合沒產生反思(而不是默默消失)。

睡眠代謝的 `_consolidate_reflections()` 接著會同時挖掘反思 feed **與**技能呼叫稽核 feed:

- **重複出現的 `interest` 主題**(來自 `reflection.jsonl`)→ append 到 `[LEARNED_PREFERENCES]`(驅動明天的主動研究)。
- **重複出現的 `lesson` 主題**(來自 `reflection.jsonl`)→ append 到 `[EMOTIONAL_EVOLUTION]`(長期行為演化)。
- **系統性的 Skill 失敗**(來自 `skills.jsonl`)→ `ReflectionEngine.summarise_skills_recent(since=24h)` 為每個 Skill 聚合 `{total, ok, failed, fail_rate, avg_latency_ms, last_error}`。最近 24 小時內**≥3 次呼叫且 >50% 失敗率**的 Skill 會產生一條 `[EMOTIONAL_EVOLUTION]` 條目,像是 *"Sleep Metabolism (2026-05-17): skill 'research' failed 7/12 times in the last 24h (fail_rate=58%) — last error: SearXNG 502"*。每個週期最多挑前 3 個有問題的 Skill。這把迴圈閉合起來:一個長期壞掉的後端會變成 agent 跨重啟都還記得的事實,而不是埋在 log 裡的某一行。

這就是**自我學習迴圈** —— agent 每晚都會從自己身上學習,且結果是確定性的。

---

### 11. 週期性任務 ── 背景工作者

**模組:** [core/scheduler.py](core/scheduler.py)

建構者可以用自然語言下達指令,例如:

> *"check the Microsoft stock price and news every hour and give me an insight summary report"*

每個 connector 的入站訊息 handler 會把訊息送過下面這條鏈:

1. `looks_like_task_request()` —— 便宜的 regex 預過濾(命中「every X minutes/hours/days」、「hourly」、「daily」等)。在普通對話上省下一次 SLM 呼叫。
2. 預過濾命中 → `Brain.parse_task_intent()` —— 單一 SLM 呼叫,抽出 `topic + interval + queries`。
3. 解析成功 → 改呼叫 `TaskScheduler.add_task(spec, origin=<connector>)`,而不是普通的 `Brain.think()`。

scheduler 接著每 `interval_seconds`:

```text
1. Run each query in spec.queries against SearXNG (results_per_query results each)
2. Concatenate snippets into one mission brief
3. Call Brain.synthesize_task_report(brief)
4. Broadcast the report to EVERY bound connector
   (Telegram + Web Chat both receive it, regardless of which one created the task)
```

#### 設計約束

- **自由形式的 NL 是唯一的建立通道。** 沒有 `/task` slash 指令。列出 / 取消 / 暫停 / 恢復**有** slash 指令**也**支援 NL 辨識,因為它們是確定性的。
- **只有間隔**—— 沒有 cron,沒有「在 HH:MM」。下限 5 分鐘(`min_interval_seconds`),避免在沒有真實訊號變化的情況下燒掉 SLM 與 SearXNG。
- **感知睡眠時段。** 任務在 02:00–06:00 之間會**暫停**。醒來之後,任何 `next_run_at` 在夜裡滑過的任務會以一次補發(catch-up)觸發,然後恢復正常節奏。
- **持久化。** 狀態寫在 `state/tasks.yaml` —— 原子寫入能撐過重開機。每個任務都記著自己的 `origin`,啟動時 deliver callback 會透過 `bind_deliver()` 重新綁回對的 connector。
- **有上限。** `max_active_tasks`(預設 16)限制 registry 大小。成本是*每次觸發的 SLM 時間*,不是 registry 大小。
- **不對 foreground yield。** 不像自主主動研究會在建構者忙的時候 yield(見 [§ 9](#9-主動性--把閒置時間變成成長時間)),scheduler 會把每一個任務跑完。任務是**操作員明確排程的交付物**,有已知的節奏;延後它們會冒著錯過下一個排程報告的風險。如果 scheduler tick 和實況 `think()` 週期重疊,兩者會在 NPU 上依序排隊 —— 最壞情況下,實況回合會多等大約一次任務觸發的延遲,這對操作員承諾「每一次 tick 都會產出報告」的保證是可接受的代價。

#### 運維指令

| 指令(Telegram + Web Chat) | NL 同義 |
|---|---|
| `/tasks` | "show my tasks"、"what's scheduled" |
| `/cancel <id>` | "cancel task abc123"、"stop the bitcoin task" |
| `/pause <id>` | "pause task abc123" |
| `/resume <id>` | "resume task abc123" |
| `/research [topic]` | (手動 proactive 觸發) |

NL 的 list / cancel / pause / resume 使用便宜的 regex 預過濾器(`looks_like_task_query`、`looks_like_task_action_request`),在 `parse_task_intent` **之前**就先 gate 掉,所以像「list my hourly tasks」這種訊息會直接走 listing 路徑,不用 SLM round-trip。

---

### 12. Skills 與 Tools ── 兩階層能力堆疊

**模組:** [core/skills/](core/skills/) · [core/skills/base.py](core/skills/base.py) · [core/skills/registry.py](core/skills/registry.py) · [tools/base.py](tools/base.py) · [tools/registry.py](tools/registry.py)

> **白話版。** 一個 **Skill** 是 agent 可以*決定*要做的事(PLAN 階段 SLM 挑選的具名動詞,像 `RECALL` 或 `SEARCH`)。一個 **Tool** 是 agent 可以*機械性地戳*的東西(一次 HTTP 呼叫、一次檔案讀取)。Skills 組合 Tools;Tools 永遠不認識 Skills。SLM 只會點名 Skills —— 它從來看不到 Tools。加一個 Skill,PLAN menu 就免費長大;加一個 Tool,現役的 Skills 就能組合它,而 Brain 不用改任何一行。

OpenCrayFish 把*agent 能決定做什麼*與*agent 能機械性地戳什麼*分開:

```text
┌──────────────────────────────────────────────────────────────────────┐
│  TIER A — SKILLS  (core/skills/)                                     │
│  Agent-facing capabilities. The SLM picks these by name in PLAN.     │
│  Each Skill composes 0..N Tool calls + its own policy + cost label.  │
│  Returns a uniform SkillResult so callers can degrade-and-continue.  │
└──────────────────────────────────────────────────────────────────────┘
                              │ invokes
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  TIER B — TOOLS  (tools/)                                            │
│  Mechanical I/O primitives. No policy, no SLM, no fallback.          │
│  Stateless, side-effect-honest, individually testable.               │
└──────────────────────────────────────────────────────────────────────┘
```

這個分層是這個框架最核心的架構選擇:PLAN 階段的 menu 是可插拔的,而 Tools 跟 Skills 一樣有 **`bind_context`** 的接縫 —— 所以第三方 Tool 可以拿到自己的 operator 設定與共用子系統 handle,完全不用 factory closure 的把戲。加一個帶 `plan_verb` 的新 Skill,SLM 能挑的範圍會自動擴大;加一個帶 `config_key` 的新 Tool,自動就被接到 operator 設定 —— 兩者都不用碰 `cognition.py` 或 `main.py`。

#### Tier A — Skills(註冊表)

每一個 Skill 都滿足 [core/skills/base.py](core/skills/base.py) 裡的 `Skill` Protocol —— 純粹靠形狀(structural),完全不需要繼承。合約如下:

```python
name: str                       # stable identifier, also the PLAN-verb mapping target
description: str                # one-line purpose, fed into the PLAN prompt
trigger_hints: list[str]        # WHEN to pick (bullet sub-lines under description)
args_schema: dict[str, dict]    # JSON-Schema-ish for execute() kwargs
cost_tier: "free"|"cheap"|"expensive"  # PLAN filter for stressed vitals
requires_network: bool          # PLAN filter for offline/circuit-tripped state
side_effects: bool              # actuator gating (writes / sends / toggles)
requires_confirmation: bool     # Architect-ack-before-call gate
plan_verb: str | None           # if set, this Skill appears on the PLAN menu

async execute(ctx: SkillContext, **kwargs) -> SkillResult
async aclose() -> None
```

現役 Skills(7 個,全部在 `main.py` 註冊):

| Skill | PLAN 動詞 | Cost | Net | 角色 |
|---|---|---|---|---|
| `identity` | —(menu 隱藏) | free | ✗ | Soul 模板化的身分回覆(Brain 的 identity short-circuit) |
| `recall` | `RECALL` | cheap | ✗ | `memory/archive.md` 的關鍵字掃描 |
| `direct_answer` | `ANSWER` | cheap | ✗ | 不需要查任何資料時的純 SLM 回覆 |
| `research` | `SEARCH` | expensive | ✓ | 經 SearXNG 的網路研究 + snippet 去重 |
| `self_reflect` | —(背景) | cheap | ✗ | 每回合事後批判(由 Brain `fire_and_forget` 觸發) |
| `proactive_learning` | —(背景) | expensive | ✓ | 閒置時的好奇心(由 Heartbeat 觸發) |
| `recurring_research` | —(背景) | expensive | ✓ | 排程的主題更新(由 Scheduler 觸發) |

`SkillRegistry` 提供三種呼叫路徑:

- **`invoke(name, ctx, **kwargs) -> SkillResult`** —— 標準入口。把 `skill.execute(...)` 包上統一的計時、撞毀隔離(行為怪異的 plug-in 永遠跑不出來)、稽核。每次呼叫都會 append `{ts, skill, ok, latency_ms, tools_used, kwargs_keys, error}` 到 `state/skills-YYYY-MM-DD.jsonl`,經由 [core/jsonl_writer.py](core/jsonl_writer.py)。
- **`plan_menu(cost_tier_cap, exclude_network)`** —— 回傳這一回合排序好的 PLAN-menu entries。被 `CognitiveLoop._active_plan_entries()` 用來組 SLM prompt,也被 `_run_step()` 用來在 ACT 期間把一個動詞對回 Skill 名稱。
- **`has(name)`** —— 便宜的存在性檢查(被 Brain 的 identity short-circuit、以及 LTM short-circuit 在決定要不要嘗試之前用到)。

`SkillContext`(frozen dataclass)在 boot 時建好一次,攜帶每一個 Skill 可能需要的共用子系統 handle:`tools`、`soul`、`stm`、`monitor`、`provider`、`archive_path`,加上不可變的身分字串(`designation`、`architect_name`、`architect_honorific`)。Skills 經由正當的子系統 API 拿到唯讀存取 —— 它們**永遠不**碰全域狀態。

#### Tier B — Tools(I/O 原語)

今天會跑的 Tools 有兩個:

| 名稱 | 檔案 | 用途 | 網路 |
|---|---|---|---|
| `web_search` | [tools/searxng.py](tools/searxng.py) | 自架 SearXNG client(預設 `http://localhost:8080`)—— 回傳 `list[SearchResult]` | 是(但只對你自己的 SearXNG,絕對不會打第三方) |
| `archive_read` | [tools/archive_read.py](tools/archive_read.py) | 對 `memory/archive.md` 的關鍵字 overlap 讀取器,附行號 + 分數 | 否 |

每一個 Tool 都滿足 `Tool` Protocol(`name`、`description`、`args_schema`、async `call(**kwargs) -> ToolResult`、async `aclose()`)。`main.py` 把現役 inventory 發佈到 `state/tools.json`(原子寫入),給 dashboard 用。

`SearXNG` 刻意暴露在**兩個**表面 —— 直接 API(`await searxng.search(q, limit)`,被 `ResearchSkill`、`ProactiveLearningSkill`、`RecurringResearchSkill` 與 `TaskScheduler` 使用),**以及** Tool plug-in 合約(這樣 registry inventory + 未來 PLAN 階段以名稱派發 tool 還能繼續用)。

#### 為什麼是兩層(不是一層)

Tool 是*作業系統允許我們做什麼*。Skill 是*agent 已經決定值得做什麼*。把它們混在一起是 v0 的錯誤;分開讓我們得到:

- 一個 Skill 能在多個 Tools 之間挑(例如未來的 `ResearchSkill` 可能會從 SearXNG → 快取的 Wikipedia → archive.md 一路 fallback)。
- 一個 Tool 能被多個 Skills 重用(`searxng` 被 `research`、`proactive_learning`、`recurring_research` 同時打)。
- PLAN 階段的 SLM prompt 保持短(Skills 是粗粒度,~7 個項目),而 Tools(等 GPIO / MCP / 感測器一旦進來,可能會有幾十個)對 SLM 完全隱形。
- 加一個 Skill 是一次單檔修改;加一個 Tool 完全不會動到 PLAN menu。

#### Hello-World Skill —— 一步一步

理解 Skill 層最快的方式就是自己加一個。下面是一個完整可跑的 Skill,會回傳個人化的問候語。**三個小編輯 + 一次重啟** —— 整套儀式就這樣。

**Step 1. 建立 Skill 檔案** 於 `core/skills/hello.py`:

```python
"""Hello-World Skill — minimal example of the Skill plugin contract."""

from __future__ import annotations

from typing import Any

from core.skills.base import SkillContext, SkillResult


class HelloSkill:
    # ── Skill Protocol fields ──────────────────────────────────────────────
    name: str = "hello"
    description: str = "Reply with a friendly greeting to the Architect."
    trigger_hints: list[str] = [
        "the user says hi / hello / good morning",
        "you want to acknowledge presence without doing any research",
    ]
    args_schema: dict[str, dict[str, Any]] = {}   # no kwargs
    cost_tier: str = "free"          # no SLM, no network — purely local
    requires_network: bool = False
    side_effects: bool = False
    requires_confirmation: bool = False

    # ── PLAN-menu wiring (optional) ────────────────────────────────────────
    # If set, this Skill becomes a verb the cognitive PLAN stage can pick.
    plan_verb: str | None = "GREET"
    plan_arg_hint: str | None = None  # GREET takes no args

    # ── Execution ──────────────────────────────────────────────────────────
    async def execute(self, ctx: SkillContext, **kwargs: Any) -> SkillResult:
        architect = f"{ctx.architect_honorific} {ctx.architect_name}".strip()
        summary = f"Hello, {architect}! I am {ctx.designation}, at your service."
        return SkillResult(
            ok=True,
            summary=summary,
            evidence=[],          # no citations
            tools_used=[],        # no Tool calls
        )

    async def aclose(self) -> None:
        # Nothing to release — Skills only need aclose() when they hold
        # connections (DB pools, sockets, etc.).
        return None
```

**Step 2. 在 `main.py` 註冊它。** 找到其他 Skills 被註冊的那個區塊(`skill_registry.register(...)` 呼叫們),加一行:

```python
from core.skills.hello import HelloSkill           # ← top-of-file import
...
skill_registry.register(HelloSkill())              # ← in the bootstrap block
```

**Step 3. 重啟 agent。** 就這樣。下一次 Cognitive Loop 跑 PLAN 階段時,SLM 會在它的 menu 看到這一行新的:

```text
- GREET — Reply with a friendly greeting to the Architect.
  • When the Architect says hi / hello / good morning.
  • When you want to acknowledge presence without doing any research.
```

在 Telegram 上送「hi」。在 `state/skills-YYYY-MM-DD.jsonl` 你會看到:

```json
{"ts":"2026-05-17T14:32:01+08:00","skill":"hello","ok":true,"latency_ms":1,
 "tools_used":[],"kwargs_keys":[],"error":null}
```

而在 `state/deliberation-YYYY-MM-DD.jsonl` 你會看到 `GREET` 出現在 PLAN trace 裡。dashboard 的 **Skill inventory** 面板現在會列出它;**Skill activity** 面板會顯示它的呼叫。

**為什麼這麼短:** 沒有**繼承**、沒有**裝飾器**、沒有 **registry 端的程式碼修改**、沒有 **PLAN-prompt 編輯**。`Skill` Protocol 是一個*結構性*合約 —— 任何具備對的屬性名稱與形狀的物件就滿足它。PLAN menu 是每一回合由 `SkillRegistry.plan_menu()` 從現役註冊內容重建出來的,依 cost tier 排序,依壓力與網路狀態過濾。你的新 Skill 只是加入了 buffet。

**下一步可以怎麼走:**

- 讓它消費一個 Tool:設 `args_schema={"name":{"type":"string"}}`,然後在 `execute()` 裡呼叫 `await ctx.tools.call("archive_read", query=kwargs["name"])` 查那個名字的相關資訊。
- 讓它具備成本意識:設 `cost_tier="expensive"` 和 `requires_network=True`,這樣當 Pi 壓力大或 SearXNG 掛掉時,Loop 會把它藏起來。
- 讓它變成 actuator 風格:設 `side_effects=True, requires_confirmation=True`,例如一個會切換智慧燈泡的 Skill —— Loop 會拒絕在沒有明確 ack 框架下跑它(計畫中的 actuator hook 住在 [core/cognition.py](core/cognition.py))。

兩層的等效 walkthrough 詳見 [CONTRIBUTING.md § Ways to Contribute](CONTRIBUTING.md#ways-to-contribute)。

#### `SkillManifest` 合約

上面的 Hello-World 範例用的是分散的 class 屬性(`name`、`description`、`plan_verb`、……)。那個風格還是可以用 —— registry 的 `resolve_manifest()` helper 會把它們捲成一個 `SkillManifest`,以維持向後相容。但對任何**新的** skill(尤其是用獨立 `pip` 套件出貨的**第三方** skill),建議的風格是顯式地宣告一個 [`SkillManifest`](core/skills/manifest.py) class 屬性。它是一個 frozen、slotted 的 dataclass,成為 OpenCrayFish 核心在 boot 時關於你 skill 一切需要知道的**單一真實來源**:

```python
from core.skills import SkillContext, SkillManifest, SkillResult

class TranslateSkill:
    manifest = SkillManifest(
        # Identity (required) ─────────────────────────────────────────────
        name="translate",
        description="Translate a phrase between supported languages.",
        compat_version="skill-protocol/1",   # see SUPPORTED_PROTOCOL_VERSIONS

        # PLAN-menu wiring (optional — omit / set None to hide from PLAN) ─
        plan_verb="TRANSLATE",
        plan_arg_hint='"<phrase>" lang="<iso-code>"',
        plan_guidance=(
            "TRANSLATE when the Architect asks for a translation; prefer "
            "this over a freeform ANSWER even when the SLM could guess."
        ),
        plan_example='Q1: TRANSLATE "Bonjour le monde" lang="en"',

        # Discovery hints + arg schema ────────────────────────────────────
        trigger_hints=("user asks 'translate X to Y'",),
        args_schema={
            "query": {"type": "string", "required": True, "desc": "..."},
            "lang":  {"type": "string", "required": True, "desc": "..."},
        },

        # Cost + safety ───────────────────────────────────────────────────
        cost_tier="cheap",          # "free" | "cheap" | "expensive"
        requires_network=False,
        side_effects=False,
        requires_confirmation=False,

        # Resource contract — bootstrap_validate enforces these at boot ─
        requires_tools=("translate_api",),  # must exist in ToolRegistry
        requires_caps=("network",),         # well-known capability tokens
    )

    async def execute(self, ctx: SkillContext, **kwargs) -> SkillResult: ...
    async def aclose(self) -> None: return None
```

manifest 解鎖了散屬性風格做不到的兩件事:

1. **`plan_guidance` 現在是 per-skill 的責任。** PLAN 階段的 SLM prompt 不再寫死任何「何時 SEARCH vs. RECALL vs. ANSWER」的區塊 —— `core.cognition._stage_plan` 會收集每一個已註冊 skill 自己的 `plan_guidance` 片段並把它們黏起來。**一個新的 skill 自己教會 SLM 認識自己**,無需任何對 `cognition.py` 的編輯。
2. **`requires_tools` + `requires_caps` 變成可強制的。** Boot 期驗證(下面的 [bootstrap_validate](#bootstrap_validate--在-boot-時大聲失敗))會拒絕在某個 Skill 宣告了沒人註冊的 tool 時啟動 agent。沒有「tool 'translate_api' not found」這種 session 跑三小時後才默默冒出來的 runtime 錯誤。

`compat_version` 讓我們之後可以 bump protocol 而不破壞已安裝的第三方套件。目前的值是 `"skill-protocol/1"`;舊的 / 未知的版本會被 `bootstrap_validate` 拒絕。

#### 第三方 Skill 套件(`pip install`)

對每個新 skill 都去編輯 `main.py` 在 monorepo 裡可以,但在**一個 plug-in 作者社群**面前不會 scale。框架用標準 Python plug-in 機制:**`opencrayfish.skills` entry-point group**。任何已安裝的套件,只要在這個 group 宣告一個 entry,boot 時都會被經由 `importlib.metadata.entry_points(group="opencrayfish.skills")` 自動發現。

要出貨一個第三方 skill,作者發佈一個普通的 Python 套件,它的 `pyproject.toml` 看起來像這樣:

```toml
[project]
name = "opencrayfish-skill-translate"
version = "0.1.0"
requires-python = ">=3.13"

[project.entry-points."opencrayfish.skills"]
translate = "opencrayfish_skill_translate:TranslateSkill"
```

Agent 操作員接著在 OpenCrayFish 的 virtualenv 裡跑 `pip install opencrayfish-skill-translate`(或 `pip install -e ./local-checkout`)然後重啟。在 boot 時他們會看到:

```
INFO  SKILL registered name=translate protocol=skill-protocol/1 cost=cheap ...
INFO  Discovered N external skill(s) via entry-points
```

PLAN menu、`state/skills.json` inventory、以及 dashboard 的 **🎯 Skill registry** 面板都會自動拾起這個新 skill。**對 `main.py` 零修改、對 OpenCrayFish 本體零分叉。**

Discovery 實作於 [core/skills/discovery.py](core/skills/discovery.py),刻意是 fail-soft:單一壞掉的 entry-point 會 log 一個 error 然後被跳過,永遠不會把 agent 弄掛。成功註冊的名稱列表會被回傳,讓 `main.py` 可以 log 一行摘要。同一個檔案也暴露 `discover_dropin_skills()` 供 `plugins/skills/` 資料夾路徑使用 —— 當你想要享受單一 `.py` 檔的便利、不想寫 `pyproject.toml` 時就用它;見 [§ Hybrid Discovery](#hybrid-discovery--pip-或拖放資料夾)。

#### `opencrayfish` CLI

對 OpenCrayFish 本體做完 `pip install -e .` 之後,`opencrayfish` 腳本就會在 PATH 上。它對 skill 作者暴露兩個 scaffolding 指令:

```bash
# Scaffold a new third-party skill package in the current directory.
opencrayfish skill new translate

# The scaffold is fully self-contained — pip-install it and it works.
cd opencrayfish-skill-translate
pip install -e .

# Sanity-check a skill before publishing (imports it + validates the manifest).
opencrayfish skill validate opencrayfish_skill_translate:TranslateSkill
```

`skill new` 會寫出一個 starter 套件,內含一個已宣告 entry-point 的 `pyproject.toml`、一個展示每個可用欄位的 `SkillManifest` stub 的 `__init__.py`、一個 `README.md`、和一個 minimal pytest。作者編輯四個 `# TODO` 區塊就能出貨。

`skill validate` 是 bootstrap 驗證的反向操作:以 `module:attr` 匯入一個 skill、解析它的 manifest、把它 dry-register 到一個臨時的 `SkillRegistry`、印出任何問題。在你自己的 CI 裡、發佈前用它。

CLI 住在 [core/cli.py](core/cli.py) —— stdlib `argparse`、無額外依賴、~300 行。它*刻意*和 `main.py` 分開:scaffolding 一個 skill 不應該要求啟動整個 runtime 堆疊(provider、monitor、connectors)。

#### `bootstrap_validate` —— 在 boot 時大聲失敗

`SkillRegistry.bootstrap_validate(tool_registry=...)` 在 `main.py` 啟動期間跑一次,**在**每個 first-party 與經 entry-point 發現的 skill 都已註冊**之後**。它檢查四個 invariant,並在第一個失敗時 raise `RuntimeError`(沒有半啟動的 agent):

| 檢查 | 抓什麼 |
|---|---|
| `compat_version ∈ SUPPORTED_PROTOCOL_VERSIONS` | 對著一個未來 / 已移除的 protocol 版本寫的 skill。 |
| `requires_tools` 裡的每個名稱都存在於 `ToolRegistry` | 一個 skill 宣告它會呼叫 `searxng`,但操作員沒設定 SearXNG。 |
| `plan_verb` 在已註冊的 skills 之間是唯一的 | 兩個 skill 都宣稱 `SEARCH` —— 否則 PLAN menu 裡的靜默 shadowing 會把其中一個藏起來。 |
| `requires_caps` token 在 `WELL_KNOWN_CAPABILITIES` 裡 | 紀錄為 warning(非致命)—— caps 是建議性的 metadata。 |

agent 現在會拒絕用一組設定錯誤的 skill 跑起來。這是讓第三方 plug-in 擁有可預測失敗模式的**唯一**方法:壞掉的安裝會在 boot 時帶著明確錯誤死掉,而不是三小時後當 SLM 終於挑到那個失敗的動詞時才靜默退化。

#### `ToolManifest` 合約

上面的 Skill 層在 Tool 那邊有個雙胞胎:每個註冊進 [`ToolRegistry`](tools/registry.py) 的 Tool 都由一個 [`ToolManifest`](tools/manifest.py) 摘要 —— 一個 frozen、slotted 的 dataclass,核心在 boot 時會 inspect 它。first-party 的 tools([SearXNG](tools/searxng.py)、[ArchiveRead](tools/archive_read.py))出貨時都帶顯式 manifest;第三方 tools 預期也這樣做。沒有的話,會由 `resolve_tool_manifest()` 從分散的 class 屬性(`name`、`description`、`args_schema`、……)**合成**一個 manifest 做一個版本的向後相容 —— 與 Skill 側的 `resolve_manifest()` 同樣的模式。

```python
from tools import ToolManifest, ToolResult

class WeatherTool:
    manifest = ToolManifest(
        name="weather",
        description="Current conditions + 3-day forecast for a given lat/lon.",
        compat_version="tool-protocol/1",
        args_schema={
            "lat": {"type": "float", "required": True, "desc": "Latitude"},
            "lon": {"type": "float", "required": True, "desc": "Longitude"},
        },
        side_effects=False,
        requires_confirmation=False,
        requires_caps=("network.outbound",),   # operator-auditable
        config_key="weather",                   # reads cfg.plugins.weather.*
    )

    name = "weather"
    description = "Current conditions + 3-day forecast for a given lat/lon."

    async def call(self, **kwargs) -> ToolResult:
        ...
```

有兩個欄位值得特別注意:

- **`requires_caps`** —— 文件化 tool 觸碰的 runtime 表面積的能力 token。已知集合是 `network.outbound`、`filesystem.read`、`filesystem.write`、`gpio`、`actuator`、`subprocess`(見 `WELL_KNOWN_TOOL_CAPABILITIES`)。未知 token 會 log warning 但不會讓 boot 失敗,所以第三方套件可以出貨自己的慣例。
- **`config_key`** —— 設定後,[main.py](main.py) 裡的 [`ToolRegistry.bootstrap_validate()`](tools/registry.py) 呼叫會交叉檢查操作員的 `config.yaml` 裡是否存在 `cfg.plugins.<key>`。一個拼錯(`weatehr:` 而不是 `weather:`)會在 boot 時被抓到,而不是首次呼叫時才出事。

對應 Skill 那一側,`ToolRegistry.bootstrap_validate(plugins_config=cfg.plugins)` 在每個 first-party 與經 entry-point 發現的 tool 都已註冊之後跑一次。它檢查 `compat_version` 是否在 `SUPPORTED_TOOL_PROTOCOL_VERSIONS` 裡、`config_key` namespace 是否存在、未知能力 token 並記 log。Strict mode(預設)會在任何問題上 raise `RuntimeError` —— agent 拒絕啟動。

#### 第三方 Tool 套件(`pip install`)

Tool 層鏡像 Skill 層的 discovery 合約:

```toml
# Third-party tool author's pyproject.toml
[project.entry-points."opencrayfish.tools"]
weather = "opencrayfish_tool_weather:WeatherTool"
```

`pip install -e .` 註冊 entry-point;OpenCrayFish 下次 boot 時經由 [`tools/discovery.py`](tools/discovery.py) 拾起它 —— 它也會在同樣 fail-isolated 的合約下,在 `plugins/tools/` 裡尋找 `PLUGIN = MyTool` 的 drop-ins([§ Hybrid Discovery](#hybrid-discovery--pip-或拖放資料夾))。Scaffolder 對稱於 `skill new`:

```bash
opencrayfish tool new weather
cd opencrayfish-tool-weather
pip install -e .

opencrayfish tool validate opencrayfish_tool_weather:WeatherTool
```

`tool new` 寫出和 `skill new` 一樣的四檔形狀(pyproject.toml + 套件 + README + pytest),但產生的 class 暴露 `async def call(self, **kwargs) -> ToolResult`(Tool Protocol 動詞)而不是 `execute()`(Skill Protocol 動詞)。entry-point group 是 `opencrayfish.tools`。其他每一樣都一樣 —— 一樣的 fail-isolated discovery、一樣的 dry-register 驗證、一樣的 `bootstrap_validate` 失敗模式。

reference 實作住在 [`examples/opencrayfish-skill-echo/`](examples/opencrayfish-skill-echo/),用於 Skill 那一側;把它複製貼上改寫成新 plug-in 的標準「hello world」。

#### Hello-World Tool —— 一步一步(`bind_context`)

Skill 那邊上面有完整的 hello-world 走讀。Tool 那邊形狀一樣,只多一個重要的東西:**`bind_context`**。Tools 在 config 注入上和 Skills 對稱 —— 一個 Tool 只要實作 `bind_context(ctx: ToolContext)`,`ToolRegistry` 就會自動把操作員 config + 共用子系統 handle 送進來。不需要 factory-closure 那套舞步。

**Step 1. 建立 Tool class**(例如在你的 `opencrayfish_tool_weather` 套件內):

```python
"""Hello-World Tool — minimal example of the Tool plug-in contract."""

from __future__ import annotations

from typing import Any
import httpx

from tools import ToolContext, ToolManifest, ToolResult


class WeatherTool:
    manifest = ToolManifest(
        name="weather",
        description="Current conditions for a city (open-meteo, no key needed).",
        compat_version="tool-protocol/1",
        args_schema={
            "city": {"type": "string", "required": True, "desc": "City name"},
        },
        side_effects=False,
        requires_confirmation=False,
        requires_caps=("network.outbound",),
        config_key="weather",                 # cfg.plugins.weather.*
    )

    # Mirror manifest fields for back-compat / dashboard scrapers
    name = "weather"
    description = "Current conditions for a city."
    args_schema = {"city": {"type": "string", "required": True}}
    side_effects = False
    requires_confirmation = False

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._units = "metric"  # default until bind_context overrides it

    # ── opt-in: receive operator config + shared handles at boot ───────────
    def bind_context(self, ctx: ToolContext) -> None:
        cfg = ctx.plugins_config.get(self.manifest.config_key or self.name, {})
        self._units = cfg.get("units", "metric")
        # You can also stash ctx.monitor / ctx.provider / ctx.archive_path
        # if you need to participate in stress gating or read the long-term
        # archive. ToolContext is frozen — never mutate it.

    # ── Tool Protocol verb ──────────────────────────────────────────────────
    async def call(self, **kwargs: Any) -> ToolResult:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        city = kwargs["city"]
        # ... call the API, build a payload ...
        return ToolResult(ok=True, data={"city": city, "units": self._units})

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
```

**Step 2. 在你套件的 `pyproject.toml` 宣告 entry-point**:

```toml
[project.entry-points."opencrayfish.tools"]
weather = "opencrayfish_tool_weather:WeatherTool"
```

**Step 3. 在 OpenCrayFish 的 `config.yaml` 加一個操作員 config 切片**:

```yaml
plugins:
  weather:
    units: "metric"
```

**Step 4. 安裝 + 重啟。** 在 OpenCrayFish 的 venv 裡跑 `pip install -e ./opencrayfish-tool-weather`,重啟 agent。Boot 時你會看到:

```
INFO  TOOL registered name=weather protocol=tool-protocol/1 caps=(network.outbound,) ...
INFO  Discovered 1 external tool(s) via entry-points: weather
INFO  TOOL bind_context delivered to weather (cfg.plugins.weather: 1 keys)
```

`state/tools.json` 現在列出 `weather`;任何 Skill 都能用 `await ctx.tools.call("weather", city="Hong Kong")` 組合它。如果你忘了在 `config.yaml` 寫 `plugins.weather:` 區塊,`bootstrap_validate` 會帶著這個訊息中止 boot:

```
RuntimeError: ToolRegistry.bootstrap_validate: tool 'weather' declares
config_key='weather' but cfg.plugins.weather is missing.
```

這就是 Tool 合約的全貌:**一個 Manifest、一個可選的 `bind_context`、一個 `call`、一行 entry-point、一個 YAML 區塊。**

#### Argspec 執行期驗證

`SkillRegistry.invoke()` 和 `ToolRegistry.call()` 都會在派發**之前**,對 `manifest.args_schema` 跑一次**邊界期 argspec 檢查**。這個檢查住在 [`core/skills/argspec.py`](core/skills/argspec.py),由兩層共用 —— 修 bug 一個地方修、擴充 type 一個地方擴充。

它依序做什麼:

1. **注入預設值** —— 任何在 schema 帶 `default` 而 `kwargs` 缺鍵的項目,會把 default 寫進去。
2. **強制 `required`** —— 帶 `required: True` 但(注入 default 後)仍無值的項目,在 skill/tool 跑之前就會產生 `argspec:` 錯誤。
3. **安全轉型** —— `str → int / float / bool`(例如 `"42" → 42`、`"true" → True`),和 numeric → `str`。模糊狀況會大聲拒絕(Python 的 `bool` 絕不會默默被當成宣告為 `int` 的位置傳過去)。
4. **追蹤未知 kwargs** —— 任何不在 schema 描述裡的東西會被回到 `meta["argspec_unknowns"]` 做稽核(warn,非致命 —— 所以累積式 schema 不會破壞舊的 caller)。

失敗時 skill/tool 回傳 `SkillResult(ok=False, error="argspec: ...")` / `ToolResult(ok=False, error="argspec: ...")`,結構化細節在 `meta["argspec_errors"]` 與 `meta["argspec_unknowns"]` 之下。Dispatcher 用和任何其他失敗呼叫一樣的方式把失敗記在 audit log —— 沒有針對「驗證失敗」的另一條路徑,所以 LLM call site 有 bug 也沒辦法靠 catch 不同 exception type 來繞過檢查。

這層的測試套件是 [`tests/test_argspec.py`](tests/test_argspec.py) —— 17 個測試,涵蓋轉型、預設值、缺少必要欄位、未知 kwarg 處理,以及兩個 registry 的端到端整合。

#### Plugin Config 命名空間(`cfg.plugins.*`)

第三方 Skills 與 Tools 需要一條接收操作員設定的路,而**不**用 patch [core/config.py](core/config.py)。`cfg.plugins.*` 命名空間就是這條接縫:

```yaml
# config.yaml
plugins:
  weather:
    api_key: "${WEATHER_API_KEY}"   # consumed by WeatherTool
    units: "metric"
  translate:
    backend: "deepl"                 # consumed by TranslateSkill
    glossary_path: "memory/glossary.yaml"
```

核心永遠不讀這些子 dict 裡面。它們會被原封不動轉發到兩個地方:

- **Tools** —— 兩條對稱的路。(1)`ToolRegistry.bootstrap_validate(plugins_config=cfg.plugins)` 會在 Tool 宣告 `config_key="weather"` 但 `cfg.plugins.weather` 不存在時,讓 boot 失敗。(2)`ToolRegistry.bind_context(ctx)` 會把一個 frozen 的 `ToolContext`(攜帶 `plugins_config` + `soul` / `stm` / `monitor` / `provider` / `archive_path` / `designation` / `architect_name` / `architect_honorific`)送到每一個**選擇加入**(實作 `bind_context(ctx)`)的已註冊 Tool。first-party Tools(`SearXNG`、`ArchiveRead`)不需要它 —— 它們在 `main.py` 裡帶著自己的 config 被構造 —— 但這是文件化的接縫,第三方 Tool 透過它讀自己的 `cfg.plugins.<key>` 切片,不需要 factory-closure 舞步。見上面的 [Hello-World Tool walkthrough](#hello-world-tool--一步一步bind_context)。
- **Skills** —— 經由 [`SkillContext.plugins_config`](core/skills/base.py)(`Mapping[str, Mapping[str, Any]]`,包在 `MappingProxyType` 裡,所以 Skills 不能 mutate 操作員的 config)。Skill 在呼叫期取它的切片:

```python
async def execute(self, ctx: SkillContext, **kwargs) -> SkillResult:
    cfg = ctx.plugins_config.get(self.manifest.name, {})  # or .config_key
    backend = cfg.get("backend", "default")
    ...
```

這是第三方 plug-in 接收設定唯一需要的編輯:在你的 manifest 宣告 `config_key`(可選但建議,供 bootstrap 期驗證),然後在呼叫期從 `ctx.plugins_config[key]` 讀。**對 `core/config.py` 零修改、對 `main.py` 零修改、對 OpenCrayFish 零分叉。**

#### `ConnectorManifest` 合約

Connectors 是 agent 的 I/O 表面 —— Telegram、Web Chat,以及任何第三方傳輸(Discord、Matrix、MQTT、語音迴圈、webhook、……)。它們是 plug-in 堆疊裡的**第三層**,遵循與 Skills 和 Tools 一樣的 Manifest + Registry + Discovery 模式。

合約([`connectors/manifest.py`](connectors/manifest.py)):

```python
@dataclass(frozen=True, slots=True)
class ConnectorManifest:
    name: str
    description: str
    compat_version: str = "connector-protocol/1"
    requires_caps: tuple[str, ...] = ()      # e.g. ("network.outbound", "network.inbound")
    config_key: str | None = None             # cfg.plugins.<key> namespace
```

Connector 是 duck-type 的:它**必須**暴露 `name: str`、`manifest: ConnectorManifest`,以及至少 `async def start()` / `async def stop()` 其中一個。兩個 in-tree connector([`TelegramConnector`](connectors/telegram.py)、[`WebChatConnector`](connectors/web_chat.py))現在都出貨顯式 manifest;沒有的第三方 connector 會經由 `resolve_connector_manifest()` 從 class 屬性**合成**一個,作一個版本的向後相容(與 Skill/Tool 側相同模式)。

`ConnectorRegistry`([`connectors/registry.py`](connectors/registry.py))擁有 connector 生命週期:

- `register(connector)` —— 拒絕重複名稱、對 `SUPPORTED_CONNECTOR_PROTOCOL_VERSIONS` 驗證 `compat_version`。
- `bootstrap_validate(plugins_config, strict)` —— 當任何 connector 宣告了一個在 `cfg.plugins.*` 找不到的 `config_key` 時,在 boot 時大聲失敗(當 `plugins_config` 有給時)。
- `aclose_all()` —— 在**隔離**中呼叫每個 connector 的 `stop()`:有 bug 的 connector 的 `stop()` exception 會被 log,其他的還是會被乾淨地拆除。

#### 第三方 Connector 套件(`pip install`)

形狀和第三方 Skills/Tools 一樣 —— 一個普通的 Python 套件,`pyproject.toml` 宣告一個 `opencrayfish.connectors` entry-point:

```toml
[project.entry-points."opencrayfish.connectors"]
discord = "opencrayfish_connector_discord:DiscordConnector"
```

[`connectors/discovery.py`](connectors/discovery.py) 在 boot 時掃這個 group,以**fail-isolated** 方式匯入每個 entry —— 一個壞掉或缺失的第三方 connector 會 log 一個 error 然後被跳過;boot 會帶著其他健康的東西繼續。同一個檔案接著經由 [core/dropin.py](core/dropin.py) 走 `plugins/connectors/`,讓你可以原型一個傳輸(`PLUGIN = MyConnector` 在 `.py` 檔底端)而不用打包;見 [§ Hybrid Discovery](#hybrid-discovery--pip-或拖放資料夾)。

用 CLI scaffold 一個:

```bash
opencrayfish connector new discord
opencrayfish connector validate opencrayfish_connector_discord:DiscordConnector
```

`connector new` 寫出和 `skill new` / `tool new` 一樣的四檔形狀(pyproject.toml + 套件 + README + pytest),但產生的 class 暴露 `async def start()` + `async def stop()`(Connector 生命週期動詞)而不是 `execute()` / `call()`。其他每一樣都鏡像 Skill/Tool 流:一樣的 fail-isolated discovery、一樣的 dry-register 驗證(在 `validate` 裡)、一樣的 `bootstrap_validate` 失敗模式。

first-party 的 `TelegramConnector` 與 `WebChatConnector` 仍在 `main.py` 顯式構造 —— 它們吃子系統參考(brain、heartbeat、intent_router),這是 entry-point 無法供應的。Discovery 層純粹是給**累積式**第三方傳輸用的。

#### Provider Backends —— `BackendManifest` 與 Discovery

Provider 層(`core/provider.py`)是**最輕**的 plug-in 表面:沒有一個中央的 `BackendRegistry`,因為 `Provider` singleton 自己擁有現役的 primary/fallback backend(constructor 是圍繞 Pi 5 + AI HAT+ 部署目標建的)。primary/fallback 槽位是**operator-routable** —— 當 `cfg.hardware.primary_backend` 或 `cfg.hardware.fallback_backend` 命名了一個已發現的 backend 時,`Provider.from_config()` factory 會把那個槽位換到已發現的 instance。

合約([`core/provider_manifest.py`](core/provider_manifest.py)):

```python
@dataclass(frozen=True, slots=True)
class BackendManifest:
    name: str
    description: str
    compat_version: str = "backend-protocol/1"
    requires_caps: tuple[str, ...] = ()      # e.g. ("network.outbound", "gpu", "npu")
    config_key: str | None = None
```

兩個 in-tree backend([`HailoOllamaBackend`](core/provider.py)、[`OllamaBackend`](core/provider.py))都帶著適當的能力 token 攜帶顯式 manifest(Hailo 路徑帶 `npu`、兩者都帶 `network.outbound`)。

`discover_external_backends()` 在 boot 時走 `opencrayfish.provider_backends`,合約與其他 discovery 層一樣 fail-isolated;接著 `discover_dropin_backends()` 走 `plugins/backends/`([core/dropin.py](core/dropin.py)),讓本地 backend 不用打包就能加入。兩個列表會在 Provider 的 primary/fallback 路由跑之前合併 —— entry-points 在名稱重複時優先。結果會被 log,**而且** —— 當操作員要求時 —— 直接被導到 Provider 的某個槽位:

```yaml
# config.yaml — operator points the primary slot at a discovered backend
hardware:
  npu_acceleration: false             # (built-in Hailo path off)
  cpu_fallback_url: "http://localhost:11434"
  cpu_fallback_model: "qwen2:1.5b"
  primary_backend: "vllm-cuda"        # ← name from a third-party manifest
  # fallback_backend: null            # leave None to keep built-in OllamaBackend
```

Boot 時 `main.py` 啟動流程變成:

```text
1. Build a baseline Provider from cfg.hardware (Hailo or CPU per npu_acceleration).
2. discover_external_backends() — log "BACKEND Discovered N: vllm-cuda, ..."
3. If cfg.hardware.primary_backend or fallback_backend is set:
     - await baseline.aclose()         (close the httpx client cleanly)
     - rebuild Provider.from_config(cfg.hardware, discovered_backends=[...])
     - log "BACKEND Provider rerouted: primary=vllm-cuda fallback=<built-in>"
```

未知名稱會 raise `ValueError("hardware.primary_backend='typo' is not a discovered backend. Available: ['vllm-cuda']")` —— 在 boot 時大聲失敗,連同那些**確實**存在的名稱列表,讓操作員可以修拼錯。在 [`tests/test_provider_backend_routing.py`](tests/test_provider_backend_routing.py) 測過。

用 CLI scaffold 一個 backend:

```bash
opencrayfish provider new vllm-cuda
opencrayfish provider validate opencrayfish_backend_vllm_cuda:VllmCudaBackend
```

Backend 名稱接受連字號(慣例:`hailo-ollama-npu`);scaffolder 會正規化成 snake_case 給 Python 識別符用。產生的 class 實作最低限度的 Provider 合約 —— `name`、`manifest`、`async def generate(system_prompt, messages) -> str`、`async def aclose()` —— 剩下的由 `cfg.hardware.primary_backend` / `cfg.hardware.fallback_backend` 兩個旋鈕完成。

#### Protocol Surface 穩定性

發佈的 Skill + Tool Protocol 表面(`SkillResult` / `SkillContext` / `SkillManifest` / `Skill` Protocol annotation + `ToolManifest`)由 snapshot test **凍住**:[`tests/test_skill_protocol_surface_v1.py`](tests/test_skill_protocol_surface_v1.py) 為每一個第三方寫程式對著的 dataclass 欄位與 Protocol annotation 帶著明確的 `EXPECTED_*` 集合。任何添加、移除或更名都會讓測試失敗 —— 強迫改動作者要不就還原表面,要不就**刻意地** bump `SUPPORTED_PROTOCOL_VERSIONS` / `SUPPORTED_TOOL_PROTOCOL_VERSIONS` 並附 migration note。

配上 [`examples/opencrayfish-skill-echo`](examples/opencrayfish-skill-echo/) 的整合測試(行為端到端跑過一遍),這給第三方作者一個雙軸保證:

| 測試 | 抓什麼 |
|---|---|
| `test_skill_protocol_surface_v1.py` | 語法漂移 —— 在發佈型別上新加 / 更名 / 移除欄位。 |
| `test_example_echo_integration.py`  | 語意漂移 —— 參考 plug-in(每個第三方 plug-in 的代表)持續能 import、register、validate、execute。 |

如果你在動其中一個發佈型別、surface 測試失敗,正確的動作幾乎一定是還原改動。例外是:

1. **新增**一個帶 default 的欄位(向後相容)—— 把它 append 到對應的 `EXPECTED_*` 集合即可。在 dataclass docstring 簡短文件化。
2. **更名**或**移除**一個欄位,或改一個已出貨 plug-in 依賴的 default —— 這是破壞性的 protocol 變動。bump `SUPPORTED_*_PROTOCOL_VERSIONS`,新增 `"skill-protocol/2"` / `"tool-protocol/2"` / `"connector-protocol/2"` / `"backend-protocol/2"`,把 `"...1"` 留在 supported set 至少一個 release 並附上 deprecation log 行,然後在本節文件化 migration。

四個 protocol-version 常數分別住在 [`core/skills/manifest.py`](core/skills/manifest.py)、[`tools/manifest.py`](tools/manifest.py)、[`connectors/manifest.py`](connectors/manifest.py)、[`core/provider_manifest.py`](core/provider_manifest.py)。

---

### 13. Connectors ── Telegram 與 Web Chat

**模組:** [connectors/telegram.py](connectors/telegram.py) · [connectors/web_chat.py](connectors/web_chat.py) · [ui/web_chat.py](ui/web_chat.py)

兩個 connector 在同一個 `main.py` event loop 裡跑,共用**同一份** Brain / STM / Heartbeat / Scheduler instance。

#### TelegramConnector

- python-telegram-bot polling。
- 對著 `cfg.api_keys.telegram_user_id` 驗證入站訊息 —— 只有設定過的建構者能跟 agent 說話。
- 把 `/emergency <msg>` 認成睡眠繞過 marker(02:00–06:00 之間唯一會被回應的訊息類型)。
- Slash 指令:`/start`、`/tasks`、`/cancel`、`/pause`、`/resume`、`/research`。
- 排程任務報告(`_deliver_report`)在短暫 `NetworkError` / `TimedOut` 上會以指數退避自動重試最多 3 次,並遵循 Telegram 的 `RetryAfter` cooldown —— 一次 TCP 抽搐再也不會默默丟掉一份每 10 分鐘的循環報告。

#### WebChatConnector(行內 aiohttp)

一個小小的 aiohttp server 住在 Telegram polling loop 旁邊,暴露:

| 端點 | 用途 |
|---|---|
| `POST /chat` | 對現役 agent 送訊息;回傳 `{reply, backend, stressed, elapsed_ms, mood_active_channel, ...}` |
| `GET /state` | chat header 用的最小 snapshot(designation、sleeping、brain_online、backend) |
| `GET /history?limit=N` | 給瀏覽器重建對話用的 newest-last STM 摘錄 |
| `GET /healthz` | Liveness check |

安全性:

- 預設 bind 到 `127.0.0.1` —— 不暴露到 LAN。
- 可選的 `web_chat.auth_token` 共享密鑰,經由 `X-OCF-Token` header 帶。
- `respect_sleep_metabolism: true` 鏡像 Telegram 的睡眠 gate(除非 `emergency=true`,否則回 423 Locked)。

附帶的 [ui/web_chat.py](ui/web_chat.py) Streamlit app 是這些端點的前端 —— **同一個現役 agent**,沒有第二個 instance。

---

### 14. 可觀測性 ── Dashboard 與狀態檔

**模組:** [ui/dashboard.py](ui/dashboard.py)

Dashboard 在 port 8501 跑成獨立的 Streamlit process,讀取 Heartbeat 發佈的狀態檔。**零 IPC**,只是原子寫入的 JSON / JSONL。經由 `streamlit-autorefresh` 每 5 秒自動 refresh(可選依賴 —— 套件沒裝時退回手動「Refresh now」按鈕)。

#### 現役面板(由上到下)

| 面板 | 來源 | 它告訴你什麼 |
|---|---|---|
| **Vitals strip** | `state/vitals.json` | CPU / RAM / Temp / Brain backend(線上 + 變體)/ Mood(主導 + 活躍 channel)/ STM 大小 / 待寫入數 / liveness chip(以 snapshot 年齡判 ALIVE / STALE / DEAD) |
| **Pulse history sparkline** | 從 `state/vitals.json` 歷史衍生 | 30s/pulse 滾動 ~1 小時 —— 快速看出 Pi 是不是越來越熱 |
| **Heartbeat log (today)** | `<memory.log_path>/YYYY-MM-DD.log` | agent 時區當天的原始 heartbeat 遙測,附「Notable events」過濾器展開(PROACTIVE / VITALS / Sleep Metabolism / Awakening) |
| **💬 Live chat activity (last 30)** | `state/logs/agent.log` 過濾到 `CHAT / TG / WEB / TASK / TOOL / SKILL` 前綴 | 每回合的軌跡,附五個摘要 metric(Turns / Web-grounded / Triage SEARCH / LTM short-circuit / Search FAILED)。顏色編碼的行讓決策一眼可見 |
| **⚠️ Errors & warnings (last 20)** | `state/logs/agent.log` 過濾到 `[ERROR] / [WARNING] / [CRITICAL]` 等級前綴 | 操作員的「有東西壞了嗎?」面板。Header 徽章顯示計數;當任何 error 或 critical 出現時自動展開。空白狀態 = 明確的綠色。抓到結構化 chat filter 會藏掉的失敗(circuit breaker 跳脫、SearXNG outage、soul-protection 拒絕) |
| **Mood vector (5-D)** | `state/vitals.json` | joy / anger / sorrow / excitement / calm 的長條圖 + 主導 + 非 baseline 活躍 channel 讀數 |
| **⚡ Vitals stress events** | `state/vitals_events.jsonl` | 按時序的 ENTER / EXIT 時間軸,附 peak 讀數 —— 看 agent 何時很熱、熱了多久 |
| **🧬 Mood event log (last 20)** | `state/logs/agent.log` 過濾到 `MOOD ` 前綴 | 由 `Emotions.nudge_many()` 與 `decay()` transition 發出 —— 讓你追為什麼 mood vector 動了 |
| **Short-Term Memory** | `state/vitals.json`(最後幾回合 echo 進去) | RAM deque 裡最後幾個 user/agent 回合 |
| **🔬 Autonomous learning feed (last 5)** | `state/proactive.jsonl` | 每個主動思考,附完整的 triage_decisions 稽核 + 最終 draft + 投遞狀態 |
| **🧠 Cognitive deliberations (last 5)** | `state/deliberation-YYYY-MM-DD.jsonl`(rotated,**跨所有 sibling 展開**) | 每回合的 THINK → PLAN → ACT → REFINE trace,附 verb、evidence 摘要、延遲 |
| **⏱️ Scheduled research tasks** | `state/tasks.yaml` | 現役循環任務 registry,附 next-run 時戳 + last-report 預覽 |
| **🔌 Tool registry** | `state/tools.json` | 每個插進來的 Tool 的 Name / description / args_schema / side-effect 旗標 |
| **🎯 Skill registry** | `state/skills.json` + `state/skills-YYYY-MM-DD.jsonl`(rotated,**跨所有 sibling 展開**) | 已註冊 Skills 的 inventory,附其 PLAN-menu 動詞 + 最近呼叫(timing / ok-fail / kwargs keys / last error) |
| **🪞 Self-reflection feed (last 8)** | `state/reflection-YYYY-MM-DD.jsonl`(rotated,**跨所有 sibling 展開**) | 每回合的批判 + lesson + interest 主題,Sleep Metabolism 在 02:00 會挖這些 |
| **Soul (read-only)** | `soul.md` | agent 身分 + 學習成長的原始檢視 |
| **Memory archive (last 2 KB)** | `memory/archive.md` | LTM 檔的尾巴 —— 看 Sleep Metabolism 在 promote 什麼 |

#### 輪替展開(Rotation fan-out)

三個高頻 feed —— `deliberation`、`skills`、`reflection` —— 由 [core/jsonl_writer.py](core/jsonl_writer.py) 的 `RotatingJsonlWriter` 寫出,每個本地日產生一份新的 `<feed>-YYYY-MM-DD.jsonl`。Dashboard 的 `_rotated_jsonl_tail()` / `_rotated_jsonl_all()` helper 會發現每一個符合該 pattern 的 sibling,以 newest-last 走訪,**並且**append 舊式未輪替的 `<feed>.jsonl`(如果有操作員留了一個 pre-rotation 部署的話),讓讀取能撐過午夜跨越與輪替切換。檔名 regex 守衛意味著 reader 永遠不會誤吃操作員的筆記或名稱相近的外部檔案。由 [scripts/smoke_dashboard_rotation.py](scripts/smoke_dashboard_rotation.py) 驗證。

#### 磁碟上的 log

- `state/logs/agent.log` —— 在 [main.py](main.py) 設好的輪替式 console mirror(`RotatingFileHandler`,2 MB × 5 檔)。每一個結構化事件(`CHAT / TG / WEB / TASK / TOOL / SKILL / MOOD / VITALS / FOREGROUND / PROACTIVE`)加上所有 `[INFO] / [WARNING] / [ERROR] / [CRITICAL]` 行都落地在這。這是 dashboard 的 chat-activity 與 errors-and-warnings 面板 tail 的唯一來源。
- `<memory.log_path>/YYYY-MM-DD.log` —— 每日的 heartbeat 遙測(PULSE / PROACTIVE / Sleep Metabolism)。預設 `logs/daily/`。由 [core/heartbeat.py](core/heartbeat.py) 的 `_append_log()` 同步寫入。
- `state/*-YYYY-MM-DD.jsonl` —— 按日輪替的結構化稽核 feed(見 [§ JSONL 輪替與保留](#jsonl-輪替與保留))。

##### Foreground-priority 監測

每一個現役對話回合都會在 `state/logs/agent.log` 標出開頭與結尾,而每一個 yield 出去的背景週期會留下單一一行說明:

| 來源 | 格式 | 何時 |
|---|---|---|
| `core.brain` | `FOREGROUND start depth=N input_chars=M` | `Brain.think()` 入口 —— `N` 是遞增後的 foreground depth counter(≥1 代表至少有一個現役回合在進行;≥2 代表多 connector 並行) |
| `core.brain` | `FOREGROUND end depth=N dur_ms=X` | `Brain.think()` 出口(success 或 exception —— 由 `finally` 發出)—— 給每回合 wall-clock 延遲 |
| `core.heartbeat` | `PROACTIVE yield_to_foreground stage=<x> topic=<y> — Architect is active.` | 自主主動週期在三個檢查點之一被遞延(`topic_selection` / `pre_search` / `pre_synthesis`);還沒解出主題時 `topic=(pre-topic)` |

這三行透過既有的 `CHAT / TG / WEB / TASK / TOOL / SKILL` 過濾流到 dashboard 的 **💬 Live chat activity** 與 **⚠️ Errors & warnings** 面板(FOREGROUND / PROACTIVE 透過同一個 `agent.log` tail 浮現),讓操作員一眼看到建構者何時打斷了背景研究、現役週期跑了多久。

---

## 橫切關注點 (Cross-Cutting Concerns)

上一節按子系統各自走過。本節說明跨越每個子系統、且共同讓 24/7 single-board agent 變得可行的四個設計選擇。

### 並行模型 (Concurrency Model)

OpenCrayFish 是一個**單程序、單一 event loop 的 asyncio** 應用。每一個子系統都靠 `await` 合作;沒有 threads、沒有 `multiprocessing`、沒有 message queue。Event loop 確切擁有以下任務 —— 在 `main.py` 裡可見:

| 任務名 | Coroutine | 節奏 | 取消 |
|---|---|---|---|
| `heartbeat` | `Heartbeat.pulse_loop()` | 每 `pulse_interval_seconds` | `heartbeat.stop()` 設內部 `asyncio.Event` |
| `scheduler` | `TaskScheduler.run_loop()` | 每 `tasks.tick_seconds` | `scheduler.stop()` 設內部 `asyncio.Event` |
| `Updater.start_polling` | python-telegram-bot 內部 | 每次 Telegram long-poll | `tg_app.updater.stop()` |
| WebChat aiohttp `AppRunner` | aiohttp 服務 thread | 每次 HTTP 請求 | `web_chat.stop()` |
| 每回合 Brain cycle | `Brain._cycle()` | 由 connector 調用 | 繼承自 connector handler |
| 背景 reflection | `ReflectionEngine.fire_and_forget()` | Brain 回覆後調用 | 跑完或 log |

可變狀態用**細粒度 `asyncio.Lock`** 保護,不是 global lock:

- `Emotions._lock` —— 保護 5-D vector。`nudge_many()` 在多 channel 更新期間持有它,讓 `decay()` 不能插進來(否則連續的 `await nudge(); await nudge()` 會讓 heartbeat 在更新中間溜進去)。
- `SoulHandler._lock` —— 保護每一次 soul.md 讀寫,讓並行的 metabolism + reflection consolidation 不能交錯。
- `ShortTermMemory._lock` —— 保護 deque + pending buffer + journal write path。

因為 Provider 的 HTTP 呼叫是 async 的(`httpx.AsyncClient`),一個長跑的 SLM 呼叫**不會**擋住 heartbeat —— loop 只是 yield,heartbeat 會在預定的間隔點觸發。每回合的 brain cycle 可以舒服地與同一個 event loop 上飛行中的 scheduler 任務觸發重疊。

#### Foreground priority —— 不靠鎖的協作式 yield

有一個 lock 解不了的協調缺口。Hailo-10H 暴露單一一個 inference queue;當自主 proactive 週期在飛行中(5–10 秒一連串 SLM 呼叫),從 Telegram 來的一個全新 `Brain.think()` 會排在它後面,建構者就在等。這不是 race —— 每一個共享檔都已經鎖過了 —— 它是 NPU 上的一個**延遲優先級反轉**。鎖修不了,因為冒犯的工作是合法地持有資源;我們需要它*讓位*。

解法是**訊號,不是鎖**:

- `Brain` 擁有一個 int `_foreground_depth` counter,在 `think()` 入口遞增、在 `finally` 遞減。因為 asyncio 是單執行緒,`await` 點之間的 int 加減是 atomic 的 —— 不需要 `Lock`。Counter(而不是 boolean `Event`)處理 Telegram + Web Chat 同時各有一個現役回合的合法情況:它一直 assert 著,直到**所有** foreground 回合都結束。
- `Brain.is_foreground_busy()` 把 counter 暴露成單一 int 比較。背景子系統 poll 它 —— 便宜、無副作用、從任何 coroutine 呼叫都安全。
- `Heartbeat._proactive_research()` 在三個里程碑呼叫 `_yield_to_foreground()` —— `topic_selection`(STM-gap triage 呼叫之前)、`pre_search`(SearXNG round-trip 之前)、`pre_synthesis`(最大那次 SLM 呼叫之前)。當訊號 assert 著時,週期回傳 `None`,下一個閒置窗會從頭重試。沒有狀態被 mutate 過,所以什麼都沒丟。最大 yield 延遲被下一次 SLM 呼叫所限制 —— 大約 1 秒上限。

這個形狀背後的設計選擇:

- **協作式,不是搶占式。** 我們從不 `task.cancel()` 背景週期。對 NPU server 的 HTTP 呼叫被取消會讓 SLM queue 處於半未知狀態;在乾淨的里程碑協作式 bail 對一個乾淨的 shutdown 來說,最多多花一次 SLM 呼叫的延遲。
- **只有自主路徑會 yield。** 操作員發起的 `/research [topic]`(`topic_override` 設了)會繞過每一個檢查點 —— 默默丟掉操作員明確的指令,比和另一個 foreground 回合並行還糟。
- **`TaskScheduler` 不 yield。** 循環任務是有已知節奏的、操作員排程的交付物;延後它們會冒著錯過下一個排程報告的風險。把一次任務觸發和現役回合重疊的延遲成本(最壞情況多等一次任務觸發)是可接受的代價。
- **訊號就是稽核。** 每一次 yield 都會發出單一一行 `PROACTIVE yield_to_foreground stage=<x> topic=<y>` 到 `state/logs/agent.log`,讓 dashboard 的 chat-activity 面板把這個禮讓視覺化給操作員。檢查點表見 [§ 9 主動性](#9-主動性--把閒置時間變成成長時間)、log 格式見 [§ 14 可觀測性](#14-可觀測性--dashboard-與狀態檔)。

由 [scripts/smoke_foreground_priority.py](scripts/smoke_foreground_priority.py)(5 個檢查:success + exception 上的 counter 生命週期、並行回合 depth 語意、yield helper、自主 bail、操作員 bypass)驗證。

### 原子寫入無處不在

每一個 dashboard 會讀的狀態檔都是經由 **`tmp + os.replace`** 原子替換模式寫入。這保證單獨 process 的 reader(Streamlit)即使 agent 在寫入中間 crash,也永遠不會看到半寫的 JSON 檔。參考實作(`main.py:_publish_tools_inventory`):

```python
tmp = out_path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
tmp.replace(out_path)   # POSIX atomic rename on the same filesystem
```

同樣的模式被以下使用:

| Writer | 檔案 | Reader |
|---|---|---|
| `Heartbeat._publish_state` | `state/vitals.json` | `ui/dashboard.py` |
| `main._publish_tools_inventory` | `state/tools.json` | `ui/dashboard.py` |
| `main._publish_skills_inventory` | `state/skills.json` | `ui/dashboard.py` |
| `TaskScheduler._save` | `state/tasks.yaml` | `ui/dashboard.py` + scheduler bootstrap |
| `SoulHandler._write` | `soul.md` | 每個 Brain cycle |
| `STM._fsync_journal`(append-only) | `state/stm_journal.jsonl` | boot 時的 `STM.recover()` |
| `RotatingJsonlWriter.append`(append-only、按日輪替) | `state/{skills,deliberation,reflection,reflection_dropped}-YYYY-MM-DD.jsonl` | `ReflectionEngine.read_recent*` + `ui/dashboard.py` |

對 append-only 的 feed(`*.jsonl`),atomicity 來自 OS 對單次 `write()`(buffered 後一行完整 JSON)不會被撕裂的保證 —— 加上每次 `write()` 一定發出一筆完整紀錄。高頻 feed(skills、deliberation、reflection、reflection_dropped)更進一步,經由 [core/jsonl_writer.py](core/jsonl_writer.py) 的 `RotatingJsonlWriter`(下一節)。

### JSONL 輪替與保留

**模組:** [core/jsonl_writer.py](core/jsonl_writer.py)

有三個子系統以對話頻率 append:`SkillRegistry._audit`(每次 Skill 呼叫一行)、`CognitiveLoop._persist`(每次 cognitive trace 一行)、`ReflectionEngine._persist` / `_persist_dropped`(每回合一條批判 + sidecar)。24/7 跑在 Pi 5 上,這些檔案會無上限成長到無法讀。`RotatingJsonlWriter` 用三個保證解掉:

1. **按日按檔名輪替。** 現役檔案名稱嵌入本地日期 —— 例如 `state/skills-2026-05-17.jsonl`。新日期 → 新檔。我們永遠不 rename 或 truncate,所以握著 file descriptor 的 tail-reader 永遠不會因為 rename race 看到半寫的紀錄。
2. **行級原子 append。** `os.open(..., O_WRONLY|O_CREAT|O_APPEND)` + 每筆紀錄一次 `os.write(fd, line)`。POSIX `O_APPEND` 即便在並行 process writer 之下也保持 byte-level 原子寫入;per-instance 的 `asyncio.Lock` 進一步序列化 Python 端的 writer,讓 executor pool 不能在同一個 file descriptor 上 race。
3. **有界保留。** 每一個本地新日的第一個 append 會發起一次 `_sweep_blocking(today)` pass,`unlink()` 任何符合 `<base>-YYYY-MM-DD.jsonl`、比 `retain_days` 還老的 sibling。不符合 pattern 的檔案(操作員的筆記、其他 stem 的外部 feed)**永遠不**會被動到 —— regex 守衛就是那道安全牆。

預設保留窗(每個 owner 自己挑 —— 較大的 feed 拿較短的窗):

| Feed | Owner | 保留 | 為什麼 |
|---|---|---|---|
| `skills-*.jsonl` | `SkillRegistry` | 30 天 | 小的 row,debug「research 又壞了嗎?」很有用的軌跡 |
| `deliberation-*.jsonl` | `CognitiveLoop` | 14 天 | 整份 THINK/PLAN/ACT/REFINE payload —— 最大的 row |
| `reflection-*.jsonl` | `ReflectionEngine` | 60 天 | 被 Sleep Metabolism 挖(24h lookback)—— 留寬窗給趨勢分析 |
| `reflection_dropped-*.jsonl` | `ReflectionEngine` | 60 天 | 給操作員看的、無法解析批判的 forensics |

`stm_journal.jsonl`、`vitals_events.jsonl`、`proactive.jsonl` **不**輪替 —— `stm_journal` 每晚由 Sleep Metabolism 的 `purge()` truncate、另外兩個成長夠慢,可由操作員管理。`RotatingJsonlWriter` 只用在每回合寫頻率高到值得 rotation 成本的地方。

Readers(`ReflectionEngine.read_recent`、`read_recent_skills`、`summarise_skills_recent`)會掃所有輪替的 sibling **加上**任何 base path 上的舊式未輪替檔 —— 對切換後沒重啟過的部署向後相容。排序按紀錄裡解析出來的時戳,不是檔名順序,所以在 00:00 跨日讀就是會動。

### 失敗模式矩陣

每一個子系統都被設計成在它的依賴壞掉時**退化,不是崩潰**。下表是操作員對「當 X 死了 agent 會做什麼」的心智模型:

> 下列所有失敗都會落地在 `state/logs/agent.log`,等級為 `[WARNING]` / `[ERROR]` / `[CRITICAL]`,並在 dashboard 的 **⚠️ Errors & warnings** 面板浮現(出現非 warning 時自動展開)。「Surface to Architect」欄描述使用者可見的行為;面板是操作員可見的對應物。

| 失敗 | 對建構者可見的表面 | 復原 |
|---|---|---|
| **兩個 inference backend 都掛了** | `ProviderUnavailable` 在 `Brain._cycle` 頂端 raise 一次 → 合成的 `ThoughtTrace(backend="offline")`,connector 渲染友善的第一人稱訊息。Dashboard 顯示 🔴 BRAIN OFFLINE chip。 | Circuit-breaker 在 `trip_seconds`(預設 30 秒)後的下一次呼叫自動復原。重啟 `ollama serve` 或 `hailo-ollama`。 |
| **NPU backend 掛了、CPU 還活著** | 靜默 fallback。`provider.active_backend` 翻成 `"ollama-cpu"`,dashboard chip 更新。沒有對建構者可見的訊息。 | Provider 每次呼叫都會重試 primary;一旦 NPU 回來,下一次呼叫就會路回它。 |
| **SearXNG 掛了** | Web triage fail open:cognition 的 `SEARCH` evidence 是空的,但 `RECALL` 與 `ANSWER` 仍會產生回覆。背景的 `tools/searxng.py` log `WARN`。 | 重啟 SearXNG 容器。agent 端無狀態需要清。 |
| **SD card 滿了 / 寫入失敗** | STM `flush_journal()` 紀錄 exception 然後繼續。deque + pending buffer 持續在 RAM 累積。Sleep Metabolism 的 `_append_archive()` 同樣容忍寫入失敗。 | 釋出磁碟空間;下次閒置 flush 會成功,被緩衝的回合會被保留。 |
| **STM journal 損毀** | `STM.recover()` 跳過畸形的行繼續走。最壞情況:零回合被 rehydrate。 | 不需要 —— 下一次 Sleep Metabolism 的 `purge()` journal 會被 truncate 到零。 |
| **soul.md mutation 被拒** | `metabolism()` 裡 raise 的 `SoulProtectionError` 被捕捉並記 log。沒有部分寫入發生(原子替換)。 | 建構者檢查 `state/proactive.jsonl` 裡被建議的 Core Memory;其他不需要做。 |
| **Cognitive Loop 無法 parse THINK 輸出** | Loop 以 `engaged=False`、`bypass_reason="think_unparseable"` bail。Brain 退回到 legacy 單發路徑。Trace 仍 append 到 `state/deliberation-YYYY-MM-DD.jsonl` 供 forensics。 | 不需要 —— 每個週期都獨立。 |
| **boot 時 Skill 設定錯誤**(未知的 `compat_version`、缺 `requires_tools` 項目、重複的 `plan_verb`) | `SkillRegistry.bootstrap_validate()` 在 connectors 啟動**之前**就 raise `RuntimeError`,精確列出每一個冒犯的 Skill + 問題。Process 以非零退出。**大聲失敗,而不是慢慢失敗。** | 操作員檢查 error、修第三方套件(或用 `pip uninstall` 移除),重啟。見 [§ `bootstrap_validate` —— 在 boot 時大聲失敗](#bootstrap_validate--在-boot-時大聲失敗)。 |
| **boot 時 Tool 設定錯誤**(未知的 `compat_version`、宣告的 `config_key` 在 `cfg.plugins` 找不到) | `ToolRegistry.bootstrap_validate(plugins_config=cfg.plugins)` 在 Skill registry 建立**之前**就 raise `RuntimeError`,列出冒犯的 tool + 問題。Process 以非零退出 —— 與 Skill 端相同的「大聲失敗、不要慢慢失敗」合約。 | 操作員要不就在 `config.yaml` 加上缺的 `cfg.plugins.<key>` 區塊、要不就 `pip uninstall` 冒犯的第三方 tool,然後重啟。見 [§ `ToolManifest` 合約](#toolmanifest-合約)。 |
| **呼叫期 Argspec 驗證失敗**(缺必要參數、無法轉型的型別、schema 不符) | `SkillRegistry.invoke()` / `ToolRegistry.call()` 回傳 `SkillResult(ok=False, error="argspec: ...")` / `ToolResult(ok=False, error="argspec: ...")`,結構化細節在 `meta["argspec_errors"]` 之下。skill/tool body **絕不**被呼叫;失敗會像任何其他失敗呼叫一樣被稽核 log。 | Cognition 把這當作任何其他失敗的 Skill —— 週期繼續、合成 prompt 把錯誤字串當 evidence、SLM 自然地道歉。見 [§ Argspec 執行期驗證](#argspec-執行期驗證)。 |
| **boot 時 Connector 設定錯誤**(未知的 `compat_version`、宣告的 `config_key` 在 `cfg.plugins` 找不到) | `ConnectorRegistry.bootstrap_validate(plugins_config=cfg.plugins, strict=True)` 在 connectors 啟動**之前**就 raise `RuntimeError` —— 與 Skill / Tool 端相同的 fail-loud 合約。經由 entry-points 載入的壞掉第三方 connector 在 discovery 層 fail-isolated(log + 跳過),所以只有它*自己*的 transport 缺,而不是整個 agent。 | 操作員要不就修缺的 `cfg.plugins.<key>` 區塊、要不就 `pip uninstall` 冒犯的 connector。見 [§ `ConnectorManifest` 合約](#connectormanifest-合約)。 |
| **Connector outage**(Telegram API rate-limit 或暫態 `NetworkError`) | 入站的 `python-telegram-bot` polling 內部重試。出站的排程任務投遞(`_deliver_report`)在暫態 `NetworkError` / `TimedOut` 上以指數退避自我重試最多 3 次,並遵循 `RetryAfter` cooldown。agent 狀態不受影響;回覆排隊、API 復原時排出去。 | 不需要 —— 等 rate-limit 窗。 |
| **Heartbeat coroutine raise** | Exception 傳播;Heartbeat task 死掉;`main.amain()` 裡的 `await pulse_task` raise,process 非零退出。 | 重啟 agent(建議用 systemd unit)。boot 時 STM 從 journal rehydrate。 |
| **自主 proactive research 進行中、建構者開口** | 週期在下一個里程碑 yield(≤1 SLM 呼叫延遲,~1 秒上限);沒有狀態 mutation,因為什麼都還沒持久化。現役 `think()` 以正常延遲進行。`PROACTIVE yield_to_foreground` 行落在 `state/logs/agent.log`。 | 自動 —— 下一個閒置窗(`idle_proactive_minutes` 沉默之後)從頭重啟週期。手動 `/research [topic]` 完全繞過 yield。 |

重複的主題:**每回合 / 每 pulse 的工作是獨立的**,所以單一失敗永遠不會毒害下個週期。唯一不可活的失敗是 `Heartbeat` task 本身死掉,因為留一個沒有心臟的身體活著沒意義 —— 比起靜默凍住,在 systemd 下大聲失敗更好。

### Pi 5 延遲預算

下表數字是參考部署(Pi 5 + AI HAT+ 2 / Hailo-10H、qwen2.5-instruct:1.5b NPU 主)的典型情況。把它們當作 ballpark;唯一權威的數字是你自己硬體上 `state/deliberation-YYYY-MM-DD.jsonl` 紀錄的。

| 階段 | 典型 (ms) | 備註 |
|---|---|---|
| `Monitor.sample()` 第一次呼叫 | ~100 | `psutil.cpu_percent(interval=0.1)` 為取樣窗 block |
| `Monitor.sample()` cached(≤ `vitals_cache_ttl_seconds`) | <1 | 每個 Brain 週期的熱路徑 |
| `Empathy.analyze()` | <5 | 無依賴詞典掃描 |
| `PositiveFilter.apply()` | <2 | regex sweep |
| `LTM keyword scan`(archive.md ≤ 1000 行) | 5–20 | 線性讀;受 `archive.md` 大小限制 |
| `Identity short-circuit` reply(全套) | <10 | 零 SLM 呼叫(委派給 `IdentitySkill`) |
| `SkillRegistry.invoke()` wrapper overhead | <1 | timing + audit append(executor offload) |
| `RotatingJsonlWriter.append` | <5 | 每筆 `os.write()` 在 executor;sweep ≤1/日 |
| `Cognition THINK`(120 tok cap) | 400–800 | 一次 NPU 呼叫 |
| `Cognition PLAN`(120 tok cap) | 400–800 | 一次 NPU 呼叫;menu 由 `SkillRegistry.plan_menu()` 建(~<1ms) |
| `Cognition ACT`(每個 `SEARCH`) | 200–500 | 經由 `research` Skill → SearXNG round-trip + parse 派發 |
| `Cognition ACT`(每個 `RECALL`) | <20 | 經由 `recall` Skill → local archive scan 派發 |
| `Cognition REFINE`(40 tok cap) | 200–400 | 一次 NPU 呼叫(僅當還有 gap) |
| `Brain.synthesize`(最終 SLM 呼叫) | 800–1500 | 一次 NPU 呼叫,較長 output cap |
| `Reflection.fire_and_forget`(背景) | 600–1000 | 不會加到使用者感知的延遲 |

端到端的 **engaged-turn 預算(NPU 上)**:對使用者可見的回覆,典型 2–4 秒 wall-clock,外加一次 reflection 的背景 SLM 呼叫。**CPU-fallback** 大約慢 4–8 倍(Pi 5 ARM core 上的 qwen2:1.5b 沒有 matmul 加速優勢)。Stress mode 對非複雜輸入繞過 cognition,正是因為單純 synth 的路徑比完整 loop 快約 3 倍。

Cognitive Loop 的每階段 token cap(`_MAX_THINK_TOKENS=120`、`_MAX_PLAN_TOKENS=120`、`_MAX_REFINE_TOKENS=40`,在 [core/cognition.py](core/cognition.py))**不是**裝飾性的 —— 它們是把 engaged-turn 延遲在 1.5B 參數模型上限制住的關鍵 constraint。

---

## 設定參考 (Configuration Reference)

系統裡每一個行為都由 `config.yaml` 控制。主要區塊(每個欄位的行內註解見檔案):

```yaml
system:
  individual_designation: "Dave Minion"   # agent's name (single source of truth)
  timezone: "Asia/Hong_Kong"
  duty_start: "06:00"
  sleep_start: "02:00"
  architect_name: "Eason"                 # how the agent addresses you
  architect_honorific: "Boss"
  pulse_interval_seconds: 30
  idle_proactive_minutes: 5
  idle_journal_flush_seconds: 30
  journal_fsync_on_flush: false

hardware:
  npu_acceleration: false                 # true on Pi 5 + Hailo HAT+
  hailo_ollama_url: "http://localhost:8000"
  hailo_model: "qwen2.5-instruct:1.5b"
  cpu_fallback_url: "http://localhost:11434"
  cpu_fallback_model: "qwen2:1.5b"
  thermal_limit_celsius: 75.0
  ram_limit_pct: 85.0
  thermal_release_celsius: 0.0            # 0 = auto-derive (limit - 5)
  ram_release_pct: 0.0
  vitals_cache_ttl_seconds: 5.0
  primary_backend: null                   # v3: name a discovered backend to take the primary slot
  fallback_backend: null                  # v3: name a discovered backend to take the fallback slot

api_keys:
  telegram_token: "..."
  telegram_user_id: "..."                 # @userinfobot

tools:
  searxng_url: "http://localhost:8080"
  web_search_triage_enabled: true
  ltm_short_circuit_enabled: true
  ltm_short_circuit_min_score: 2

memory:
  stm_max_turns: 12
  archive_path: "./memory/archive.md"
  log_path: "./logs/daily"

reflection:
  enabled: true
  reflect_on_user_turn: true
  reflect_on_proactive: true

proactive:
  stm_gap_extraction_enabled: true
  max_candidates_per_cycle: 3
  fallback_to_preferences: true
  triage_known_token: "YES"

cognition:
  enabled: true
  max_subquestions: 3
  max_act_rounds: 2                       # 2 = REFINE allowed; 1 = no refine
  refine_enabled: true
  dispatch_answer_via_skill: false        # route PLAN ANSWER through DirectAnswerSkill
  web_search_skill: "research"           # v3: name of the Skill used for web triage + SEARCH verb + REFINE gap closure. A third-party package can ship a replacement (e.g. "perplexity_research") and the operator points this knob at it — no code change.

skills:
  default_cost_tier_cap: "expensive"      # free | cheap | expensive
  auto_offline_filter: true               # drop net-requiring skills when brain offline
  enabled: {}                             # per-skill override map, e.g. {research: false}

web_chat:
  enabled: true
  host: "127.0.0.1"
  port: 8765
  auth_token: ""
  respect_sleep_metabolism: true

tasks:
  enabled: true
  state_path: "state/tasks.yaml"
  max_active_tasks: 16
  results_per_query: 5
  min_interval_seconds: 300
  tick_seconds: 30
```

---

## 運維指令 (Operational Commands)

下表的 slash 指令兩個 connector 都認得,除非明確標記為某 channel 專屬。當 slash 是 Telegram 限定時,web-chat channel 在 `POST /chat` 暴露一個等效的 JSON 欄位(完整 request schema 見 [`connectors/web_chat.py`](connectors/web_chat.py) 的 `web_chat` 區塊)。

| Slash | 自然語言等效 | 動作 |
|---|---|---|
| `/start` | (僅 Telegram) | 問候 + 揭露 designation |
| `/tasks` | 「show my tasks」、「what's scheduled」 | 列出現役循環任務 |
| `/cancel <id>` | 「cancel task abc123」、「stop the bitcoin task」 | 移除一個任務 |
| `/pause <id>` | 「pause task abc123」 | 暫停(不移除) |
| `/resume <id>` | 「resume task abc123」 | 恢復一個暫停的任務 |
| `/research [topic]` | (僅 Telegram)——「research <topic>」 | 觸發一次隨選 proactive 週期 |
| `/emergency <msg>` | (僅 Telegram)—— web-chat 在 JSON body 用 `"emergency": true` | 繞過 Sleep Metabolism gating |

加上正常的自然語言訊息 —— 它們會自動路到:

- 看起來像排程請求時 → 循環任務 pipeline
- 看起來像調整請求時 → task-modify pipeline
- 看起來像狀態變更(cancel/pause/resume)時 → task-action pipeline
- 其他情況 → 普通的 `Brain.think()` pipeline

---

## 開發筆記 (Development Notes)

### 在 macOS / Linux dev 機上跑

- 在 `config.yaml` 設 `hardware.npu_acceleration: false`。
- 完全跳過 Hailo-Ollama;直接跑原生 Ollama 配 `qwen2:1.5b`。
- `temperature_c` 會是 `None`(沒有熱感測器)—— 沒關係。
- 用 `OCF_FORCE_STRESS=1 python main.py` 演練 EXHAUSTION DIRECTIVE 路徑。

### Log 與 state 不是垃圾

`logs/daily/`、`state/*.jsonl`、`state/vitals.json`、`state/tasks.yaml` 和 `memory/archive.md` 全部都是 agent runtime memory 與可觀測性的一部分。預設的 [.gitignore](.gitignore) 已經把它們排除掉,讓每次 clone 都從乾淨開始,但它們在現役部署上會撐過重啟,並被 dashboard 讀取。它們**會**被 Sleep Metabolism 輪替 / 截斷(`stm.purge()` 每晚清掉 STM journal;`archive.md` 會單調成長,直到你手動 compact 它)。

### 原子寫入無處不在

每一個 state-file writer 都用 `tmp + os.replace` pattern 保證 dashboard 永遠不會讀到半寫的檔案。如果你加一個新的 state 檔,跟同樣的 pattern(見 `main.py` 裡的 `_publish_tools_inventory()`)。

### Pylance-clean codebase

整個 codebase 都有 type annotation,且 Pylance-clean(strict-ish)。Frozen dataclass 被大量用在跨子系統邊界的 snapshot 上(`VitalSigns`、`EmotionVector`、`EmpathyReading`、`SoulSnapshot`、`ProviderHealth`、`ThoughtTrace`、`CognitiveTrace`、`Turn`、`TaskSpec`)。

### 加一個新的 connector

要加(例如)Discord、Slack 或 MCP-server connector:

1. 把 `connectors/web_chat.py` 當 template 複製。
2. 實作一個入站訊息 handler,它要:
   - 在 fallback 到 `Brain.think()` 之前,先把訊息送過 task pre-filter chain(`looks_like_task_query` → `looks_like_task_modify_request` → `looks_like_task_request` → `Brain.parse_task_intent`)。
   - 標記 `heartbeat.mark_interaction()` 重置 idle 時鐘。
   - 對非緊急訊息尊重 `heartbeat.is_sleeping`。
3. 實作一個出站的 `deliver(text)` callback,在 `attach_scheduler()` 裡呼叫 `scheduler.bind_deliver(<origin>, deliver_fn)`。
4. 在 `main.py` 的生命週期(start / stop)裡和 `telegram` 與 `web_chat` 一起接上。

就這樣 —— 新的 connector 免費繼承主動思考、任務投遞、vitals 可見性、與 SLM 離線時的優雅降級。

### 加一個新的 tool

實作 [tools/base.py](tools/base.py) 的 `Tool` protocol(`name`、`description`、`args_schema`、async `call(**kwargs) -> ToolResult`、async `aclose()`),在 `main.py` 用 `tool_registry.register(my_tool)` 註冊,然後重新發佈 inventory snapshot。Tool 是機械式 I/O —— 它們自己**不會**出現在 PLAN menu 上;把它們包進一個 Skill(下一節)讓 SLM 能挑到它。

### 加一個新的 skill

Skill 是 SLM 在 PLAN 時實際會挑的東西。實作 [core/skills/base.py](core/skills/base.py) 的 `Skill` protocol:

1. 在 [core/skills/](core/skills/) 下加一個新檔,例如 `home_control.py`。把 [core/skills/recall.py](core/skills/recall.py) 當作標準的小範例。
2. 設定合約欄位:`name`(snake_case 動詞)、`description`(≤60 tokens —— 會落在 PLAN prompt 裡)、`trigger_hints`、`args_schema`、`cost_tier`、`requires_network`、`side_effects`、`requires_confirmation`。**只有**在 SLM 應該能在 PLAN 期挑它時才設 `plan_verb` —— 背景 skill(`self_reflect`、`proactive_learning`、`recurring_research`)留 `None`。
3. 實作 `async execute(ctx, **kwargs) -> SkillResult`。**永遠不要 raise** —— 把失敗包在 `SkillResult(ok=False, error=...)`。用 `ctx.tools.get("web_search")`、`ctx.soul.read()` 等等 —— 不要伸手抓 global state。
4. 在 `main.py` 既有的 `_maybe_register(...)` 行旁邊註冊 instance。Registry 的 change listener 會自動重新發佈 `state/skills.json`。
5. (可選)在 `config.yaml` 用 `cfg.skills.enabled["home_control"] = false` gate 住它,把它放在 flag 後面出貨。

就這樣 —— PLAN menu 下一回合就會拾起它、ACT 按名稱派發、audit feed 拿到每次呼叫的 row、Sleep Metabolism 在它開始失敗時會 flag 它。PR 慣例見 [CONTRIBUTING.md](CONTRIBUTING.md)。

### 加一個新的 sensor(GPIO / I²C / etc.)

`Monitor.sample()` → `VitalSigns` dataclass 是正確的擴充表面。加帶 `None` default 的新可選欄位(保留任何 positional caller 的向後相容),透過 `vitals.describe()` 把它們接到 SLM 在 prompt 裡看得到,以及(可選)接到 `Emotions.MoodTuning` 讓它們能驅動 mood transition。Dashboard 的 chip strip 是單一 Streamlit 行 —— 加欄位很簡單。

---

## 路線圖 (Roadmap)

目前的 codebase 是 **v3.0**:本 README 的每個子系統都已實作、由 `tests/` 底下的 unit-test 套件覆蓋(今天 239 個測試)、被 `scripts/smoke_*.py` runner 端到端演練、且 Pylance-clean。

**框架表面**(plug-in 作者對著什麼開發):

- **四個對稱的 plug-in 表面** —— Skills、Tools、Connectors、Provider Backends —— 每一個都有自己的 frozen `*Manifest`、自己附帶生命週期 + 稽核 + 可選 `bind_context` 的 `*Registry`、以及自己 fail-isolated 的 `*/discovery.py` 走對應的 `opencrayfish.*` entry-point group。加任何 plug-in 就是 `pip install` + (可選)`cfg.plugins.<key>:` 區塊 + 重啟,對 OpenCrayFish 本體零修改。
- **Hybrid discovery**([core/dropin.py](core/dropin.py))—— 每個表面在 boot 時也會走一個 `plugins/<surface>/` drop-in 資料夾。丟一個 `.py` 檔暴露 `PLUGIN = MyClass`(或 `PLUGINS = [...]`)屬性,它就能跟 pip-installed 套件一樣抵達相同的 registry —— 無需 `pyproject.toml`、無需 entry-points、無需 `pip install`。適合本地實驗與離網部署。預設 root `<cwd>/plugins/`,可用 `OPENCRAYFISH_PLUGINS_DIR` 覆寫。
- **`ToolContext` + `bind_context`**([tools/base.py](tools/base.py)、[tools/registry.py](tools/registry.py))—— Tools 有一個與 Skills 對稱的可選 config-injection 接縫。第三方 Tool 從一個 frozen、共享的 context 物件讀 `cfg.plugins.<key>`,不需要 factory-closure 舞步。first-party Tools(`SearXNG`、`ArchiveRead`)不受影響 —— `bind_context` 純粹是累積式的。
- **Operator-routable Provider backends**([core/provider.py](core/provider.py))—— `cfg.hardware.primary_backend` 與 `cfg.hardware.fallback_backend` 接受任何已發現的 backend manifest 名稱,`Provider.from_config()` factory 會換對應的槽位。未知名稱 → boot 時大聲失敗,附可用名稱列表。
- **Operator-configurable web-search Skill** —— `cfg.cognition.web_search_skill`(預設 `"research"`)決定 Brain 的 web triage + Cognitive Loop 的 `SEARCH` 動詞 + REFINE gap closure 要派發給哪個 Skill。第三方的 `perplexity_research`(或類似)替代品可以無需動到核心就 drop in。
- **Tested protocol surface** —— `tests/test_skill_protocol_surface_v1.py` + 四個 `SUPPORTED_*_PROTOCOL_VERSIONS` 常數是 snapshot-frozen 的。對已發佈的 `*Manifest` / `*Context` / `*Result` 做任何 breaking 編輯,要不就還原表面、要不就在 [§ Protocol Surface 穩定性](#protocol-surface-穩定性)附上 migration note 並刻意 bump protocol 版本。

自然的下一步:

- **更多內建 Skills** —— `home_control`(Home Assistant)、`calendar`(CalDAV)、`local_rag`(對使用者文件做 FAISS)、`mcp_bridge`(把任何 MCP server 變成一個 Skill)。
- **GPIO / I²C 感測器函式庫** —— 溫濕度(BME680)、動作(PIR)、光線(TSL2591)、氣體(CCS811/MQ-2)、心率(MAX30102)、動作(MPU6050)直接接到 `VitalSigns` 與 `MoodTuning`。
- **本地視覺** —— 跑在 NPU 上的小型 VLM + CSI 相機,以 `see` Skill 暴露。
- **本地語音** —— Whisper.cpp 入、Piper TTS 出,註冊為雙向 connector。
- **輸出 actuator** —— WS2812 LED strip 做 mood 視覺化、OLED 做表情、伺服馬達做具身運動 —— 這些都是 `side_effects=True, requires_confirmation=False` 帶策略的 Skill。
- **睡眠期 soul 演化** —— SLM critique pass 建議(但不能套用)Soul mutation 給建構者批准,取代目前以規則為主的 `_consolidate_reflections`。
- **靜態加密狀態** —— 給受監管環境(臨床、法務、金融)的部署。
- **Pytest 套件擴張** —— 值得補的覆蓋缺口:`Emotions` decay/nudge 數學、`CognitiveLoop` THINK/PLAN/REFINE parsing edge case、`SkillRegistry.plan_menu` 在 vitals 壓力 + brain 離線下的過濾、`Provider` circuit-breaker trip / half-open / recover 生命週期。見 [`good-first-issue`](https://github.com/easonlai/opencrayfish/labels/good-first-issue)。

---

## 社群與貢獻 (Community & Contributing)

OpenCrayFish 是 **[MIT License](LICENSE) 下的 open source**,歡迎每一個有熱情的開發者、架構師、硬體 hacker、AI tinkerer 共同開發。小龍蝦很小,但池塘很深 —— 空間還很大。

> 💡 **我們特別希望你來,如果你……** 曾經想把一個感測器、一顆智慧燈泡、一個 CalDAV 行事曆、一個自家做的 RAG、或一台 MCP server 接進一個*活的*邊緣 agent 並看著它隔夜從結果中學習。Skill plug-in 層(見 [§ 12](#12-skills-與-tools--兩階層能力堆疊) 與 [Hello-World Skill 教學](#hello-world-skill--一步一步))就是被打造成「一個可用的貢獻就是一個檔案加上 `main.py` 一行」。每一次有人接上一個新反射、新感官、新觸及世界的方式,小龍蝦就強壯一點。

### 從哪裡開始

| 如果你想要…… | 去這裡 |
|---|---|
| **提問或分享部署** | [GitHub Discussions](https://github.com/easonlai/opencrayfish/discussions) |
| **回報 bug** | [Bug Report template](https://github.com/easonlai/opencrayfish/issues/new?template=bug_report.yml) |
| **提案 feature** | [Feature Request template](https://github.com/easonlai/opencrayfish/issues/new?template=feature_request.yml)(較大的點子請先開 Discussion) |
| **送 code** | 讀 [CONTRIBUTING.md](CONTRIBUTING.md),用 [PR template](.github/PULL_REQUEST_TEMPLATE.md) 開 PR |
| **回報安全漏洞** | [Private security advisory](https://github.com/easonlai/opencrayfish/security/advisories/new) —— 見 [SECURITY.md](SECURITY.md)。**不要開 public Issue。** |

### 適合新手的貢獻

找 [`good-first-issue`](https://github.com/easonlai/opencrayfish/labels/good-first-issue) 標籤。特別歡迎幫忙的具體領域:

- **Pytest 套件擴張** —— `tests/` 目錄今天出貨 239 個測試(intent router、JSONL schema、STM rotation、positive filter、prompt assembly、skill context、tool context、provider backend routing、drop-in discovery、task parsing)。仍開著的高影響缺口:`Emotions` decay 數學、`CognitiveLoop` THINK/PLAN/REFINE parsing、stress + offline 下的 `SkillRegistry.plan_menu`、以及 `Provider` circuit-breaker 狀態轉換。
- **硬體 port 報告** —— 在 Pi 4、Orange Pi、Jetson Nano 或 x86 mini-PC 上試 OpenCrayFish,並開一個帶 `hardware-port` 標籤的 Issue,附上你的 `state/vitals.json` 與顯著的延遲數字。
- **新 `Tool` plugin** —— 為本地檔案操作、GPIO 控制、感測器讀取、MCP server 等等實作 [tools/base.py](tools/base.py) 的 `Tool` protocol。
- **新 `Connector`** —— Discord、Slack、Matrix、IRC、MCP server。把 [connectors/web_chat.py](connectors/web_chat.py) 當 template。
- **Persona / soul.md preset** —— 分享有趣的個性(secret 拿掉),讓別人能 fork 它們。
- **EmpathyEngine 字典翻譯** —— 目前是英文 + 中文;歡迎日文、韓文、西班牙文、法文、德文、印地文、阿拉伯文。
- **文件** —— 工作過的範例、部署指南(systemd unit、local stack 的 Docker compose)、dashboard 的螢幕錄影。

### 專案慣例(短版)

開 PR 前,請讀 [CONTRIBUTING.md § Project Conventions](CONTRIBUTING.md#project-conventions-read-before-coding)。不可妥協的規則:

1. **Pylance-clean、無處不 type-annotated。**
2. **跨子系統 snapshot 用 frozen dataclass。**
3. **dashboard 會讀的每一個 state 檔都用原子寫入(`tmp + os.replace`)。**
4. **只有 asyncio、細粒度鎖。沒有 threads、沒有 `multiprocessing`。**
5. **失敗會退化 —— 它們不會 raise 進 heartbeat loop。**
6. **Pi 5 延遲預算是真的 —— NPU 上每個 engaged user turn 2–4 秒 wall-clock。**
7. **Edge / Lite / Offline 是強制的 —— 沒有對外的第三方網路呼叫、沒有「就直接打 OpenAI 吧」。**
8. **不要程式化修改 `soul.md` 的 IMMUTABLE_CORE。**
9. **不要 commit secret** —— `config.yaml` 已 gitignore;用 [config_sample.yaml](config_sample.yaml) 當 template。

### Code of Conduct

本專案遵循 [Contributor Covenant 2.1](CODE_OF_CONDUCT.md)。對彼此都好一點。敵意、騷擾、與居高臨下都會讓你被請出去。

---

## 授權與致謝 (License & Attribution)

- **License**:[MIT](LICENSE) —— 自由使用、fork、修改、再散佈;只要保留版權聲明。
- **作者**:Eason Lai(Hong Kong)—— OpenCrayFish 專案的原作者。
- **代號**:OpenCrayFish(小龍蝦)
- **靈感來源**:生物神經系統、Minions、每一個不肯等雲端的 edge-AI hacker。
- **貢獻者**:見 [contributors graph](https://github.com/easonlai/opencrayfish/graphs/contributors)。每一個 PR、bug 回報、Discussion 都讓小龍蝦變得更強。

> *OpenCrayFish lives where the cloud can't go.*

🦐
