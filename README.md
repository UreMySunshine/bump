# Bump

基于 Textual 的终端工具管理界面。集中查看 Homebrew、npm 全局包和独立工具的版本，筛选并选择需要更新的项目。

## 功能

- Homebrew formula、cask 和依赖包清单。
- 当前 npm 全局目录中的直接安装包清单。
- Bun、rustup、Rust、uv、OpenCode、Pi 和 Claude Code 的版本检查。
- 深浅主题、名称搜索、来源与状态筛选。
- 更新前确认，按已处理项目数显示进度，保留本次会话的成功与失败结果。

主要在 macOS 上验证。独立工具的不同安装渠道可能需要使用原包管理器升级；网络或版本查询失败会显示“未知”。

## 安装与运行

需要 [uv](https://docs.astral.sh/uv/getting-started/installation/)：

```sh
git clone https://github.com/UreMySunshine/bump.git
cd bump
chmod +x bump
./bump
```

首次启动由 uv 创建隔离环境并安装 Textual 8.2.8，需要联网。也可以使用 Python 3.10+：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python bump
```

## 键盘操作

| 按键 | 操作 |
| --- | --- |
| j / k 或上下键 | 列表上下移动 |
| h / l 或左右键 | 列表横向滚动 |
| 空格 | 勾选或取消可更新项 |
| Enter | 为已勾选项目打开确认框 |
| Ctrl+A / Ctrl+N | 选择当前筛选结果中的可更新项 / 清空选择 |
| / | 聚焦搜索 |
| Ctrl+T | 深浅主题切换 |
| Ctrl+R | 重新检查 |
| Ctrl+Q | 退出 |
| Escape | 关闭对话框 |

确认框默认聚焦“取消”；Tab 移至“确认更新”，再按 Enter 执行。
更新进度百分比按已处理项目数计算，不代表单个包的下载进度；失败项目计入已处理数量。

## 命令行检查

```sh
./bump --check --no-color
./bump --json
```

检查模式不升级软件。文本模式发现可更新项时退出码为 1，否则为 0；JSON 模式退出码为 0，调用方需检查结果中的 errors。

## uv 独立安装凭据缺失

默认使用 `uv self update`。若已确认 uv 来自 Astral 官方独立安装脚本，但安装凭据缺失，可显式指定其路径：

```sh
BUMP_UV_STANDALONE_PATH="$HOME/.local/bin/uv" ./bump
```

仅在当前 uv 路径精确匹配且不是符号链接时启用：下载 Astral 官方安装脚本，使用原目录更新 uv/uvx，设置 `UV_NO_MODIFY_PATH=1`。仍需在界面确认后执行。通过 pip、Homebrew 等安装的 uv 不应使用此选项。

## 测试

```sh
uv run --with textual==8.2.8 --python 3.14 python test_bump.py
```

测试模拟包清单和更新程序，不实际升级工具。

## Homebrew

目前尚未发布 Homebrew formula。可通过个人 tap 提供安装，后续需准备固定版本源码、依赖资源、校验值和安装测试。
