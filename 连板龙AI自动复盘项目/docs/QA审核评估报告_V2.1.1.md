# 连板龙.1.1 — QA 审核评估报告

> **审核版本**：V2.1.1 (基于 V2.1.0 代码实际状态)  
> **审核日期**：2026-06-03  
> **审核范围**：全模块代码审查、与产品说明书 V2 对齐度、交付后稳定性与可靠性  
> **上期报告**：QA审核评估报告_V2.1.0.md（2026-06-03）  
> **合规参考**：合规可行性评估报告.md  
> **再次声明**：本报告基于静态代码审查和文档对比，未进行运行时测试。

---

## 一、总体结论

| 维度 | 评分 | 说明 |
|:-----|:---:|:-----|
| 代码-文档对齐度 | 🟡 中 | V2.1.0 致命 Bug **未修复**，版本号仍混乱 |
| 交付后稳定性 | 🔴 差 | 概念分析模块调用参数不匹配，运行时 **TypeError 崩溃** |
| 数据可靠性 | 🟢 高 | 双源降级 + 6 项自动校验，设计合理 |
| 安装部署可靠性 | 🟢 高 | 注册表查找 + 路径探测 + 原子写入 |
| 长期可维护性 | 🟡 中 | 版本号混乱、无单元测试、无自动更新机制 |

**综合判定：🔴 不建议交付，必须修复以下致命问题后方可交付。**

---

## 二、致命问题（必须修复，否则运行时崩溃）

### 🔴 2.1 概念分析模块调用参数不匹配（V2.1.0 未修复）

**位置**：`engine.py` 第 579 行 → `concept_analyzer.py` 第 193 行

**调用代码**（`engine.py` 第 579 行）：
```python
concept_result = analyze_concepts(limit_up_codes, callback=log, up_stocks=up_stocks)
```

**被调用函数签名**（`concept_analyzer.py` 第 193 行）：
```python
def analyze_concepts(limit_up_codes, callback=None):
```

**问题**：
1. `engine.py` 调用 `analyze_concepts()` 时传入了 `up_stocks=up_stocks` 关键字参数，但 `concept_analyzer.py` 中的函数定义 **不接受** `up_stocks` 参数
2. 运行时将抛出 `TypeError: analyze_concepts() got an unexpected keyword argument 'up_stocks'`，**导致整个连板龙引擎崩溃**
3. 产品说明书 V2 第 5 行明确写"概念分析改用 reason_type 分组"，但 `engine.py` 调用的仍是旧版 `analyze_concepts()`（push2 API，已不可用），而非新版的 `analyze_concepts_by_reason()`
4. `concept_analyzer.py` 中已实现 `analyze_concepts_by_reason(up_stocks, callback=None)`（第 30 行），但 **从未被 `engine.py` 调用**

**影响**：
- 概念热点板块（RDGN/HYRD/RDGN3）输出为空
- `extern_user.txt` 中的"所属概念"列为空
- 用户看到的概念分析功能完全不可用
- 综合评分排名（V2.1 核心功能）未执行

**修复方案**（择一）：

**方案 A（推荐，最小改动）**：
```python
# engine.py 第 579 行改为：
from concept_analyzer import analyze_concepts_by_reason
concept_result = analyze_concepts_by_reason(up_stocks, callback=log)
```

**方案 B（接口改造，更通用）**：
```python
# concept_analyzer.py 修改 analyze_concepts 函数，增加 up_stocks 参数：
def analyze_concepts(limit_up_codes, callback=None, up_stocks=None):
    if up_stocks:
        return analyze_concepts_by_reason(up_stocks, callback=callback)
    # fallback: push2 API
    ...
```

**风险评估**：🔴 致命 — 运行时必然崩溃，概念分析 100% 不可用  
**引入版本**：V2.1.0（未修复）  
**修复优先级**：P0

---

## 三、严重问题（影响稳定性，建议交付前修复）

### 🟠 3.1 版本号严重不一致（V2.1.0 未修复）

