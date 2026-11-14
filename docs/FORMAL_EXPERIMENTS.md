# 正式重复实验、竞争流量与原始数据

基线：整合提交 `0796ecbf83bd9ec77811790449ba7f58425de57e`。该提交中的
16 次 Linux netem 是单次接入预跑，只证明流程可运行，不作为性能结论。
本次交付为正式实验工具与方案；新 veth/HTB 竞争拓扑仍需在 Linux 验证，未填写虚构测量值。

## 固定实验矩阵

| 实验 | 条件 | 模式/窗口 | 独立重复 | 次数 |
| --- | --- | --- | --- | --- |
| 弱网 | normal/delay/loss/reorder-duplicate，沿用已预检参数 | fixed/adaptive × 1/16 | 每格 5 次 | 80 |
| 竞争 | 10 Mbit/s 共享 FIFO，双向各 10 ms；UDP 背景载荷 0/7/12 Mbit/s | fixed/adaptive，窗口上限 64 | 每格 5 次 | 30 |

弱网文件固定 1 MiB，竞争文件固定 10 MiB。竞争队列限制 100 个包；负载数值为
UDP 载荷的目标发送速率，不含 UDP/IP 等头部。发送与接收实测速率、错误和逐秒计数独立保存。
这是恒定 UDP 干扰压力实验，不能作为 TCP 竞争公平性或完整 TCP Reno 的结论。

每个重复使用不同种子，同一重复的模式使用相同种子与输入文件。每轮交替两种模式的先后顺序，
竞争条件按记录的种子随机打乱。种子相同不保证不同算法触发相同的实际丢包位置。
不要运行多份 campaign 或在繁忙主机上混跑；正式期间固定代码、二进制、文件、网络参数和机器。

## Mac 上准备计划（不生成测量结果）

```sh
python3 scripts/file_tools.py generate test-data/formal-v1
python3 scripts/formal_campaign.py --plan-only \
  --file test-data/formal-v1/payload-1048576.bin \
  --competition-file test-data/formal-v1/payload-10485760.bin \
  --output results/formal-plan-v1
```

计划包含 35 个任务、110 次传输。默认至少五次独立重复；正式模式拒绝次数不足的配置。
小规模接入测试使用 `--phase pilot --repeats 1` 并选择新的目录。
`--suite netem` 或 `--suite competition` 可分开执行，两份数据分别归档。

## Linux 执行与新竞争拓扑预检

使用新的专用实验克隆，安装/构建方式沿用 SERVER.md、LOCAL_TC.md。
需要 root、unshare、nsenter、ip、支持 seed 的 tc/netem，以及 HTB/veth 内核能力。
先构建：

```sh
make all CFLAGS='-std=c11 -Wall -Wextra -Wpedantic -Werror -O2'
make check-experiments
python3 scripts/file_tools.py generate test-data/formal-v1
```

先对新拓扑跑 pilot，仅验证连通、HTB 限速和竞争流量确实存在：

```sh
sudo sh scripts/with_local_tc.sh python3 scripts/formal_campaign.py \
  --phase pilot --repeats 1 --suite competition \
  --file test-data/formal-v1/payload-1048576.bin \
  --competition-file test-data/formal-v1/payload-10485760.bin \
  --output results/competition-pilot-v1
```

检查每个目录的 ERROR.txt、ping.log、qdisc.log、双方 tc 计数器和 generator/sink JSON/CSV。
目标载荷与实测发送速率偏离超过 50%，或接收不到背景流量，脚本返回配置错误；
正式前还需人工检查速率是否稳定、排队/丢包是否符合场景，不能只依据退出码。
背景流量包含 1 秒预热及接收端 FIN 保留期，汇总速率不得当作文件传输重叠区间的精确竞争速率。
两端 monotonic 时间戳与逐秒计数保留，供按实际传输区间分析。

竞争测试进入新的外层网络命名空间，再创建接收侧嵌套命名空间，两侧经 veth 相连。
仅对实验 veth 应用 HTB/netem，不改变宿主机接口或路由。任务结束关闭背景进程和命名空间持有进程；
campaign 中断会终止该任务的进程组并保留 interrupted/planned 状态。

