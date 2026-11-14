# 2026-11-13 — haohao0803 自适应发送端集成

## 本人完成与贡献边界

以整合分支 `35b1af1e9dba384c05632a16635c4f03feed46dd` 为基线，引入 LPCAQ PR #5 提交 `edd81996aad5eca2407760e6564007700b854b0a`，保留其模块、发送端接入、测试及说明的原始作者。Mac 修复及实验工具仍保留 bushi510 作者，接收端仍为 Clive-111 实现。

本人负责合并、核对发送事件和协议兼容、执行本机联合验收、保存证据、更新构建/日志使用说明和整合 CI。未代替组员编写拥塞算法或批量实验工具。未合并到 main。

CI 增加 integration/file-transfer 推送触发；移除旧工作流的命令行 `_DARWIN_C_SOURCE` 定义，使用已整合的源文件 Apple 条件编译修复，避免重复宏定义，并让跨平台 CI 验证实际默认构建入口。

## 本机证据

环境为 WSL2 Ubuntu 24.04，内核 `6.18.33.2-microsoft-standard-WSL2`。网络检查在临时 `unshare --net` 命名空间，先启用 lo，未改宿主/VPN，未修复默认 WSL 回环。

原始结果目录：[2026-11-13-adaptive-integration](2026-11-13-adaptive-integration/validation.log)。本次是在合并提交前对实际组合代码运行；后续合并提交包含相同源码。自适应日志末尾保存源码/二进制 SHA-256，用于将证据与版本对应。

| 范围 | 实际结果 | 证据 |
| --- | --- | --- |
| 构建 | 发送端 C11 `-Wall -Wextra -Wpedantic -Werror -O2`；参数公式程序另以相同严格标志重新编译通过 | validation.log、adaptive-validation.log |
| 固定模式单模块 | UDP 探测，以及 sender/window/trace/receiver 的 7+8+6+16 个测试方法通过 | validation.log |
| 固定模式真实两端 | 31 场景全部满足预期，29 次确认成功、1 次接收已保存但发送端未确认、1 次永久零窗口有界失败 | fixed.json |
| 固定模式日志联调 | 窗口 1/4 两次真实文件收发与 CSV 一致 | fixed-traces/trace-results.json |
| 自适应公式及模拟对端 | 公式检查和 6 个测试方法通过，覆盖增长/流控、Karn、退避、零窗口、无进展上限及参数边界 | adaptive-validation.log |
| 自适应真实接收端 | 3 个测试方法、10 个场景均发送成功且实际落盘哈希一致 | adaptive/*.json、adaptive/*.csv |

自适应实际文件覆盖 0、1、1024、1025、1048576 B；另有 DATA/ACK/FIN_ACK 各首次丢失、rwnd=2、零窗口恢复。1 MiB 哈希为 `6d3af67d05cf314316aa12dafbe8f5d258047b5539141cc8ee68949f97ce9dd7`。这些由真实 C 两端运行，故障中继注入报文丢失，不冒充 Linux netem 性能实验。

发送端二进制 SHA-256：`5bc34942e88b90dc977fe7cf189f81eb3d6413949fcb43ba3f56d9118840462f`。接收端仍为 `8d4d3b728d662a4443292f4cd24165345c313570377d3c70c55ccf12695cc289`。

初次临时 Bash 包装脚本末尾带 Windows CR，固定模式检查已全通过后，Make 将目标读作 `check-adaptive\r` 而停止。保留 validation.log 原错误；将临时包装脚本改成 LF 后只继续尚未执行的自适应检查，见 adaptive-validation.log，最终退出 0。这不是协议测试失败，未删改日志来隐藏问题。

## 复跑与剩余事项

```sh
make all CFLAGS='-std=c11 -Wall -Wextra -Wpedantic -Werror -O2'
# WSL default loopback issue: temporary network namespace only
sudo unshare --net sh -c 'ip link set lo up && make check-all && make check-adaptive'
```

普通 Ubuntu/Mac 回环正常时直接运行两个 Make 检查入口。`--adaptive` 的时间单位是毫秒，窗口单位是 DATA 包；完整接口和运行预算见 [CONGESTION.md](../CONGESTION.md)。固定模式 schema 1 不变，自适应 schema 2 单独记录。

当前实验脚本还未传入 `--adaptive`。由实验负责人补充模式选择、参数日志及分组，并配合更长的 DATA 退避设置 receiver idle 和整场超时。正式弱网矩阵、竞争流量、实际 VPS、跨机器和最终汇报仍待完成。最新合并版本的 Mac/Ubuntu CI 结果须按具体提交另行确认，不能用 PR #5 单独分支的绿灯替代。

请组员审阅整合 PR #3 中各自模块及接口，通过后再合并 main。PR #5 的提交进入整合分支后，无须再次把同样提交重复应用到 main；最终以 #3 包含的完整组合为准。
