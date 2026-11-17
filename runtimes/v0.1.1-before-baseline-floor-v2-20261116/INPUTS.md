# 当前版本如何输入文件和skill

General RSI 0.1.1的入口是命令行。文件通过输入目录传入，skill由现有Pi框架加载。这两项不需要改变RSI控制器。

## 附加文件

将问题所需的原始文件放入一个目录，再运行：

```bash
cd /home/jianghp3/general-rsi-20261114-v0.1.1
./run.sh ask '按说明处理附件，并给出可验证结果' --workspace /绝对路径/输入目录
```

控制器保存输入快照，每次实验与复测都从它建立新的副本。Agent在副本中读取文件、运行计算、写出产物；原始输入不被结果覆盖。当前没有“在聊天窗口上传文件后自动绑定到此次goal”的专门界面。

文件能进入工作目录，并不意味着每种格式都已安装了读取工具。文本和代码可直接读取；Excel可以用库或ZIP/XML解析；PDF、音频、图片和视频需要适合的工具。

## 附加skill

已有skill可以放在输入项目的 `.pi/skills/<名称>/SKILL.md` 中，自动发现还取决于Pi的信任与设置。明确指定路径最容易确认加载：新建 `with-skill.json`，内容如下，替换skill绝对路径：

```json
{
  "backend": {
    "type": "pi",
    "provider": "zhizengzeng",
    "model": "deepseek-v4.1-flash",
    "env_file": "~/.pi/agent/zhizengzeng.env",
    "args": ["--offline", "--skill", "/绝对路径/skill目录/SKILL.md"]
  }
}
```

```bash
./run.sh ask '使用指定skill完成附件中的任务' \
  --workspace /绝对路径/输入目录 --config /绝对路径/with-skill.json
```

外层CLI目前通过配置的 `backend.args` 把参数交给Pi。多个skill可以重复添加 `--skill` 和相应路径；路径可指向已有的独立skill目录。skill提供流程、说明和辅助脚本，实际工具与运行依赖仍需可用。此次GAIA试跑使用了一个只指导读取表格格式、未含答案的 `spreadsheet-evidence` skill，其实际调用情况记录在试跑报告中。

## 图片和视频

核对当前账户配置，智增增 `deepseek-v4.1-flash` 的 `input` 是 `["text"]`。在这个配置下，Pi的图片读取工具会提示图片未发送给模型。因此，不能把“图片已复制进目录”当成“模型已经看到图片”。这描述的是当前账户与运行配置，没有据此判断该模型服务的全部潜在能力。

保持主Agent使用默认Flash时，可以接入OCR、图片识别工具或视觉子Agent，把实际识别结果作为有出处的工具反馈。视频还需要解码、抽帧、时间戳和必要时的转录工具；当前控制器没有自带的视频理解管线。接入多模态模型时，也要使用与其消息格式兼容的Agent框架/接口，单独修改配置中的能力标签不能证明支持。

此次GAIA附件是Excel。单元格颜色可从工作簿的格式数据中提取；完成这种题不能证明原生图片或视频理解已经可用。

能力依据：当前Pi安装的 `docs/skills.md`、`dist/core/tools/read.js` 与账户模型输入类型，已留存不含凭据的哈希记录；Pi官方[技能说明](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md)可供参考。

0.1.1默认关闭控制层的单次Agent、目标总时长和验收时限。仍可配置资源预算、手动停止；需要时间限制时再显式设置。
