# PDX Runtime

面向 AI Agent 的 Paradox 游戏原生接口。

当前支持 Victoria 3（v1.13.11 x64）。

框架以高层游戏 API 封装了国家、经济、工业建造、政治科研、军事外交、事件仲裁与时间步进；同时完整开放底层原生调用与内存原语（`game.raw` / `NativeBridge` / `RemoteProcess`）作为扩展出口。

---

## 快速起步

### 1. 从当前源码构建并安装

使用 Windows x64、64 位 Python 3.11+、CMake 和 Visual Studio C++ 构建工具。
仓库不依赖已发布的安装包。在仓库根目录构建唯一原生 DLL，然后安装当前源码：

```powershell
cmake -S . -B build/native -G "Visual Studio 17 2022" -A x64
cmake --build build/native --config Release --target pdx_runtime --parallel
python -m pip install .
python -c "import pdx_runtime; print(pdx_runtime.__file__)"
```

包内包含 `pdx_runtime.dll` 和两级 Agent Skills，无第三方 Python 运行时依赖。
更多构建与测试命令见 [DEVELOPMENT.md](DEVELOPMENT.md)。更新已加载的原生 DLL
需要新游戏进程；安装 Python 包不会自动热替换当前战役中的 DLL。

`pdx-runtime` 是需要 `--pid` 的底层 Python 会话，不是自动接入 V3 的游戏 CLI。
普通 Agent 操作使用下面的 `V3Game` 入口。

### 2. 连接现有游戏、观察、选择并操作

先正常启动 Victoria 3 并进入要操作的战局。把以下代码保存为 `agent_entry.py`，
在安装包的同一 Python 环境运行。它不需要仓库内的实验脚本或手动原生安装步骤：

```python
import asyncio
import sys
from pprint import pprint
from pdx_runtime.v3_game import V3Game, PendingOperation

async def settle(value):
    while isinstance(value, PendingOperation):
        value = await value.resume()  # 继续原操作，不重新提交
    return value

async def main(tax_band=None):
    game = await settle(await V3Game.attach())  # 多进程时显式传 pid=实际PID
    async with game:
        pprint(await settle(await game.country.summary()))
        pprint(await settle(await game.economy.tax_options()))
        if tax_band is not None:  # 由调用者从真实选项中选择
            pprint(await settle(await game.economy.set_tax_level(tax_band)))
            pprint(await settle(await game.economy.tax_level()))

asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
```

```powershell
python .\agent_entry.py       # 只连接并观察
python .\agent_entry.py low   # 明确选择 low 税档，提交一次并读取当前档位
```

结果保留原始 `None`、数值、原因与拒绝，不要求先把未知值格式化成数字。
完整领域调用见 [gameplay.md](skills/victoria3/references/gameplay.md)。
`attach()` 不启动或重载战局；零个或多个匹配进程会给出真实错误/候选。
`async with game` 结束时只释放 SDK 自有资源，游戏继续运行。退出游戏必须显式调用
`await game.quit()`；创建新战局则显式调用 `await game.new_campaign(country="CHI")`。

---

## 架构分层

```text
┌─────────────────────────────────────────────────────────────┐
│              上层应用 / AI Agent (自主决策 / 参谋辅助)          │
└──────────────────────────────┬──────────────────────────────┘
                               │
                ┌──────────────┴──────────────┐
                ▼                             ▼ (扩展出口)
┌──────────────────────────────┐ ┌─────────────────────────────┐
│  高层游戏 API (V3Game)        │ │ 底层公开原语 (game.raw)       │
│  - game.country              │ │ - NativeBridge (IPC 管道)    │
│  - game.economy              │ │ - RemoteProcess (内存与线程)  │
│  - game.construction         │ │ - v3_commands / v3_data     │
│  - game.politics / research  │ │ - v3_script (CFixedPoint)   │
│  - game.military / diplomacy │ │ - Win64 ABI 8 参数原生调用   │
│  - game.events / time        │ │                             │
└──────────────┬───────────────┘ └──────────────┬──────────────┘
               │                                │
               └───────────────┬────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│          单一聚合原生动态库: pdx_runtime.dll (Native API)      │
│               Windows x64 进程挂载与通用执行域切入             │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                 Victoria 3 (1.13.11 x64 游戏引擎)            │
└─────────────────────────────────────────────────────────────┘
```

1. **高层游戏 API（面向玩家与 Agent，默认推荐）**：以游戏自然语言组织，自动处理元数据分页、对象生命周期与内存回收；
2. **底层公开原语（扩展出口）**：`game.raw` 保持完全公开。原生内存读写、虚表调用、原始命令构造与定点数脚本求值均可直接调度；
3. **单体原生库（`pdx_runtime.dll`）**：分发包只包含这一个原生 DLL，导出接口由本次正规构建生成。