| 位置 | 显示的版本号 | 内容 |
|:-----|:------------|:-----|
| `version.json` | `2.1.0` | "V2.1变更" |
| `产品说明书 V2` | `V2.1.0` | 正文标题 |
| `app.py` 第 3 行 | `V5` | 文档字符串 "连板龙 V5 — 通达信自动复盘桌面应用" |
| `app.py` 第 41 行 | `V5` | 窗口标题 `self.root.title('连板龙 V5')` |
| `engine.py` 第 3 行 | `V2.1` | 文档字符串 "连板龙引擎 V2.1" |
| `tg_bot.py` 第 3 行 | `V5` | 文档字符串 "连板龙 V5 Telegram Bot" |
| `build.py` 第 3 行 | `V5` | 文档字符串 "连板龙 V5 PyInstaller 打包脚本" |
| `report.py` 第 3 行 | `V5` | 文档字符串 "每日复盘报告 V5" |
| `install.py` 第 3 行 | `V2` | 文档字符串 "连板龙 安装器" |

**问题**：用户界面显示 `V5`、引擎内部用 `V2.1`、`version.json` 记录 `2.1.0`、安装器写 `V2`。当用户反馈问题时，无法准确判断其运行版本。

**修复建议**：统一为 `V2.1.1`，从 `version.json` 动态读取到各模块。在 `app.py` 中将窗口标题和文档字符串同步。

---

### 🟠 3.2 TDX_DIR 全局变量非线程安全

**位置**：`utils.py` 第 332-342 行

```python
TDX_DIR = None  # 模块级全局变量

def set_tdx_dir(tdx_path):
    global TDX_DIR
    TDX_DIR = tdx_path
```

**问题**：`engine.py` 在 `run_engine()` 中调用 `set_tdx_dir(tdx_dir)` 设置全局变量，然后 `calc_board_days_tdx()` → `read_tdx_daily()` 读取该变量。如果两个线程同时调用 `run_engine()`（理论上 GUI 的"立即更新"按钮可能被重复点击），`TDX_DIR` 可能被覆写为错误值。

**当前影响**：`app.py` 有 `self.running` 互斥锁防止重复点击，单线程模式下问题不大。但命令行模式 (`--update`) 没有此保护。

**修复建议**：将 `TDX_DIR` 改为线程局部存储（`threading.local()`），或作为参数直接传递给 `read_tdx_daily()` 和 `calc_board_days_tdx()`。

---

### 🟠 3.3 config.json 中 tgBotToken 明文存储

**位置**：`app.py` 第 260-262 行（设置面板中等号框用 `show='*'` 掩码但保存时明文写入 config.json）

**问题**：TG Bot Token 以明文 JSON 存储在本地文件。如果用户共享 config.json 或上传到网盘，Token 泄露后攻击者可通过 Bot 向用户发送恶意消息。

**修复建议**：用 `keyring` 库存储敏感凭证，或至少做简单的 Base64 编码（虽非真正加密，但可防肉眼泄密）。

---

## 四、中等问题（影响用户体感，建议下版本修复）

### 🔸 4.1 install.py 中定时任务的 PowerShell 脚本有编码风险

**位置**：`install.py` 第 104-108 行

```python
ps_script = f"""
$action = New-ScheduledTaskAction -Execute "{python_exe}" -Argument "\\"{app_py}\\" --update"
...
"""
ps_file = os.path.join(os.environ["TEMP"], "fupan_create_task.ps1")
with open(ps_file, "w", encoding="utf-8") as f:
    f.write(ps_script)
```

**问题**：
1. `python_exe` 和 `app_py` 路径中可能包含空格和特殊字符，需要额外转义
2. 如果 `python_exe` 路径中含中文字符（如 `C:\用户\...`），PowerShell 可能解析失败
3. 创建定时任务需要管理员权限，部分用户可能遇到权限错误

**建议**：在创建任务前检测管理员权限，失败时给出手动创建指引。

---

### 🔸 4.2 TG Bot 广播无速率限制

**位置**：`engine.py` 第 365-395 行（`_push_tg()`）

**问题**：`_push_tg()` 函数遍历所有用户并逐一发送消息，没有调用间延迟或失败重试。如果用户数量较大且 Telegram API 限流（通常 30 条/秒），后续消息会失败。

**建议**：加入 `time.sleep(0.05)` 调用间延迟，并为失败的消息记录到日志。

---

### 🔸 4.3 节假日文件缺失无默认兜底

**位置**：`utils.py` 第 448-456 行

```python
def _load_holidays():
    fp = STANDALONE_DIR / 'holidays.json'
    data = load_json(fp)
    _holidays_cache = set(data) if data else set()
    return _holidays_cache
```

**问题**：如果 `standalone/holidays.json` 缺失且非周末非假日运行，`is_trading_day()` 会误判交易日。在长假期（如春节）期间，定时任务可能在非交易日运行，触发 API 请求获取空数据。

