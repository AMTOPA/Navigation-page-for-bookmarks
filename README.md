# Navigation page for bookmarks

为您的edge浏览器收藏夹建立一个导航页面。



---

## 使用效果

![本地路径](.pic/6.png "示例图片")

![本地路径](.pic/search.png "示例图片")

---

## 使用方法

1. 首先安装所需要的库，双击requirements.bat即可进行安装。

![](.pic/1.png "安装库示例")

2.去[智谱AI开放平台](https://www.bigmodel.cn/usercenter/proj-mgmt/apikeys)申请一个apikey

申请流程：注册账号并登录-->再次访问上述链接-->按照下图所示申请即可（本程序使用的为免费的模型）

![本地路径](.pic/2.png)

3.从edge浏览器导出收藏夹

**首先**在浏览器输入：edge://favorites

![](.pic/3.png "示例图片")

**然后**点击右上角导出收藏夹。

4.执行主程序

将导出的html复制到Navigation page for bookmarks文件夹下，并重命名为bookmarks.html。双击run.bat

首次使用需要输入申请的API秘钥：

![本地路径](.pic/4.png "示例图片")

然后程序会分三步执行：提取json文件-->调用ai分类-->获取网页图标

![本地路径](.pic/5.png "示例图片")

5.打开web-ui页面

最后双击web-ui.bat即可自动打开

![本地路径](.pic/6.png "示例图片")

## 后续使用

如果有增加的标签，程序会自动对比哈希值，减去重复调用api等步骤，减少资源的浪费。

![本地路径](.pic/new.png "示例图片")

如果想要删除某个网页，需要手动删除。（后续可能会改为自动删除）


