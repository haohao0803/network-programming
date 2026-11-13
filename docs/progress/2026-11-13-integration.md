# 2026-11-13 — haohao0803 两端整合记录

## 本人完成与代码归属

- 完成发送端可选 CSV 事件日志及 6 个方法的验证，源码提交为 `7a3e4c0`，发送端原有窗口提交为 `b3a4b45`。
- 整合 Clive-111 的 `feature/receiver`（`2f2613a`，接收端代码提交 `1bca783`），保留原作者历史。接收端 C 实现和其两个原始测试文件未作修改。
- 解决 Makefile 和 README 冲突，构建同时输出两端；提供 check、check-integration、check-all 入口。
- 测试临时文件默认在已有 TMPDIR 或 `/tmp`，可用 TEST_TMPDIR 指定。此前复跑时 Windows 挂载目录上的并发日志读取报 `No data available`；切换 Linux 临时目录后通过，保留原失败事实。
- 新增本人负责的日志集成测试，使用 Clive 的真实接收进程、实际发布文件和 CSV 核对结果。更新统一运行说明、部署说明和进度。

接收端的乱序缓存、去重、临时文件保存和信号窗口控制均归 Clive；不是本人的接收端开发成果。LPCAQ 参数模块与 bushi510 实验工具仍待对应成员提供。

## 实际验证

在 Windows / WSL 2 Ubuntu-24.04 的临时隔离网络执行；保持 VPN 与默认 WSL 网络设置。两端严格 C11 `-Wall -Wextra -Wpedantic -Werror -O2` 编译及干净 Make 构建通过。

完整命令为 `make clean`、`make`、严格编译两端、`make check-all`；RUDP_RESULTS_JSON 与 RUDP_TRACE_RESULTS_DIR 将输出保存至本记录同名目录。

| 验证 | 结果与证据 |
| --- | --- |
| 探测、sender、窗口、CSV、receiver 回归 | 探测检查及 7+8+6+16 个方法通过，见 [validation.log](2026-11-13-integration/validation.log) |
| 原始真实两端测试 | 8 个方法、31 个场景通过，耗时 60.321 秒；见 [results.json](2026-11-13-integration/results.json) |
| 真实 CSV 联调 | 1 个方法、窗口为 1/4 的两个场景通过，输出哈希与源文件一致；见 [trace-results.json](2026-11-13-integration/trace-results.json) |
| 原始 CSV | [窗口 1](2026-11-13-integration/trace-window-1.csv)、[窗口 4](2026-11-13-integration/trace-window-4.csv)，会话 ID、ACK、结束确认与记录对应 |

31 个原始场景中，29 次两端确认成功；1 次全部 FIN_ACK 丢失，接收端已保存且发送端正确报告未确认；1 次永久零窗口，双方在预算内失败且不发布部分文件。后两种属于符合预期的故障结果，不能计为成功传输。

两个新增日志场景各传输 4097 B，实际输出 SHA-256 均为 `b574ebff706803df9f0a526bdd804c9eba68c5006891005ed3221e4816db1aa6`。记录含两端构建产物 SHA-256，可与本次二进制核对。

这些是在本机隔离网络中运行真实 C 发送端、接收端和实际落盘文件；原始故障由 UDP 转发器按规则注入。尚不是跨机器、VPS 或 Linux netem 性能实验，也不说明自适应 RTO/cwnd 已完成。

## 每日计划与剩余事项

11-12 的本机真实文件联调与 11-13 的本人重传/结束/失败处理均已验证；提前完成 11-14 中本人发送窗口部分并与 Clive 流控联合验证。11-15 的 CSV 事件记录先行完成，自适应参数接入仍未完成。

接下来需要 LPCAQ 的 C 接口、单位和模块代码，供本人接入 ACK/超时事件。bushi510 继续提供 Mac 验证、批量实验及图表；服务器所有者按部署说明回传实际检查日志。最终性能评估、英文汇报及演示验收仍在计划内。

本整合版本放在独立草稿 PR，供组员审阅后合入 main。原模块 PR 保留供个人贡献核对，当前不把 main 描述为已更新。
