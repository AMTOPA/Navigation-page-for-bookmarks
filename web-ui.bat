@echo off
title 书签导航系统

echo 正在启动书签导航系统...
echo.

REM 检查Python是否安装
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo 错误: 未检测到Python，请先安装Python
    pause
    exit /b
)

REM 检查是否已生成最终书签文件
if not exist "bookmarks_final.json" (
    echo 警告: 未找到书签文件，请先运行main.py处理书签
)

REM 检查index.html是否存在
if not exist "index.html" (
    echo 错误: 未找到index.html文件
    pause
    exit /b
)

REM 启动Python HTTP服务器并打开浏览器
start "" "http://localhost:8000"
echo 服务器已启动，访问地址: http://localhost:8000
echo 按Ctrl+C停止服务器
echo.
python -m http.server 8000