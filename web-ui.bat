@echo off
title ��ǩ����ϵͳ

echo ����������ǩ����ϵͳ...
echo.

REM ���Python�Ƿ�װ
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ����: δ��⵽Python�����Ȱ�װPython
    pause
    exit /b
)

REM ����Ƿ�������������ǩ�ļ�
if not exist "bookmarks_final.json" (
    echo ����: δ�ҵ���ǩ�ļ�����������main.py������ǩ
)

REM ���index.html�Ƿ����
if not exist "index.html" (
    echo ����: δ�ҵ�index.html�ļ�
    pause
    exit /b
)

REM ����Python HTTP���������������
start "" "http://localhost:8000"
echo �����������������ʵ�ַ: http://localhost:8000
echo ��Ctrl+Cֹͣ������
echo.
python -m http.server 8000