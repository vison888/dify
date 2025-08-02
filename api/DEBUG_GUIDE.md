# Dify API 断点调试指南

## 配置说明

已为您配置好了VS Code/Cursor的断点调试环境，包含以下文件：

### 1. `.vscode/launch.json` - 调试配置
包含三种调试配置：
- **Python: Flask Debug** - 直接运行app.py进行调试
- **Python: Flask with UV** - 使用UV环境运行Flask（推荐）
- **Python: Current File** - 调试当前打开的Python文件

### 2. `.vscode/settings.json` - VS Code设置
- 配置了Python解释器路径
- 启用了代码格式化和linting
- 设置了环境变量文件路径

### 3. `.env` - 环境变量
添加了 `FLASK_DEBUG=1` 来启用Flask调试模式

## 使用方法

### 方法一：使用VS Code/Cursor调试器（推荐）

1. 在代码中设置断点（点击行号左侧或按F9）
2. 按 `F5` 或点击调试面板的"开始调试"按钮
3. 选择 "Python: Flask with UV" 配置
4. 应用将在调试模式下启动，遇到断点时会自动暂停

### 方法二：命令行调试

如果您仍想使用命令行，可以使用以下命令：

```bash
# 使用UV运行（推荐）
uv run python -m debugpy --listen 5678 --wait-for-client app.py

# 或者直接运行
uv run python app.py
```

## 调试技巧

1. **设置断点**：在需要调试的代码行左侧点击，或按F9
2. **条件断点**：右键断点可设置条件，只有满足条件时才会暂停
3. **查看变量**：调试时可在"变量"面板查看当前作用域的所有变量
4. **调试控制台**：可以在调试控制台中执行Python表达式
5. **调用堆栈**：查看函数调用链，了解代码执行路径

## 常用快捷键

- `F5` - 开始调试/继续执行
- `F9` - 设置/取消断点
- `F10` - 单步跳过（Step Over）
- `F11` - 单步进入（Step Into）
- `Shift+F11` - 单步跳出（Step Out）
- `Shift+F5` - 停止调试

## 注意事项

1. 确保已安装Python扩展
2. 确保UV环境已正确配置
3. 如果遇到gevent相关问题，调试配置已自动禁用gevent
4. 调试模式下性能会有所下降，这是正常现象

## 故障排除

如果调试无法启动：
1. 检查Python解释器路径是否正确
2. 确认.env文件中的FLASK_DEBUG=1设置
3. 检查端口5001是否被占用
4. 查看调试控制台的错误信息

现在您可以愉快地进行断点调试了！🎉