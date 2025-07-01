<div align="center"><h1>Navigation Page for Bookmarks 🌐🔖</h1>

<a href="README_zh.md">简体中文</a>  |  ENGLISH

[![GitHub release](https://img.shields.io/github/release/AMTOPA/Navigation-page-for-bookmarks.svg)](https://github.com/AMTOPA/Navigation-page-for-bookmarks/releases)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/platform-Windows-blue)](https://www.microsoft.com/windows)
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://github.com/AMTOPA/Navigation-page-for-bookmarks/graphs/commit-activity)
[![Blog](https://img.shields.io/badge/📖_My_Blog-math--enthusiast.top-FF5733)](https://math-enthusiast.top/)

</div>

A beautiful and functional navigation page for Edge browser bookmarks with AI-powered categorization.

---

## ✨ Features

- 🧠 AI-powered bookmark categorization
- 🎨 Clean and aesthetic UI design
- ⚡ Quick access to frequently used sites
- 🔄 Automatic detection of new bookmarks
- 🌐 Automatic favicon fetching

## 🖼️ Preview

![Main Interface](pic/6.png "Main Interface")
![Search Function](pic/search.png "Search Feature")

---

## 🛠️ Usage Guide

### 1. Install Dependencies

Run `requirements.bat` to automatically install required Python libraries:
![Install Dependencies](pic/1.png)

### 2. Get API Key

Apply for a free API key at [Zhipu AI Platform](https://www.bigmodel.cn/usercenter/proj-mgmt/apikeys):
![API Application](pic/2.png)

### 3. Export Edge Bookmarks

1. Enter `edge://favorites` in Edge address bar
2. Click "Export bookmarks" in top-right corner
   ![Export Bookmarks](pic/3.png)

### 4. Run Main Program

1. Copy the exported HTML file to project folder and rename to `bookmarks.html`
2. Double-click `run.bat`
3. Enter your API key when prompted (first run only):
   ![Enter API Key](pic/4.png)
4. The program will automatically:
   - Extract bookmark data
   - Perform AI categorization
   - Fetch website favicons
     ![Processing Steps](pic/5.png)

### 5. Launch Web Interface

Run `web-ui.bat` to open the navigation page:
![Final Result](pic/6.png)

---

## 🔄 Subsequent Usage

- New bookmarks will be automatically detected and processed
- Hash comparison prevents duplicate processing
  ![Update Detection](pic/new.png)

> ⚠️ Note: Currently bookmark deletion requires manual operation (auto-sync coming in future versions)

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details






