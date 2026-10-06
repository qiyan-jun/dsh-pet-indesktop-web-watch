# PR 报告：打包后双击弹出黑色控制台窗口（frameseq 阶段一编码）

- 日期：2026-10-06
- 分支/提交：本机 `dsh-pet-indesktop`（本次修复提交见文末）
- 触发反馈：用户原话「双击打开 exe 软件后会有黑色弹窗」

## 一、现象与根因

**现象**：双击安装目录里的 `dsh-pet-standalone-webm-chat.exe` 后，屏幕上出现一个黑色
控制台窗口，标题就是 ffmpeg 的完整路径：

```
D:\Users\七颜\AppData\Local\Programs\dsh-pet-standalone-webm-chat
  \_internal\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe
```

**根因**（实机进程链证据，见 §四）：`pet/frameseq_provision.py` 的阶段一编码
（webm → 无损中间帧）用 `_popen(_ffmpeg_argv(...), creationflags=_creation_flags())`
派生 ffmpeg，而 `_creation_flags()` 当时**只返回 `BELOW_NORMAL_PRIORITY_CLASS`**。
打包后的父进程是 GUI 子系统（PE subsystem = **2**，没有控制台），于是 Windows 给这个
ffmpeg 子进程**新分配了一个控制台**，窗口可见 → 用户看到黑窗。

**排除项**（都查过，不是它们）：

| 怀疑对象 | 证据 | 结论 |
| --- | --- | --- |
| exe 自身是 console 子系统 | 读 PE 头：当前安装 / 原始备份 / 本次构建三者的 subsystem 都是 **2（WINDOWS_GUI）** | 排除 |
| `harness_autostart` 拉起 cmd/node | 配置 `harness_autostart=false`，进程树无 cmd/node 子进程 | 排除 |
| 其他 ffmpeg spawn（播放解码） | `pet/webm_clip.py` 有专门的 STARTUPINFO/`_PopenCapture` 漏斗隐藏控制台；90 秒盯窗期间只有阶段一编码产生窗口 | 排除 |
| `click_sound.py` 的 `Popen` | 那段是 macOS/Linux 回退（`afplay`/`paplay`/`aplay`），Windows 不走 | 排除 |

> 为什么以前没暴露：只有当 frameseq 供给**真的有活干**（缓存缺失/源素材变了）时才会
> 派生阶段一编码。本次因多次重新部署安装目录导致帧序列被判定为陈旧，启动即重编码，
> 黑窗才稳定出现（安装目录内 `frameseq/random/.converting.lock` 时间戳 = 用户双击的
> 那一刻，即为现场物证）。

## 二、修改文件说明

`git diff --numstat`（提交前）：

```
16	4	pet/frameseq_provision.py
19	0	tests/test_frameseq_provision.py
```

| 文件 | 改了什么 | 为什么 |
| --- | --- | --- |
| `pet/frameseq_provision.py` | `_creation_flags()`（+12/−1）：Windows 分支由 `BELOW_NORMAL_PRIORITY_CLASS` 改为 `BELOW_NORMAL_PRIORITY_CLASS \| CREATE_NO_WINDOW`；补写该函数 docstring（记录 PE subsystem=2 的前提与实机现象）；模块 docstring 的「Windows 下 ffmpeg 子进程 creationflags=…」一句同步更新（+3/−2） | 只给优先级标志会让 Windows 为 ffmpeg 新建可见控制台；`CREATE_NO_WINDOW` 表示"以无控制台窗口的方式运行控制台程序"，正是该场景的标准修法。两个标志可并存，优先级让让渡行为不变 |
| `tests/test_frameseq_provision.py` | 新增 `test_creation_flags_hide_console_window`（+19）：Windows 上断言标志**同时**含 `CREATE_NO_WINDOW` 与 `BELOW_NORMAL_PRIORITY_CLASS`；非 Windows 断言返回 0；顶部补 `import subprocess` | 这个缺陷在 CI（offscreen、非打包、父进程通常有控制台）里**不可能被行为测试捕获**，唯一可机器化的契约就是标志位本身。测试同时钉住"优先级标志没被挤掉" |

无新增/删除文件；无配置键、无设置页、无 schema 变化。

## 三、性能分析

本改动只改 `CreateProcess` 的一个标志位，**不改变任何执行路径的热度**：

- **稳态开销**：不在播放/渲染/tick 路径上。`_creation_flags()` 仅在 frameseq 供给
  worker 决定要转换某段素材时调用（每次转换 1 次），实现是两次 `getattr` + 一次按位或
  （常量折叠级），无新增系统调用；
- **新增路径成本与触发频率**：零新增路径。触发频率 = frameseq 转换次数
  （本次实机观察 90 秒内 ffmpeg 派生峰值并发 3、重建 422 帧 ≈ 数十次转换），
  与改动前完全一致；
- **有无新的系统调用/网络/磁盘/线程**：无。`CREATE_NO_WINDOW` 只影响子进程的
  控制台分配；ffmpeg 的 stdin/stdout/stderr 仍是既有 PIPE，参数表
  （`_ffmpeg_argv`）一字未动，编码产物等价（本次实机重建 422 帧，见 §四）；
