# cc-notify

让它帮你干活，你去刷剧、浏览网页、做任何事。

Claude Code 需要确认时，桌面上会弹出一个三按钮对话框，点一下就行，不用切回来。真正的效率提升 —— 不用盯着屏幕等它问你。

## 平台支持

| 平台 | 状态 |
|------|------|
| Linux (Ubuntu / GNOME) | 完整支持 |
| macOS | 不支持 |
| Windows | 不支持 |

目前仅支持 Linux 桌面环境。macOS 和 Windows 用户暂无法使用。

## 什么时候会弹窗？

Claude Code 在执行 Bash 命令、编辑文件等操作前需要你确认权限时，cc-notify 拦截到请求，弹出 GTK3 原生对话框：

```
┌──────────────────────────────────────┐
│  cc-notify                            │
│  ───────────────────────────────────  │
│  Bash - 需要确认权限                   │
│                                       │
│  描述：列出当前目录下的文件             │
│                                       │
│  预览：                                │
│    ls /home/maoheng/项目/              │
│                                       │
│  请选择操作：                           │
│  ───────────────────────────────────  │
│   [允许本次]   [始终允许]   [拒绝]      │
│    蓝色高亮                  红色       │
└──────────────────────────────────────┘
```

- 弹窗**置顶**，不会被其他窗口挡住
- 仅响应**鼠标点击**，键盘乱按不会误触
- 选择「始终允许」后同类操作不再询问

## 使用场景

### 场景 B：VS Code 扩展模式（推荐）

在 VS Code 的 Claude Code 插件中自动工作，无需改变任何使用习惯。

**部署一次，永久生效：**

```bash
# 进入扩展目录
cd ~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/

# 备份原生二进制
mv claude claude-real

# 创建包装器脚本
cat > claude << 'EOF'
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 /path/to/cc-notify/wrapper.py "$DIR/claude-real" "$@"
EOF
chmod +x claude
```

重启 VS Code 后即可生效。在聊天面板正常使用 Claude Code，确权时自动弹窗。

### 场景 A：终端 CLI 模式

直接在终端中包装 `claude` 命令：

```bash
cc-notify claude
```

通过 PTY 代理监控终端输出，检测到确认模式时弹出对话框。

## 工作原理（场景 B）

```
VS Code 扩展
    ↓ JSON 流 (stdin/stdout)
┌─ wrapper.py ──────────────────────────────┐
│  解析 JSON 流                              │
│  检测 can_use_tool 确认请求 → 拦截         │
│  弹出 GTK3 三按钮对话框                     │
│  用户点击 → 注入正确 JSON 回复             │
└────────────────────────────────────────────┘
    ↓ JSON 流
claude-real (原生二进制)
```

## 依赖

- Python 3.8+
- PyGObject (python3-gi)，Ubuntu 预装

无第三方 Python 依赖。

## 配置

可选配置文件 `~/.cc-notify.json`：

```json
{
    "patterns": [
        "Y/n", "y/N", "yes/no",
        "Confirm", "Proceed", "Allow",
        "确认", "是否继续"
    ],
    "ignore_patterns": [],
    "cooldown_seconds": 10
}
```

## 文件说明

| 文件 | 用途 |
|------|------|
| `wrapper.py` | VS Code 扩展模式，JSON 协议拦截（主力） |
| `cc-notify.py` | 终端 CLI 模式，PTY 代理 + 文本匹配 |
| `probe.py` | 协议探针，用于分析扩展与 Claude 的通信 |
| `cc-notify.json.example` | 配置文件样例 |

## License

MIT
