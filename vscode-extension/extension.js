/**
 * cc-notify VS Code 扩展
 *
 * 自动将 Claude Code 扩展目录中的原生二进制替换为包装器脚本，
 * 当 Claude Code 请求权限确认时弹出桌面三按钮对话框。
 *
 * 仅支持 Linux（Ubuntu / GNOME）。
 */

const vscode = require("vscode");
const fs = require("fs");
const path = require("path");
const os = require("os");

// ── 常量 ────────────────────────────────────────────────────────────

const EXT_ID = "anthropic.claude-code";
const BINARY_NAME = "claude";
const REAL_NAME = "claude-real";
const WRAPPER_PY = "wrapper.py";

// ── 路径 ────────────────────────────────────────────────────────────

function home(...parts) {
  return path.join(os.homedir(), ...parts);
}

function extDir() {
  return home(".vscode", "extensions");
}

function ccNotifyDir() {
  return home(".cc-notify");
}

// ── 查找 Claude Code 扩展 ──────────────────────────────────────────

function findClaudeExtensions() {
  const root = extDir();
  const dirs = [];
  try {
    for (const name of fs.readdirSync(root)) {
      if (name.startsWith(EXT_ID + "-")) {
        dirs.push(name);
      }
    }
  } catch (_) {
    /* extensions dir missing */
  }
  return dirs;
}

function findClaudeBinary(extName) {
  return path.join(extDir(), extName, "resources", "native-binary", BINARY_NAME);
}

function isDeployed(binaryPath) {
  const dir = path.dirname(binaryPath);
  const realPath = path.join(dir, REAL_NAME);
  return fs.existsSync(realPath);
}

// ── 部署包装器 ─────────────────────────────────────────────────────

function deploy(binaryPath) {
  const dir = path.dirname(binaryPath);
  const realPath = path.join(dir, REAL_NAME);
  const wrapperPath = path.join(dir, BINARY_NAME);

  // 1. 确保 wrapper.py 在 ~/.cc-notify/ 下
  const ccDir = ccNotifyDir();
  if (!fs.existsSync(ccDir)) {
    fs.mkdirSync(ccDir, { recursive: true });
  }
  const wrapperPyPath = path.join(ccDir, WRAPPER_PY);
  // 从扩展目录复制 wrapper.py（与 extension.js 同级）
  const srcWrapper = path.join(__dirname, WRAPPER_PY);
  if (fs.existsSync(srcWrapper)) {
    fs.copyFileSync(srcWrapper, wrapperPyPath);
  }

  // 2. 备份原生二进制 claude → claude-real（如果还没备份）
  if (!fs.existsSync(realPath)) {
    fs.renameSync(binaryPath, realPath);
  } else {
    // 如果 wrapper 脚本已存在，先删掉，防止多次部署叠加
    if (fs.existsSync(wrapperPath)) {
      try { fs.unlinkSync(wrapperPath); } catch (_) {}
    }
  }

  // 3. 创建包装器脚本
  const script = `#!/bin/bash
# cc-notify wrapper — auto-generated, do not edit
DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "${wrapperPyPath}" "$DIR/${REAL_NAME}" "$@"
`;
  fs.writeFileSync(wrapperPath, script, { mode: 0o755 });

  return { realPath, wrapperPath };
}

function remove(binaryPath) {
  const dir = path.dirname(binaryPath);
  const realPath = path.join(dir, REAL_NAME);
  const wrapperPath = path.join(dir, BINARY_NAME);

  if (fs.existsSync(realPath)) {
    // 删掉 wrapper 脚本
    if (fs.existsSync(wrapperPath)) {
      try { fs.unlinkSync(wrapperPath); } catch (_) {}
    }
    // 还原原生二进制
    fs.renameSync(realPath, binaryPath);
  }
}

// ── 部署所有 Claude Code 版本 ──────────────────────────────────────

