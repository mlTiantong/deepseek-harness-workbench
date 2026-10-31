# 上游来源

## 窗口代码
- [wraven68/deepseek-harness-pet](https://github.com/wraven68/deepseek-harness-pet)
- 固定版本：`727554719360d58c49a16d3f4a4cc736ec95b4d8`
- window.py 基于上游 pet.py；MIT，Copyright (c) 2026 wraven68，见 LICENSE。
- 旧版五张成人比例静态立绘已移除。

## 迷你 Q 版动画
- [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)
- 固定版本：`0b8a6ad98ad2bc2bee986cd027936a5eeb3accc8`
- 17 段透明 WebM 动画，转换为 15 fps、360×360 透明动画 WebP。
- 素材署名、非商业和相同方式共享要求见 ASSET_LICENSE.md；不要将代码 MIT 当作动画许可。
- assets/animations.json 记录动作、原始文件名及逐帧时长；tools/build_pet_assets.py 可重建。

## Companion 接入
- 用 `/api/pet` 的 action 和任务标题驱动动作：回消息、看视频、读评论、写稿、画图、制作代码、思考、搜索、归档、完成、休息。
- 按真实帧时长播放和跳帧；只解码当前动画，不常驻整套动画帧。
- 待命时随机呼吸、玩魔方、跳舞、吃火锅；点击挥手，拖拽时有悬空动作。
- 浅色圆角卡片仅 248 px 宽，82% 不透明度，最多显示 3 条任务；保留角色分工、当前媒体阶段、自主方向、工作台入口、暂停/恢复、缩放与位置记忆。
- 只展示本项目状态，不扫描其他会话，不运行第二套模型或自主任务调度。