---

## 游戏领域高层 API 概览

| 模块 | 入口与核心方法 | 说明 |
|---|---|---|
| **入口与生命周期** | `V3Game.attach()`, `V3Game.launch()`<br>`game.new_campaign()`, `game.save()`, `game.saves()`, `game.load()`, `game.menu()`, `game.quit()`, `game.detach()` | 游戏自动发现接入、本地进程拉起、开局选国、存档列表与持久化、受控退出与分离 |
| **国家与宏观** | `game.country.summary()`, `game.country.states()`, `game.country.buildings()` | 国家基础信息（名称、人口、GDP）、所属省份列表与建筑信息查询 |
| **经济与财政** | `game.economy.market()`, `game.economy.goods()`, `game.economy.budget()`<br>`game.economy.set_tariff()`, `game.economy.set_consumption_tax()`, `game.economy.set_tax_level()`, `game.economy.set_wages()` | 市场商品行情（实时市价、买单需求、卖单供给、进出口量）、进出口关税策略、商品消费税、国家税档与薪资调节 |
| **工业与建造** | `game.construction.queue()`, `game.construction.capacity()`, `game.construction.build()`, `game.construction.cancel()`, `game.construction.estimate()` | 实时建造队列、建造力产能、下达建筑建造订单、撤销订单与建设预算评估 |
| **科技与研究** | `game.research.current()`, `game.research.options()`, `game.research.queue()`<br>`game.research.start()`, `game.research.enqueue()`, `game.research.dequeue()` | 当前在研科技唯一键名与序号识别、候选科技库查询、直接启动研究、队列入队与移除 |
| **政治与法律** | `game.politics.laws()`, `game.politics.law_options()`, `game.politics.enactment()`<br>`game.politics.enact()`, `game.politics.stop_enactment()`, `game.politics.institutions()`, `game.politics.set_institution()`<br>`game.politics.interest_groups()`, `game.politics.political_movements()`, `game.politics.bolster()`, `game.politics.suppress()`<br>`game.politics.decree_options()`, `game.politics.set_decree()`, `game.politics.decrees()`, `game.politics.remove_decree()` | 法律与修正案查询、法案推进与撤回、机构投资等级设定、利益集团与政治运动声援/打压、省份法令颁布与撤回 |
| **军事与武装** | `game.military.wars()`, `game.military.formations()`<br>`game.military.war(id)`, `game.military.formation(id)`, `game.military.commander(id)` | 从国家目录选择战争、编队或指挥官，沿战争—战线—战斗与编队—编制—动员—后勤关系按需读取，并在选定对象上操作 |
| **外交与条约** | `game.diplomacy.countries()`, `game.diplomacy.plays()`, `game.diplomacy.proposals()`<br>`game.diplomacy.terms()`, `game.diplomacy.accept()`, `game.diplomacy.decline()`<br>`game.diplomacy.relations()`, `game.diplomacy.actions()`, `game.diplomacy.options()`, `game.diplomacy.diplomatic_action()`, `game.diplomacy.improve_relations()`, `game.diplomacy.stop_improving_relations()` | 外交关系网络、正在进行的外交博弈、双边条约草案与条款富文本读取、签署接受与拒绝、外交行动派发与关系改善 |
| **事件与决策** | `game.events.pending()`, `game.events.choice()`, `game.events.choose()` | 待决事件弹窗捕获、事件大标题/正文/背景故事/选项说明富文本提取、原生选项仲裁选择 |
| **模拟与时钟** | `game.time.observe()`, `game.time.pause()`, `game.time.resume(speed=3)`<br>`game.time.interrupt()`, `game.time.run_for()`, `game.time.run_until()` | 游戏时钟状态读取、原生暂停与恢复运行、按时长或目标日期连续推步推进 |

---

## 配套 Agent Skills（符合 AgentSkills.io 规范）

项目在新仓库中提供两级解耦的 Agent Skills，位于 `skills/` 目录：
- **`skills/pdx/`**：跨游戏共享底座 Skill（进程连接、底层内存读写、`CFixedPoint` 64 位定点数脚本求值、跨生命周期会话自愈）；
- **`skills/victoria3/`**：Victoria 3 专有业务指挥 Skill（高层 `V3Game` 调度、各业务领域指令与事件仲裁）。

离线语法与结构校验命令：
```powershell
python tools/validate_agent_skills.py --sdk-src src/pdx_runtime
```

## 许可证

本项目采用 [Mozilla Public License 2.0 (MPL-2.0)](LICENSE) 许可证。
