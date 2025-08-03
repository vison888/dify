# Dify API 调试环境配置指南

## 概述

本文档详细说明如何在 VSCode 中配置 Dify API 的调试环境，实现代码断点调试、热重载等功能。配置完成后，您可以在 VSCode 中直接启动和调试 Dify API 服务。

## 前置条件

### 1. 软件要求

- **Visual Studio Code** (最新版本)
- **Python** (3.11 或 3.12)
- **UV** 包管理器
- **Git** (用于克隆代码仓库)

### 2. VSCode 扩展

确保安装以下 VSCode 扩展：

```
- Python (Microsoft)
- Python Debugger (Microsoft) 
- Python Docstring Generator
- Python Indent
- autoDocstring - Python Docstring Generator
- GitLens — Git supercharged
```

安装命令：
```bash
code --install-extension ms-python.python
code --install-extension ms-python.debugpy
code --install-extension njpwerner.autodocstring
code --install-extension visualstudioexptteam.vscodeintellicode
code --install-extension eamodio.gitlens
```

### 3. 环境准备

确保已完成以下基础配置：

1. **中间件服务已启动** (参考 `外网中间件配置调整.md`)
2. **配置文件已设置** (`.env` 文件已配置)
3. **依赖已安装** (通过 `uv sync --dev` 命令)

## 调试配置详情

### 1. VSCode 配置文件结构

项目中已创建以下配置文件：

```
.vscode/
├── launch.json      # 调试启动配置
├── settings.json    # 工作区设置
└── tasks.json       # 任务配置
```

### 2. 调试配置 (launch.json)

配置了三种调试模式：

#### a) Python: Flask (UV) - 主要调试模式
- **用途**: 调试 Flask API 服务
- **特点**: 支持热重载、断点调试、Jinja 模板调试
- **启动方式**: F5 或调试面板选择该配置

#### b) Python: Celery (UV) - 异步任务调试
- **用途**: 调试 Celery 异步任务
- **特点**: 支持后台任务断点调试
- **适用场景**: 数据集导入、文档索引等异步任务

#### c) Python: Flask (Simple) - 简化调试模式
- **用途**: 简单的 Flask 应用调试
- **特点**: 直接运行 app.py
- **适用场景**: 快速测试和简单调试

#### d) Launch Flask and Celery - 组合模式
- **用途**: 同时启动 Flask 和 Celery 服务
- **特点**: 完整的开发环境
- **适用场景**: 完整功能测试

### 3. 工作区设置 (settings.json)

主要配置项：

```json
{
    "python.pythonPath": "${workspaceFolder}/.venv/Scripts/python.exe",
    "python.defaultInterpreterPath": "${workspaceFolder}/.venv/Scripts/python.exe",
    "python.envFile": "${workspaceFolder}/.env",
    "python.linting.enabled": true,
    "python.formatting.provider": "black"
}
```

### 4. 任务配置 (tasks.json)

提供了以下快捷任务：

- **Check UV Environment**: 检查 UV 环境
- **UV Sync**: 同步依赖
- **Flask DB Upgrade**: 数据库迁移
- **Start Flask Development Server**: 启动开发服务器
- **Start Celery Worker**: 启动 Celery 工作进程

## 操作指南

### 1. 环境初始化

#### 步骤 1: 安装依赖
```bash
# 在 VSCode 终端中执行
cd /e:/gitcode/dify/api
uv sync --dev
```

#### 步骤 2: 配置环境变量
```bash
# 确保 .env 文件存在并已正确配置
# 生成 SECRET_KEY (如果为空)
openssl rand -base64 42
```

#### 步骤 3: 数据库迁移
```bash
# 使用 VSCode 任务或手动执行
uv run flask db upgrade
```

### 2. 启动调试

#### 方法一: 使用调试面板
1. 按 `Ctrl+Shift+D` 打开调试面板
2. 选择 "Python: Flask (UV)" 配置
3. 点击绿色播放按钮或按 `F5`

#### 方法二: 使用命令面板
1. 按 `Ctrl+Shift+P` 打开命令面板
2. 输入 "Debug: Start Debugging"
3. 选择相应的调试配置

#### 方法三: 使用快捷键
1. 打开任意 Python 文件
2. 按 `F5` 直接启动调试

### 3. 设置断点

#### 在代码中设置断点
1. 点击代码行号左侧的空白区域
2. 红色圆点表示断点已设置
3. 再次点击可取消断点

#### 条件断点
1. 右键点击断点
2. 选择 "Edit Breakpoint"
3. 设置条件表达式

#### 日志断点
1. 右键点击行号
2. 选择 "Add Logpoint"
3. 输入要输出的表达式

### 4. 调试操作

#### 调试控制
- **继续执行**: `F5`
- **单步跳过**: `F10`
- **单步跳入**: `F11`
- **单步跳出**: `Shift+F11`
- **重启调试**: `Ctrl+Shift+F5`
- **停止调试**: `Shift+F5`

#### 变量查看
- **变量面板**: 查看当前作用域的变量
- **监视面板**: 添加自定义表达式监视
- **调用堆栈**: 查看函数调用链
- **悬停查看**: 鼠标悬停在变量上查看值

### 5. 高级调试功能

#### 调试控制台
- 按 `Ctrl+Shift+Y` 打开调试控制台
- 可以执行 Python 表达式
- 查看变量值和调用函数