- **内存**：无变化（不新增对象、不新增缓存；控制台不再分配后，理论上还省下
  一个 conhost 窗口对象与其宿主进程内存）；
- **可量化的副作用（正向）**：修复前每个转换进程都会分配一个**可见**控制台窗口；
  修复后窗口句柄采样为 0（§四的表），即消除了"每次重编码闪一个黑窗"的界面干扰。

## 四、实机运行记录

环境：Windows + Edge 153 机器本体；Python 3.13.13 venv；构建
`scripts/build_onedir.ps1 -Variant webm-chat -SkipZip`（exit 0，smoke：exe/`--settings`
均启动成功，中文编码检查 PASS）。

### 4.1 复现（修复前）

像双击一样启动（`Start-Process`，ShellExecute 语义），同时用 `EnumWindows` 枚举
**可见窗口** + 采样 ffmpeg 的 `MainWindowHandle`：

```
新出现的可见窗口:
  pid=12552 标题 = ...\_internal\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe
            [进程 = ffmpeg-win-x86_64-v7.1]
  pid=8700  标题 = StatusBarWnd  [进程 = wetype_update]   ← 与本缺陷无关的输入法窗口

进程链（Get-CimInstance）：
  conhost 19800 ← 父 = ffmpeg 12552 ← 父 = 桌宠 9656
  ffmpeg 12552 命令行 = ...ffmpeg-win-x86_64-v7.1.exe -nostdin -v error -y
      -c:v libvpx-vp9 -i ...\videos\random\变鸽子.webm -pix_fmt bgra
      -c:v libwebp -lossless 1 ...\frameseq\random\变鸽子.g796eba8ebe70.tmp\f_%04d.webp
  ffmpeg 12552 MainWindowHandle = 395908（非 0 = 有可见窗口）
```

命令行里的 `-lossless 1` + `f_%04d.webp` + `.tmp` 目录即 `_ffmpeg_argv` 的特征，
确认窗口来自 frameseq 阶段一编码，而不是播放解码。

### 4.2 A/B（用应用自己的代码路径，不是替身脚本）

`.scratch/web-watch/probe_frameseq_flags.py` 直接 `import pet.frameseq_provision`
并调用其 `_creation_flags()` / `_ffmpeg_argv()`，只替换标志位，用 `pythonw.exe`
（GUI 子系统、无控制台）当父进程，复现打包后的情形：

| 模式 | 标志 | ffmpeg 实测 | 可见窗口 |
| --- | --- | --- | --- |
| legacy（现状） | `0x00004000` | pid 4460 `MainWindowHandle=3672680`，标题 = ffmpeg 路径 | **出现** |
| fixed（修复） | `0x08004000` | 无窗口句柄 | **未出现** |

### 4.3 修复后验证（部署后的真实产物）

先删掉安装目录里已转换的帧序列（3872 个文件）以**强制**启动时重编码，再像双击一样
启动新构建，盯窗 90 秒：

| 指标 | 结果 |
| --- | --- |
| 同时存在的 ffmpeg 进程峰值 | 3 |
| 采样中 ffmpeg `MainWindowHandle ≠ 0` 的次数 | **0** |
| 出现的 ffmpeg 可见窗口 | **0** |
| 重建的帧文件数（证明测试非空转） | **422** |

> 说明：修复后仍会看到"以 ffmpeg 为父进程的 conhost"——`CREATE_NO_WINDOW` 依然会有一个
> **无窗口**的控制台宿主。判别口径是**窗口句柄**，不是 conhost 是否存在；这一点已用
> 4.2 的 A/B 自证（同一套监测脚本在 legacy 下抓到了窗口，在 fixed 下抓不到）。

### 4.4 门禁

- 新增回归：`tests/test_frameseq_provision.py::test_creation_flags_hide_console_window`
  （red→green：改前 `flags & CREATE_NO_WINDOW == 0`）；
- `python -m pytest tests/test_frameseq_provision.py -q`：见提交信息；
- `ruff check pet tests`：All checks passed；
- 全量 pytest 与部署后用户可见行为的确认见提交/对话记录。

## 五、遗留与边界

- **未在 CI 可测**：黑窗只在"打包 + GUI 子系统父进程 + 真有转换工作"三者同时成立时
  出现。CI 里既没有打包产物也没有真实桌面，因此本报告的 A/B（§4.2）与盯窗（§4.3）
  是**唯一**的验证形态，回归只钉标志位；
- **同类风险面**：任何"打包后从 GUI 进程派生控制台程序"的新代码都要带
  `CREATE_NO_WINDOW`。当前全树审查结果：`harness_launcher`/`agent_link`/`updater`
  各自已处理（`CREATE_NO_WINDOW` / `DETACHED_PROCESS`），`click_sound` 那段是
  非 Windows 回退；本次修的是最后一个漏点；
- **素材侧副作用**：frameseq 供给一旦判定陈旧就会重编码（本次 3872 文件被删后重建
  422 帧）。这是既有语义，本次不涉及。