**建议**：内置 2026 年关键假日兜底列表，或从在线日历 API 获取。

---

## 五、低优先度问题（改善项，不阻塞交付）

### 💡 5.1 日志文件没有大小限制

**位置**：`utils.py` 第 122-131 行（`Logger.emit()`）

**问题**：日志每秒追加到 `logs/YYYYMMDD.log`，每天一个文件。`clean_old_logs(days=30)` 只按时间清理。如果一个交易日 API 故障反复重试，单日日志文件可能增长到几十 MB。

**建议**：增加单文件大小限制（如 10MB 后轮转）。

---

### 💡 5.2 自定义数据列（extern_user.txt）需用户手动配置

**位置**：产品说明书 2.3 章节

产品说明书写"用户需手动配置：功能→公式系统→自定义数据管理器→新建数据号1/2"。这是整个产品中**唯一需要用户手动操作的步骤**，对不熟悉通达信菜单的用户构成体验障碍。

**建议**：在安装完成提示中增加截图指引，或尝试通过修改通达信的配置文件（如 `attrib.ini`）自动创建数据号。

---

## 六、V2.1.0 审核意见修复追踪

对比 V2.1.0 报告（18 个问题），V2.1.1 的修复情况：

| # | V2.1.0 问题 | 严重度 | V2.1.1 修复状态 |
|:--|:-------------|:------|:---------------|
| 1 | 概念分析 TypeError Bug | P0 | ❌ **未修复** — 同款 Bug 仍存在 |
| 2 | 版本号混乱 | P1 | ❌ **未修复** — app.py 仍显示 V5 |
| 3 | TDX_DIR 非线程安全 | P1 | ❌ **未修复** |
| 4 | tgBotToken 明文存储 | P1 | ❌ **未修复** |
| 5 | 节假日文件缺失兜底 | P2 | ❌ **未修复** |
| 6 | 定时任务 PowerShell 编码风险 | P2 | ❌ **未修复** |
| 7 | TG Bot 广播无速率限制 | P2 | ❌ **未修复** |
| 8 | 日志文件大小无限制 | P3 | ❌ **未修复** |
| 9 | 自定义数据列需手动配置 | P3 | ⚠️ 部分解决：已加截图指引建议 |

**修复率**：9 个问题中，✅ 完全修复 0 个，⚠️ 部分修复 1 个，❌ 未修复 8 个。

---

## 七、交付稳定性评分矩阵

| 测试场景 | 通过条件 | 实际状态 | 风险 |
|:---------|:---------|:---------|:---:|
| **交易日正常运行** | 所有 15 个板块有数据 | 🔴 概念板块为空的 Bug（见 2.1） | 🔴 |
| **非交易日运行** | 识别为非交易日后退出 | ✅ `is_trading_day()` 正确判断 | 🟢 |
| **同花顺 API 不可用** | 降级到 boards 缓存 | ✅ `_stale_recovery()` 保留旧数据 | 🟢 |
| **ClawHub 不可用** | 降级到东方财富龙虎榜 | ✅ `fetch_lhb_eastmoney()` 自动切换 | 🟢 |
| **东财 push2ex 不可用** | 跌停/炸板板块为空 | ✅ 不崩溃，日志记录 | 🟢 |
| **TDX 本地日线缺失** | 降级到同花顺历史 API | ✅ `calc_board_days()` 降级逻辑 | 🟢 |
| **网络完全断开** | 保留旧数据，不覆盖 | ✅ `_stale_recovery()` 机制 | 🟢 |
| **部分数据源返回空** | 校验日志警告，继续运行 | ✅ R1-R7 校验 + 日志 | 🟢 |
| **定时任务执行** | 静默完成 | 🟡 依赖 Windows 计划任务权限 | 🟡 |
| **通达信版本更新后** | 板块文件仍可用 | 🟡 二进制格式可能变化 | 🟡 |

---

## 八、交付前的必做事项清单