function deployAll() {
  const exts = findClaudeExtensions();
  const results = [];
  for (const extName of exts) {
    const binaryPath = findClaudeBinary(extName);
    if (!fs.existsSync(binaryPath)) continue;
    // 跳过已经部署过的 (real 存在)
    if (isDeployed(binaryPath)) continue;
    try {
      const r = deploy(binaryPath);
      results.push({ extName, status: "deployed", ...r });
    } catch (e) {
      results.push({ extName, status: "error", error: e.message });
    }
  }
  return results;
}

// ── 入口 ────────────────────────────────────────────────────────────

let watcher = null;

function activate(context) {
  const output = vscode.window.createOutputChannel("cc-notify");
  output.appendLine("[cc-notify] 扩展已激活");

  // 部署命令
  const deployCmd = vscode.commands.registerCommand("cc-notify.deploy", () => {
    output.appendLine("[cc-notify] 手动部署...");
    const results = deployAll();
    if (results.length === 0) {
      vscode.window.showInformationMessage("cc-notify: 未找到需要部署的 Claude Code 扩展");
    } else {
      for (const r of results) {
        output.appendLine(`  ${r.extName}: ${r.status}`);
      }
      vscode.window.showInformationMessage(`cc-notify: 已部署 ${results.length} 个版本`);
    }
  });

  // 移除命令
  const removeCmd = vscode.commands.registerCommand("cc-notify.remove", () => {
    output.appendLine("[cc-notify] 移除中...");
    const exts = findClaudeExtensions();
    let count = 0;
    for (const extName of exts) {
      const binaryPath = findClaudeBinary(extName);
      if (!fs.existsSync(binaryPath) || !isDeployed(binaryPath)) continue;
      try {
        remove(binaryPath);
        count++;
      } catch (e) {
        output.appendLine(`  ${extName}: error - ${e.message}`);
      }
    }
    vscode.window.showInformationMessage(`cc-notify: 已移除 ${count} 个版本的包装器`);
  });

  // 状态命令
  const statusCmd = vscode.commands.registerCommand("cc-notify.status", () => {
    const exts = findClaudeExtensions();
    const lines = [];
    for (const extName of exts) {
      const binaryPath = findClaudeBinary(extName);
      const deployed = isDeployed(binaryPath);
      lines.push(`  ${extName}: ${deployed ? "已部署 ✓" : "未部署 ✗"}`);
    }
    if (lines.length === 0) {
      vscode.window.showInformationMessage("cc-notify: 未找到 Claude Code 扩展");
    } else {
      const msg = "cc-notify 部署状态:\n" + lines.join("\n");
      output.appendLine(msg);
      vscode.window.showInformationMessage(msg);
    }
  });

  context.subscriptions.push(deployCmd, removeCmd, statusCmd);

  // 启动时自动部署
  const results = deployAll();
  if (results.length > 0) {
    for (const r of results) {
      output.appendLine(`[cc-notify] 已部署: ${r.extName}`);
    }
  }

  // 监听扩展目录变化（Claude Code 更新后自动重部署）
  const watchDir = extDir();
  try {
    watcher = fs.watch(watchDir, (eventType, filename) => {
      if (!filename || !filename.startsWith(EXT_ID)) return;
      if (eventType !== "rename") return;
      // 新版本安装后稍等片刻再部署
      setTimeout(() => {
        const binaryPath = findClaudeBinary(filename);
        if (!fs.existsSync(binaryPath) || isDeployed(binaryPath)) return;
        try {
          deploy(binaryPath);
          output.appendLine(`[cc-notify] 检测到更新，已自动部署: ${filename}`);
        } catch (e) {
          output.appendLine(`[cc-notify] 自动部署失败: ${filename} - ${e.message}`);
        }
      }, 3000);
    });
  } catch (_) {
    // fs.watch 在某些系统不可用，忽略
  }

  // 初始部署状态
  output.appendLine(
    `[cc-notify] 发现 ${findClaudeExtensions().length} 个 Claude Code 版本`
  );
}

function deactivate() {
  if (watcher) {
    watcher.close();
    watcher = null;
  }
}

module.exports = { activate, deactivate };