#### 异常调试
1. 在调试配置中设置 `"justMyCode": false`
2. 可以调试第三方库代码
3. 自动在异常处暂停

#### 远程调试
如果需要调试远程服务器上的代码：
1. 修改 launch.json 中的 host 配置
2. 确保远程服务器安装了 debugpy
3. 配置端口转发

## 常见问题解决

### 1. Python 解释器未找到

**问题**: VSCode 无法找到 Python 解释器

**解决方案**:
```bash
# 方法一: 使用命令面板选择解释器
# Ctrl+Shift+P -> Python: Select Interpreter
# 选择 .venv/Scripts/python.exe

# 方法二: 检查 UV 虚拟环境
uv --version
uv sync --dev
```

### 2. 环境变量未加载

**问题**: `.env` 文件中的环境变量未生效

**解决方案**:
1. 确保 `.env` 文件位于项目根目录
2. 检查 `launch.json` 中 `envFile` 配置
3. 重启 VSCode

### 3. 断点未命中

**问题**: 设置的断点没有被触发

**解决方案**:
1. 确保代码路径正确
2. 检查 `justMyCode` 设置
3. 确认代码确实被执行到

### 4. 模块导入错误

**问题**: 调试时出现模块导入错误

**解决方案**:
```json
// 在 launch.json 中确保设置了 PYTHONPATH
"env": {
    "PYTHONPATH": "${workspaceFolder}",
    "FLASK_APP": "app.py"
}
```

### 5. 端口占用问题

**问题**: 5001 端口已被占用

**解决方案**:
```bash
# 查找占用端口的进程
netstat -ano | findstr :5001

# 结束进程 (替换 PID)
taskkill /PID <PID> /F

# 或者修改调试配置使用其他端口
```

## 性能优化建议

### 1. 调试配置优化

```json
{
    "justMyCode": true,           // 只调试自己的代码
    "console": "integratedTerminal",  // 使用集成终端
    "jinja": true                 // 启用 Jinja 模板调试
}
```

### 2. 文件排除设置

```json
{
    "files.exclude": {
        "**/__pycache__": true,
        "**/*.pyc": true,
        ".venv": false  // 显示虚拟环境目录
    }
}
```

### 3. 自动格式化

```json
{
    "[python]": {
        "editor.formatOnSave": true,
        "editor.codeActionsOnSave": {
            "source.organizeImports": "explicit"
        }
    }
}
```

## 最佳实践

### 1. 代码调试流程

1. **设置日志级别**: 在开发环境中使用 DEBUG 级别
2. **使用断点策略**: 在关键业务逻辑处设置断点
3. **变量监视**: 监视关键变量的值变化
4. **异常处理**: 在异常处理块设置断点

### 2. 调试技巧

#### 条件断点使用
```python
# 只在特定条件下暂停
# 断点条件: user_id == 'specific_user'
def process_user(user_id):
    # 业务逻辑
    pass
```

#### 日志断点使用
```python
# 日志断点表达式: f"Processing user: {user_id}"
def process_user(user_id):
    # 不需要修改代码即可输出日志
    pass
```

### 3. 多服务调试

当需要同时调试 Flask 和 Celery 服务时：
1. 使用 "Launch Flask and Celery" 组合配置
2. 在不同的终端窗口查看各服务日志
3. 分别在相应代码中设置断点

## 快捷键参考

| 功能 | 快捷键 | 说明 |
|------|--------|------|
| 开始调试 | F5 | 启动调试或继续执行 |
| 单步跳过 | F10 | 逐行执行，不进入函数 |
| 单步跳入 | F11 | 进入函数内部 |
| 单步跳出 | Shift+F11 | 跳出当前函数 |
| 停止调试 | Shift+F5 | 停止调试会话 |
| 重启调试 | Ctrl+Shift+F5 | 重新启动调试 |
| 切换断点 | F9 | 在当前行设置/取消断点 |
| 调试面板 | Ctrl+Shift+D | 打开调试面板 |
| 调试控制台 | Ctrl+Shift+Y | 打开调试控制台 |
| 命令面板 | Ctrl+Shift+P | 打开命令面板 |

## 故障排除清单

在遇到问题时，请依次检查：

- [ ] UV 环境是否正确安装和激活
- [ ] Python 解释器路径是否正确
- [ ] `.env` 文件是否存在并配置正确
- [ ] 依赖是否完全安装 (`uv sync --dev`)
- [ ] 数据库是否已迁移 (`uv run flask db upgrade`)
- [ ] 中间件服务是否正常运行
- [ ] 端口是否被占用
- [ ] VSCode Python 扩展是否已安装
- [ ] 工作区是否正确打开 (打开整个 api 目录)

## 总结

通过以上配置，您可以在 VSCode 中享受完整的 Dify API 调试体验：

- ✅ 代码断点调试
- ✅ 变量实时监视
- ✅ 热重载开发
- ✅ 异常自动捕获
- ✅ 多服务并行调试
- ✅ 集成终端支持

建议在开发过程中充分利用这些调试功能，提高开发效率和代码质量。

---

**文档创建时间**: 2025-01-14  
**最后更新时间**: 2025-01-14  
**版本**: v1.0  
**适用环境**: Windows + VSCode + UV