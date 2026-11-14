# 2026-11-15 正式实验准备与交接

基于最新整合分支 `0796ecb`，准备了正式实验工具；没有执行或编造正式 Linux 测量。
按本人确认，由服务器负责人执行并回传完整原始数据。

新增内容：

- formal_campaign.py：80 次弱网 + 30 次共享链路竞争，至少五次独立重复、不同种子、交错模式顺序。
- run_competition.py / competition_traffic.py：隔离 veth/HTB 10 Mbit/s 共享 FIFO，0/7/12 Mbit/s 恒定 UDP 背景流量。
- report_campaign.py：数据完整性与参数日志哈希验证、失败率、全部成功样本散点、至少五个独立成功重复时的 bootstrap 区间，RTO/SRTT/cwnd/接收窗口曲线。
- archive_campaign.py：停止后完整目录归档与逐文件/压缩包哈希。
- 正式输入快照、成功接收文件、全部失败/超时/参数日志、竞争计数及 tc 状态均保留。

Mac 验证：`make check-experiments` 通过。原 7 项测试及新增 7 项均通过；包括真实固定/自适应收发、
真实本机 UDP 干扰进程、命名空间边界的模拟检查、缺样本/日志篡改/重复编号、统计区间及归档验证。
原始日志见 [formal-tools-validation.log](2026-11-15-formal-tools-validation.log)。
使用既有 16 次 Linux 接入预跑，仅做绘图流程 QA，生成的预览明确标注 PILOT/no performance conclusion；
没有把预跑包装成五次重复，也没有据此写算法提升结论。

**尚未验证：新 Linux veth/HTB 竞争拓扑、完整 110 次运行和正式性能分析。**

## 给服务器负责人的交接文字

请审阅正式实验工具分支 `feature/formal-experiments`，先在专用克隆上运行竞争拓扑 pilot。
完整步骤和条件在 [FORMAL_EXPERIMENTS.md](../FORMAL_EXPERIMENTS.md)。

```sh
git clone --branch feature/formal-experiments https://github.com/haohao0803/network-programming.git network-programming-formal
cd network-programming-formal
git rev-parse HEAD
make all CFLAGS='-std=c11 -Wall -Wextra -Wpedantic -Werror -O2'
make check-experiments
python3 scripts/file_tools.py generate test-data/formal-v1
```

按 LOCAL_TC.md 准备项目内 tc（已有系统 tc 支持 seed 时可直接执行 Python）。
新竞争拓扑预检：

```sh
sudo sh scripts/with_local_tc.sh python3 scripts/formal_campaign.py \
  --phase pilot --repeats 1 --suite competition \
  --file test-data/formal-v1/payload-1048576.bin \
  --competition-file test-data/formal-v1/payload-10485760.bin \
  --output results/competition-pilot-v1
```

确认 ping、HTB/netem 计数器和竞争发送/接收实测速率正常后，固定提交号并执行正式矩阵：

```sh
sudo sh scripts/with_local_tc.sh python3 scripts/formal_campaign.py \
  --file test-data/formal-v1/payload-1048576.bin \
  --competition-file test-data/formal-v1/payload-10485760.bin \
  --output results/formal-v1 --repeats 5 --seed 20261120
```

所有运行停止后归档，原样回传两个文件；不要只发均值或截图：

```sh
sudo python3 scripts/archive_campaign.py --campaign-dir results/formal-v1 \
  --output results/formal-v1.tar.gz
```

需回传 `formal-v1.tar.gz` 和 `formal-v1.tar.gz.sha256.json`。
若预检失败，先回传 pilot 目录及外层执行日志，保留失败样本，不直接启动正式矩阵。
正式矩阵需要数小时、建议至少 1 GiB 空闲磁盘。数据回传后在 Mac 生成正式图表并分析。
