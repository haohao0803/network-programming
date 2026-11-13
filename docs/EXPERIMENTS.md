# 测试、Mac 验证与实验复跑

本分支整合发送端 `b3a4b452c3510da9e13a0621b91dce9eec056097` 与 Clive 接收端
`2f2613a2eb38cd9ac1d1bd6d67ff69689cef5eaf`。保留两人的 Git 历史，协议不变。
Mac 兼容修复仅在 Apple 平台启用 `_DARWIN_C_SOURCE`，使严格 POSIX 模式下
`MSG_DONTWAIT` 可见，不把非阻塞调用退化为阻塞调用。

## 本机验收

在仓库根目录执行，所有工具只用 Python 标准库：

```sh
make clean
make CFLAGS='-std=c11 -Wall -Wextra -Wpedantic -Werror -O2'
make check
RUDP_RESULTS_JSON="$PWD/results/integration.json" make check-integration
python3 scripts/file_tools.py generate test-data/demo
```

`make check` 运行 UDP 探测、发送端 15 个及接收端 16 个测试方法。
`check-integration` 复用 Clive 提供的真实两端测试，运行 31 个子场景。
Python 转发器只转发、丢弃、重复和重排实际报文，不代替 C 接收端。
程序能监听本机 UDP 是前提；受限沙箱中的权限错误应单独记录。

生成器创建 0、1、1023、1024、1025、1 MiB、10 MiB 的可重复二进制文件和哈希清单。
同一个目录不能重复生成，以防覆盖数据；复跑请使用新目录。

手动演示，终端 A：

```sh
mkdir -p outputs
./build/rudp-recv 127.0.0.1 19090 outputs/demo-new.bin 64 15000 7000
```

终端 B（应在接收端空闲超时前启动）：

```sh
./build/rudp-send 127.0.0.1 19090 test-data/demo/payload-1048576.bin 1000 5 16
python3 scripts/file_tools.py verify test-data/demo/payload-1048576.bin outputs/demo-new.bin
```

哈希工具返回 0 表示大小和 SHA-256 都相等，1 表示不一致，2 表示路径/读写错误。
Mac 也可使用 `shasum -a 256`，Linux 可使用 `sha256sum`。
接收文件路径必须尚未存在。发送端成功后，接收端仍等待完成保留期再退出。

## 批量正常网络实验

```sh
python3 scripts/run_experiments.py \
  --file test-data/demo/payload-1048576.bin \
  --windows 1 16 --repeats 3 --output results/normal-new
python3 scripts/summarize_results.py results/normal-new/runs.jsonl \
  --output results/normal-summary-new
```

输出目录必须是新目录。每次传输启动真实 C 两端，记录：命令、系统、Git 提交及脏状态、
二进制哈希、源文件与接收文件哈希、两端退出码、发送耗时、重传数和最大在途数。
成功与失败都写入 `runs.jsonl`。失败不计入成功耗时/吞吐量均值，计入完成率分母。
计时从启动发送进程到发送进程结束，不包含接收端 FIN 保留期；有效吞吐量单位为 B/s。
脚本返回非零表示至少一次失败。默认删除哈希验证成功的接收文件以节省磁盘，
`--keep-files` 可保留；失败输出和原始日志保留。

汇总生成 `summary.csv` 和 `goodput.svg`，按场景、窗口及文件大小分组。
本机回环数据只用于验证流程与初步观察，不能作为真实公网性能或拥塞控制结论。
当前窗口 16 仍是固定窗口基线，尚未接入 LPCAQ 的自适应计时与拥塞模块。

## Linux 服务器预检

服务器所有者在本分支执行，脚本不安装软件、不改宿主网络配置：

```sh
make
sudo python3 scripts/collect_server_preflight.py --output results/server-preflight-new
```

收集系统/内存/磁盘、提交号、工作区状态、默认网络测试、隔离命名空间 netem/veth
检查及工具版本。请回传整个输出目录，包括失败日志。
需要已有 `cc`、Make、Python 3、Git、iproute2、util-linux 和 `ping`。
Ubuntu/Debian 缺少 ping 时对应软件包是 `iputils-ping`，安装由服务器所有者处理。
sudo 执行的构建及输出可能归 root 所有，建议使用专用实验克隆。

## Linux 弱网矩阵（已编写，待真实 Linux 验证）

整合补充：当前 WSL 的系统 tc 6.1.0 不识别 seed；项目已单独构建并安装 tc 6.15.0，实测内核返回 seed=20261113。构建、检查和选择新版的入口见 [LOCAL_TC.md](LOCAL_TC.md)，Linux 接入过程及原始失败日志见 [整合记录](progress/2026-11-13-experiments-integration.md)。本节原 Mac 分支交付时的“待验证”状态保留，最新整合结果在该记录追加。

```sh
sudo python3 scripts/run_netem.py \
  --file test-data/demo/payload-1048576.bin \
  --output results/netem-new --seed 20261113
python3 scripts/summarize_results.py results/netem-new/*/runs/runs.jsonl \
  --output results/netem-summary-new
```

默认共 4 场景 × 2 窗口 × 3 次 = 24 次，窗口为 1 和 16：

| 场景 | 隔离 lo 上配置 |
| --- | --- |
| normal | 无额外故障的 netem |
| delay | 每个出站报文增加 50 ms，往返约增加 100 ms |
| loss | 每个方向的报文独立随机丢包 1% |
| reorder-duplicate | delay 20 ms、reorder 10%、duplicate 1% |

脚本先 `unshare --net`，只修改短生命周期网络命名空间内的 lo。
每个场景保存配置/种子、ping、tc 计数器及传输记录，退出时销毁该命名空间。
ping 后重新创建 qdisc，避免探测流量污染实验计数。
小文件不保证观察到随机丢包/乱序；需要检查 tc 计数器与传输日志后解释结果。
此拓扑用于初期双向弱网测试，尚未实现独立 veth 链路、限速与竞争流量，
不能据此宣称完成拥塞控制性能评估。后续应补独立链路实验与 RTT/RTO/cwnd 曲线。

netem 随机种子需要服务器内核/iproute2 支持；不支持时会明确失败，不静默省略。
即使种子相同，调度和报文数量不同也可能使实际故障位置不同。
1 MiB 文件在停止等待、100 ms RTT 下每次可能超过 100 秒；先小文件预跑，
再固定正式文件和超时预算，完整记录失败。

## 当前证据与未完成项

- Mac 实测见 [11 月 13 日记录](progress/2026-11-13-mac.md)。
- 真实 Linux 服务器预检、跨机器传输及 netem 矩阵尚未运行：当前没有服务器连接信息。
- LPCAQ 分支和计时/拥塞接口尚未提供，当前结果属于固定超时/窗口基线。
- RTT/RTO/cwnd 图表待该模块日志就绪后接入，当前图表仅使用真实吞吐量数据。
