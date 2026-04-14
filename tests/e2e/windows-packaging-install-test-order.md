# Windows 打包与安装测试顺序（CLI + EXE）

> 目标: 在同一轮回归中同时验证  
> 1) Python wheel 安装路径（接近 PyPI 用户）  
> 2) Desktop EXE 打包安装路径（真实桌面发布环境）

---

## 结论先行

- 是的，建议你准备 **两份产物**：
  - 一份 `wheel`（CLI 安装路径）
  - 一份 `EXE`（Desktop 打包安装路径）
- 建议流程是：**先 CLI，再 EXE**。
- 建议在 EXE 测试前清理 CLI 安装影响（卸载或换干净环境），避免交叉污染。

---

## A. CLI wheel 路径（做法 B）

这是你给的做法，保留为标准流程：

```powershell
python -m build
pip install dist\openakita-*.whl
openakita --help
```

说明：

- 这条路径最接近 `pip install openakita[all]` 用户行为。
- 能较好暴露: 包缺文件、entry point 错误、依赖漏装、安装后无法启动等问题。
- 注意 `pyproject.toml` 的 `force-include`（前端产物/文档/技能等）会影响 wheel 内容；如果本地未构建相关产物，和完整发版可能有差异。

---

## B. EXE 打包路径（Desktop）

在 `apps/setup-center` 下常规打包：

```powershell
npm install
npm run build
npm run tauri build
```

安装并验证：

1. 安装生成的 EXE（或 installer）
2. 启动 Desktop
3. 验证后端接口可用（如 `GET /api/health`）
4. 运行 Agent 回归子集（8~10 轮 AI 探索）
5. 运行人工 Desktop Checklist

---

## 推荐完整顺序（可直接执行）

### 阶段 1: CLI 主样本验证

1. 构建并安装 wheel
2. 冒烟: `openakita --help`
3. 跑 Agent 自动测试（CLI 全量）:
   - 用 `tests/e2e/agent-auto-test-runbook.md`
   - 阶段 6 跑 20+ 轮
4. 如需，跑人工 CLI 清单:
   - `tests/e2e/manual-test-checklist.md` 的 CLI 部分
5. 保存报告: `report-cli-agent.md`

### 阶段 2: 环境隔离（进入 EXE 前）

推荐至少做其中一种：

- 方案 A（同机清理）:
  - `pip uninstall openakita -y`
  - 若用过 pipx：`pipx uninstall openakita`
  - 清理/备份用户配置与缓存目录（避免读到旧配置）
- 方案 B（更稳）:
  - 在全新虚拟机或全新系统用户下安装 EXE 直接测

### 阶段 3: EXE 发布态验证

1. 打包并安装 EXE
2. 跑 Agent 自动回归子集（EXE 8~10 轮）
3. 跑人工 Desktop Checklist（重点 UI/托盘/快捷键/上传/语音）
4. 保存报告: `report-exe-agent.md`

### 阶段 4: 差异分析

输出三类问题：

- 仅 CLI 复现
- 仅 EXE 复现（优先级更高，通常是打包/运行时差异）
- CLI/EXE 都复现

---

## 什么时候可以不卸载 CLI？

仅在以下场景可放宽：

- EXE 测试在独立机器或独立系统用户中执行
- 或你明确验证了 EXE 不会复用 CLI 的 Python 环境/配置目录

否则仍建议卸载/隔离，避免“看起来通过但环境被污染”的假阳性。

