# 方案依据与范围

核对日期：2026-11-14。本包独立实现一个可运行的通用问题入口与经验演化控制器，沿用现有 Agent 框架。没有把多个研究框架的代码混装为依赖，也没有据它们的论文结果声称本包具有相同效果。

## 现有方案提供哪些部件

| 方案及核对版本 | 有用机制 | 本次判断与采用范围 |
|---|---|---|
| [LoopX](https://github.com/loopx-project/loopx/tree/dd53ca52184cb6283e4728301fa996f8cd7b2ca0)，发布版v2.3.0，源码main另行固定 | 目标状态、所有权、证据、恢复、配额 | 借鉴持续目标的控制边界。Explore默认关闭，Reward Memory为实验性默认关闭能力，不能视为已经开箱完成通用RSI。 |
| [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve/tree/3e72b5bcce27cdd948d8cdff2fa00e67c4d6066c)，v0.4.1 | 代码候选档案、MAP-Elites、多岛、外部评估反馈 | 借鉴保留父版本、并行探索和环境选择；它本身主要是程序优化器。 |
| [ShinkaEvolve](https://github.com/SakanaAI/ShinkaEvolve/tree/7ab55d7254d2732b26d9c30dde23ab777de631cb)，v0.0.8 | 档案、父代选择、新颖性、异步评估、可选元提示优化 | 借鉴候选与父代可追溯、真实评估后选择；未引入其集群调度或声称等效搜索能力。 |
| [GEPA](https://github.com/gepa-ai/gepa/tree/ce5b0d6e3d6af6c6dbec6de2c54536bbb961d309)，main；已发布v0.1.4与main有差别 | 执行轨迹与文本反馈、反思、候选选择；main含optimize_anything | 借鉴“可执行经验→外部反馈→方法修订”。通用优化接口仍需具体对象和评估器。 |
| [Darwin Gödel Machine](https://github.com/kew-lab/darwin-godel-machine/tree/a565fd2d1dca504ef5104a7cc0f3bdc4ab9b4fd2) | Agent自身代码修改、Agent档案与测量 | 用于区分更强的自身代码演化。本包固定控制器，当前只演化方法经验。 |
| [HyperAgents](https://github.com/facebookresearch/HyperAgents/tree/59a68f672dfb92c74aeb7e61535d776fb36e172d) | 可修改任务Agent与元Agent | 用于区分改进程序本身也可变的研究路线；没有把它作为已实现能力。 |
| [CO-Bench](https://github.com/sunnweiwei/CO-Bench/tree/2f6620ba5cc8d80d6fefc121b5ee2c4dcec29f33) | 开发集评估、反馈、最终测试 | 环境验收来源与候选程序演化基准，不能仅凭多轮打分把它叫作通用Agent系统。 |
| [Agentic Harness Engineering](https://github.com/china-qijizhifeng/agentic-harness-engineering) | 固定模型，演化提示/工具/中间件/技能/记忆，检查下一轮结果及回滚 | 本包沿用这种非参数改进方向，但没有迁入原有批次运行器，也没有把部分开源的Agent Debugger当成独立通用组件。 |

这些是源码机制判断，不是复现各论文性能。快照对应的main提交与发布标签分开记录，避免把main新功能误写成发布版已有功能。原始公开仓库元数据和选读文件哈希保存在本次交付记录中。

## 144上原有代码的分类

只读核对了用户指定目录，未修改或启动原实验。

- `/home/jianghp3/CO-Bench`：提交 `2f6620ba5cc8d80d6fefc121b5ee2c4dcec29f33`。自定义 `multica_evolver.py` 走候选、开发集评分、反馈回路；主要演化任务求解程序。
- `/home/jianghp3/funsearch-deepseek-v4-flash-cobench/run_funsearch.py`：确有候选生成、开发集反馈、候选保存与最终测试；也是任务程序演化。
- `/home/jianghp3/agentic-harness-engineering`：提交 `67c91fe94730fe1d094847c01a97d5bbbfe4db80`。`evolve.py` 有工作区快照、改动归因、最好版本、通过目标、演化调用、历史保存和回滚。属于Agent/harness的非参数演化，但原入口围绕批次/Harbor任务。
- 该目录下 `.code_sources/nexau@v0.3.9/examples/deep_research`：NexAU提交 `35ee1861546db3cb280a6e17e38a74060d7c96c3`。主研究Agent和子Agent会检索、阅读、管理研究任务并产出报告；在这些入口未发现“自身策略→独立验收→更新策略→下一代重新载入”的完整回路。因此作为按需研究能力接入，而不是把它单独称为RSI。

## 从原有固定任务实验抽出的约束

本次抽取的是方法约束：固定模型、候选指导明确版本化、从原始输入重新执行、环境反馈与自评分离、同一新版本最多一组复测、至少两次新执行、失败父代得分按0处理、质量无退化、质量或实际token改善、保留父代及失败条件。

这些约束重新实现于本包 `engine.py`、`protocol.py` 和 `storage.py`。原有固定任务实验的成绩不作为本包的通用化效果证据。跨任务收益、复杂工程、长时间稳定运行和真实仿真/集群环境都还需要用户场景验证。
