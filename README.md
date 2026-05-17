# cc-notify

桌面通知工具，解决在使用 Claude Code 时因切屏而错过确认提示的问题,可以在做项目时做其它的事情，需要确认时会自动弹窗，提升工作效率。

当 Claude Code 等待用户确认（工具调用权限、Y/n 提示等）时，自动弹出桌面对话框，用户点击 Allow/Deny 即可回复，无需切回终端或 VS Code。

## 两种使用场景

### 场景 A：终端 CLI 模式

直接在终端中包装 `claude` 命令：

```bash
python3 cc-notify.py claude
# 或安装到 PATH 后
cc-notify claude
```

通过 PTY 代理监控终端输出，检测到确认模式时弹出 zenity 对话框。

### 场景 B：VS Code 扩展模式（推荐）

在 VS Code 聊天面板中使用 Claude Code 时，自动拦截确认请求。

部署方式：将 Claude Code 扩展目录中的原生二进制替换为包装器脚本。

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

重启 VS Code 后即可生效。

## 工作原理（场景 B）

```
VS Code 扩展
    ↓ JSON 流 (stdin/stdout)
┌─ wrapper.py ──────────────────────────────┐
│  解析 JSON 流                              │
│  检测 can_use_tool 确认请求 → 拦截         │
│  弹出 zenity 对话框 (Allow / Deny)         │
│  用户点击 → 注入正确 JSON 回复             │
└────────────────────────────────────────────┘
    ↓ JSON 流
claude-real (原生二进制)
```

## 依赖

- Python 3.8+
- zenity（Ubuntu 预装，`sudo apt install zenity`）

无 Python 第三方依赖，仅使用标准库。

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