| # | 事项 | 阻塞交付 | 负责人 |
|:--|:-----|:-------:|:------|
| 1 | **修复 2.1 概念分析 TypeError Bug** | 🔴 是 | 开发 |
| 2 | **统一所有模块版本号为 V2.1.1** | 🟡 建议 | 开发 |
| 3 | 修复 TDX_DIR 线程安全问题 | 🟡 建议 | 开发 |
| 4 | tgBotToken 改为安全存储 | 🟡 建议 | 开发 |
| 5 | 在 `standalone/` 中确认 `concept_map.json` 和 `holidays.json` 文件存在 | 🟢 否（有兜底） | 部署 |
| 6 | 在至少一台非开发环境 Windows 10/11 上完整安装测试 | 🟡 建议 | 测试 |
| 7 | 模拟一个完整交易日流程（获取涨停 → 连板计算 → 板块写入 → 打开通达信验证） | 🔴 是 | 测试 |
| 8 | 确认中银证券通达信版（非原生通达信）的目录结构兼容 | 🟡 建议 | 测试 |

---

## 九、与产品说明书 V2 的对齐度检查

| 说明书功能描述 | 代码实现状态 | 对齐度 |
|:---------|:---------|:-------|
| 15 个板块（含 ZFB2，移除 ZJL） | ✅ 正确（SECTORS 列表 15 个） | ✅ 100% |
| 概念分析改用 reason_type 分组 | ❌ `engine.py` 仍调用旧版 `analyze_concepts()` | ❌ 0% |
| DT(今跌停) 改为包含所有触及跌停 | ✅ `all_dting = 封跌停 + 曾跌停` | ✅ 100% |
| ST 股显式追踪 | ✅ `st_codes` 集合 + `st_zting` 列表 | ✅ 100% |
| ZFB2 增加 API 降级数据源 | ✅ `_fetch_ths_day_codes()` 降级 | ✅ 100% |
| 晋级率/溢价率/炸板率展示 | ✅ `_calc_premium_rate()` + `_calc_zhaban_rate()` | ✅ 100% |
| 看盘界面 .sp 文件动态更新 | ✅ `_update_sp()` 函数 | ✅ 100% |
| 双环节数据校验（R1-R7） | ✅ `_validate_data()` 函数 | ✅ 100% |
| 微信推送 + TG Bot 推送 | ✅ `_push_wechat()` + `_push_tg()` | ✅ 100% |
| 原子写入（.tmp → os.replace） | ✅ `save_json()` + `write_blk()` + `write_extern_user()` | ✅ 100% |

**对齐度总结**：10 项功能描述中，✅ 9 项正确实现，❌ 1 项（概念分析）实现错误导致崩溃。

---

## 十、版本信息

| 字段 | 值 |
|:-----|:---|
| **本期审核版本** | V2.1.1 |
| **上期审核版本** | V2.1.0 |
| **产品说明书** | 连板龙_产品说明书_V2.md |
| **代码目录** | Workbuddy版本V2/ |
| **审核日期** | 2026-06-03 |
| **审核结论** | 🔴 **不建议交付**（需修复 2.1 致命 Bug + 统一版本号） |

---

## 十一、附录：代码架构概览

### 11.1 模块依赖关系

```
app.py (GUI/CLI 入口)
  ├── engine.py (连板龙引擎)
  │     ├── utils.py (工具层)
  │     └── concept_analyzer.py (概念分析)
  ├── report.py (HTML 报告生成)
  ├── tg_bot.py (Telegram 推送)
  └── config.json (运行时配置)

install.py (安装入口)
  ├── engine.py (install_blocks)
  └── utils.py (set_column_green)

build.py (PyInstaller 打包)
  └── 生成：连板龙V5.exe / engine.exe / install.exe
```

### 11.2 关键数据结构

| 文件 | 格式 | 用途 |
|:-----|:-----|:-----|
| `config.json` | JSON | 运行时配置（TDX 路径、API Token） |
| `version.json` | JSON | 版本号记录 |
| `data/latest.json` | JSON | 最新复盘数据（供 GUI 展示） |
| `data/boards/YYYYMMDD.json` | JSON | 连板天数缓存（供 ST 股继承） |
| `data/history/YYYYMMDD.json` | JSON | 历史复盘快照 |
| `data/concept_history/YYYYMMDD.json` | JSON | 概念历史快照 |
| `logs/YYYYMMDD.log` | 文本 | 运行日志（30 天轮转） |
| `tg_users.json` | JSON | TG Bot 已注册用户列表 |
| `standalone/concept_map.json` | JSON | 概念名称映射表 |
| `standalone/holidays.json` | JSON | 节假日列表 |

---

> **免责声明**：本报告基于静态代码审查和文档对比，未进行运行时测试。建议在交付前进行完整的端到端集成测试。
