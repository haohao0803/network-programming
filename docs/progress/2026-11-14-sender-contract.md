# 2026-11-14 — haohao0803 自适应发送端边界验收

## 范围

以整合提交 `7e6b93df8f0d0ba7e6399d9db1dbcc85eebb37ab` 为基线。
当天检查远端后，实验分支仍为 `a5f31eb`，没有新的模式选择/日志分组交付；
PR #3 仍为草稿，main 仍为 `7fd69b6`，未合并。

本人补充发送端调用与计时边界的回归验证，未修改发送端生产代码、Clive 的接收端、
LPCAQ 的拥塞算法或 bushi510 的实验工具。新增测试归 haohao0803；已有模块与测试仍归原作者。

## 新增验证

`tests/test_adaptive_sender_contract.py` 接入 `make check-adaptive`，3 个方法覆盖 4 个场景：

| 场景 | 检查内容 |
| --- | --- |
| DATA 阶段持续噪声 | 重复 ACK 的窗口变化、未来 ACK、其他会话 ID 和非法帧持续到达，仍按 1+2 秒预算有界失败；不产生 RTT 样本或 cwnd 增长 |
| 零窗口持续通知 | 持续 START_ACK 窗口通知不能延长 persist 等待或伪造进展 |
| 丢包后缩窗 | 丢失 DATA 后 rwnd 缩为 1，重传序号遵守有效窗口，最终内容一致 |
| DATA 退避后 FIN | DATA 丢失并恢复后保留 2000 ms RTO、Karn 抑制样本；首次 FIN 确认丢失时仍按固定 1000 ms 重试并完成 |

持续通知测试为 3 秒等待预算提供调度余量（2.8–5.5 秒断言）；子进程另有 9 秒硬上限。
这些对端均为测试夹具，不作为真实接收端、netem 或性能证据。
CI 在 Ubuntu/macOS 的现有自适应步骤执行新增检查，并上传 `results/sender-contract` CSV/JSON。

## 本机结果与证据

在 WSL2 Ubuntu 的临时网络命名空间中运行，未改宿主接口或 VPN。
WSL 启动时报告 NAT 失败回退 VirtioProxy 及 localhost 代理提示；本轮隔离 lo 检查不依赖宿主回环。

- 强制重建三个 C 程序及公式检查程序，C11 `-Wall -Wextra -Wpedantic -Werror -O2` 通过。
- 新增 3 个测试方法/4 个场景通过；首次单独验证及完整入口中的结果均保留。
- 更新后的 `make check-adaptive`：公式检查、原有 6 个模拟对端方法、新增 3 个方法、真实两端 3 个方法/10 个场景通过。
- 真实两端覆盖 0 B 至 1 MiB、DATA/ACK/FIN_ACK 首次丢失、rwnd=2 和零窗口恢复，实际输出 SHA-256 与源文件一致。
- 生产源码未变，本轮本机未重复固定模式全套；跨平台固定/自适应回归由本次提交的 CI 另行验收。

证据目录：[2026-11-14-sender-contract](2026-11-14-sender-contract/adaptive-validation.log)。
`build.log` / `formula-build.log` 为严格编译与公式结果；`environment.log` 保存系统、编译器、Python 与源码/二进制 SHA-256。
`noise-*.csv/json`、`shrink.csv/json`、`control-timer.csv/json` 为新增夹具场景；
`real-transfer/*.csv/json` 为现有真实两端场景证据。测试目录中的 CSV/JSON 是运行原始输出，不用于性能排序。

复跑：

```sh
make -B all CFLAGS='-std=c11 -Wall -Wextra -Wpedantic -Werror -O2'
# 普通 Ubuntu/macOS 回环正常时直接运行；本机 WSL 用临时网络：
sudo unshare --net sh -c 'ip link set lo up && make check-adaptive'
```

## 下一阶段与可转发文字

实验模式选择、参数日志及分组仍由 bushi510 交付；正式弱网/竞争流量、实际 VPS、跨机器及最终汇报继续待办。
本阶段补足本人发送端边界验收后暂停，不自动启动下一阶段。PR #3 继续等待组员模块/接口审阅，不合并 main。

可转发给实验负责人：

> 整合分支已补充自适应发送端边界回归，接口保持不变。请在你的实验分支补固定/自适应模式选择、分组和 schema 2 参数日志采集；保持固定模式基线。每次记录模式、完整命令、提交号、双方退出码、文件哈希与网络参数/种子，失败也保留。自适应 DATA 会指数退避，请相应调整 receiver idle 和整场超时；START/FIN 仍使用固定计时。交付 PR 后我负责接入和问题修复。实际服务器请所有者最迟 11 月 16 日完成预检，回传环境、提交号与日志。