确认 pilot 后，再正式执行。目录必须是新的，计划目录不能直接当作执行目录：

```sh
sudo sh scripts/with_local_tc.sh python3 scripts/formal_campaign.py \
  --file test-data/formal-v1/payload-1048576.bin \
  --competition-file test-data/formal-v1/payload-10485760.bin \
  --output results/formal-v1 --repeats 5 --seed 20261120
```

若系统 tc 已支持 seed，可省略 `sh scripts/with_local_tc.sh`。
弱网任务统一设每次传输 1800 秒墙钟上限，使两种模式的外部超时条件相同；
接收端仍分别按固定/自适应协议预算配置。竞争任务同样统一设 1800 秒墙钟上限，
比较时必须把超时计入失败，不能把失败样本的耗时混进成功耗时均值。
整个 campaign 可能需数小时；不要把单条命令启动等同于实验完成。

## 完整原始数据

每个任务保留独立目录和外层执行日志，campaign.json 逐次更新任务状态/命令/种子/退出码。
输入文件在正式执行时复制到 inputs/；所有成功接收文件通过 `--keep-files` 保留。
默认至少约 380 MiB 的传输文件，加上完整参数 CSV 和日志，建议预留 1 GiB 以上磁盘。

必须保留整棵目录：

- campaign.json、两个输入文件和各任务执行日志；
- 逐次 JSONL、metadata.json、发送端/接收端日志、参数 CSV、接收文件和哈希；
- netem 配置、种子、ping、tc 前后计数器；
- 竞争生成器/接收器日志、逐秒计数 CSV 和汇总 JSON；
- ERROR.txt、失败、超时、中断及未运行任务；
- 图表与其派生 all_runs.jsonl、summary.csv 和 completeness.json。

禁止覆盖失败样本、只留均值、静默补跑或把新运行填回旧记录。修复后建立新的 campaign；
报告需注明修改和被排除的整批运行，保留原批次。

## 统计与图表

绘图依赖 Python matplotlib，建议在独立虚拟环境中安装；测量工具本身只用标准库。

```sh
python3 scripts/report_campaign.py --campaign results/formal-v1/campaign.json \
  --output results/formal-v1/report
```

生成 PNG/PDF 的成功率及吞吐量散点图、完整 CSV、每组首个成功重复的参数时间曲线。
每条曲线标注“首个成功重复，非平均曲线”，不挑选最快样本。
分组同时区分模式、场景、窗口、文件大小/哈希和二进制哈希，避免混合代码版本。
SHA-256 不匹配的成功记录、参数日志被修改、重复采样编号会阻止报告。

失败进入完成率分母；吞吐量/耗时均值只用成功样本，并报告成功数量及超时数量。
至少五个不同种子的成功重复才显示成功样本均值的 95% percentile-bootstrap 区间
（2000 次、固定统计种子 42）。区间是探索性不确定性描述，五次重复不足以证明算法普遍更优；
不计算或宣传整体“提升百分比”，也不忽略失败率。

默认不完整任务会阻止报告；`--allow-partial` 只生成标记 PARTIAL 的图表。
pilot 始终标记 PILOT/no performance conclusion；只有计划没有测量时拒绝作图。
检查完整性与图表后再撰写结论；单次预跑及本地测试不能替代上述正式结果。

## 停止后归档

```sh
python3 scripts/archive_campaign.py --campaign-dir results/formal-v1 \
  --output results/formal-v1.tar.gz
```

压缩包和逐文件 SHA-256 sidecar 写在原始目录之外，拒绝覆盖、符号链接和活动运行快照。
请回传整个 tar.gz 与同名 `.sha256.json`，不要只发截图。

当前 Mac 上仅能验证编排、真实本机 UDP 竞争工具和绘图。新的 Linux veth/HTB 接入 pilot、
正式 110 次运行及其性能结论尚未完成，需要服务器所有者执行并提供完整数据。
