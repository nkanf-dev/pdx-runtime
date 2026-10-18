# PDX Runtime 开发与构建

## 目录与依赖方向

```text
src/native/              单体 DLL、通用执行机制与显式游戏适配
src/pdx_runtime/         Python 传输、进程资源与底层 SDK
  v3_game/              V3Game 和游戏领域
    _core.py            会话与领域入口
    _run.py             原操作、查询与资源所有权
    _read.py             原生字段观测
    _projection.py       原生批量投影
    country/ economy/    国家和经济
    research/ events/    科研和事件
    military/            战争、编队、指挥官与舰船
    diplomacy/ politics/ 外交和政治
skills/pdx/              通用运行时 Skill
skills/victoria3/        V3 API 使用说明
 tests/                  core、native_sdk、v3_game、typing、fixtures
 tools/                  构建、检查和自有测试宿主入口
```

游戏适配与显式组合入口依赖通用运行时，通用机制不能反向依赖 V3 业务。
领域实现放在所属领域；共享的执行、取值和文本机制直接放在 V3 层。
不要建立过渡服务目录、重复示例副本或旧路径兼容壳。
高层接口以游戏 UI 的真实数据绑定、对象关系和操作参数为依据。
玩法策略由上层 Agent 或应用决定，不放进底层 SDK。

## 构建与安装

需要 Windows x64、Python 3.11+、CMake 3.25+、Visual Studio C++ 工具及 Windows SDK。

```powershell
cmake -S . -B build/native -G "Visual Studio 17 2022" -A x64
cmake --build build/native --config Release --target pdx_runtime --parallel
python -m pip install .
```

原生输出是 `build/native/Release/pdx_runtime.dll`。安装时由 PEP 517 后端把它与当前
Python 源码、Skills、完整 MPL-2.0 许可证和类型标记一起打包。打包器不会隐式编译 DLL。
不同输出位置可用 `--config-settings native-dll=路径` 指定。

需要本地 Wheel/Portable 文件时，可执行：

```powershell
python tools/release_builder.py --repo . --out dist
```

这是本地构建，不上传或创建 GitHub Release。无需先复制仓库到另一个 staging 目录。
游戏中已经加载的 DLL 不会因 Python 安装而热替换；新原生构建在新游戏进程中使用。

## 测试

```powershell
$env:PYTHONPATH = "src;."
.venv/Scripts/python.exe -m unittest discover -s tests
.venv/Scripts/python.exe tools/validate_agent_skills.py --sdk-src src/pdx_runtime
```

只改一个行为时先运行对应模块。不要用反射清单、旧返回形状或重复假矩阵代替行为测试。
保留真实 ABI、原始查询续接、部分结果和资源所有权的检查。

原生回归只启动本仓库自有测试宿主，不查找或接管游戏：

```powershell
cmake -S . -B build/owned-host -G "Visual Studio 17 2022" -A x64 -DPDX_BUILD_TEST_HOSTS=ON
cmake --build build/owned-host --config Release --target pdx_regression_hosts --parallel
.venv/Scripts/python.exe tools/run_owned_host_regression.py --bin-dir build/owned-host/Release --out build/owned-host/evidence.json --timeout 60
```

短 host 接入测试覆盖空闲客户端并存、多个独立进程与并发连接。
只有实际原生请求分派串行化；空闲管道读写不能占用执行权。

## 提交

提交消息使用 Conventional Commits：`type(scope): description`。
本地与 CI 使用同一个 `tools/check_commit_messages.py`。
启用方式见 [CONTRIBUTING.md](CONTRIBUTING.md#commit-messages)。
