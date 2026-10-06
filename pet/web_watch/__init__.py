# -*- coding: utf-8 -*-
"""Edge 网页内容实时互动（web watch）。

浏览器扩展（`integrations/edge-web-watch/`）把当前页面的标题/正文/划词/视频进度
POST 到桌宠的本地回环端口，桌宠据此**评论**（评论页面内容、划词内容、视频进度）
并**给建议**（只在代码/文档/论文/新闻/问答这类页面、且用户停下来时）。

分层（依赖单向：service → policy/digest/llm → protocol）：

- `protocol.py`：事件协议与唯一的输入校验/裁剪（零 Qt）；
- `digest.py`：隐私摘要（丢查询串、正文截断、页面分类）（零 Qt）；
- `policy.py`：触发策略与页级去重（零 Qt，时钟可注入）；
- `llm.py`：非流式短文本生成（仿 pet/vision.py 的请求骨架）；
- `server.py`：本地回环 HTTP 接收端（标准库，后台线程）；
- `service.py`：Qt 侧装配（信号桥 → GUI 线程决策 → 频控 → 气泡）。
"""
