# 2026-11-13 — haohao0803 实验工具接入与环境修复

本次将 bushi510 的 `feature/experiments`（`a5f31eb`，Mac 兼容修复 `99ca5b6`）整合至现有 integration/file-transfer。保留组员作者与原始记录，冲突使用已验证的统一构建入口，并补充其证据链接。

## 范围和归属

Apple 平台 `_DARWIN_C_SOURCE` 修复、测试文件、哈希、批量实验、汇总及 Linux netem 脚本由 bushi510 提供。本人负责整合、Linux 接入检查和项目专用 tc 环境，不把组员的工具记作本人编写。

组员 Mac 记录针对其交付版本。当前整合还含新增 CSV 功能，需要 bushi510 在 Mac 重新检查；不能将旧版本测试直接标成当前版本全部通过。

## 已验证内容

- 当前整合通过探测及 sender/window/trace/receiver 的 7+8+6+16 个测试方法；新增真实日志收发在窗口 1/4 下均通过。见 [validation.log](2026-11-13-experiments-integration/validation.log)。
- Linux 两端构建产物 SHA-256 与上一轮完整 31 场景联调的二进制完全相同；Apple 条件编译段未改变本机 Linux 行为，因此未重复整轮测试。
- 组员文件生成工具和哈希清单成功；批量工具以 1025 B、窗口 1/16 各运行一次，双方成功、哈希一致；汇总 CSV/SVG 生成成功。原始数据在 [normal](2026-11-13-experiments-integration/normal/runs.jsonl)、[summary](2026-11-13-experiments-integration/summary/summary.csv)。这里只验证工具接入，不作为吞吐量比较结论。
- 此次 root 冒烟运行遇到 Git 所有权限制，元数据提交号/工作区状态为 null，原样保留。二进制与文件哈希仍已保存；该数据不作为版本信息齐全的正式实验结果。

## tc 安装与固定种子

原系统 iproute2 6.1.0 直接拒绝 seed，失败原文见 [netem-capability.log](2026-11-13-experiments-integration/netem-capability.log)。未移除种子来绕过问题。

从 Linux 官方站点下载 iproute2-6.15.0 源码，归档 SHA-256 为 `8041854a882583ad5263466736c9c8c68c74b1a35754ab770d23343f947528fb`。增加 pkg-config、bison、flex 等约 4.4 MB 构建依赖，以单任务构建 lib/tc。首次直接进入子目录缺少顶层编译标志，改用官方顶层 `make -j1 SUBDIRS="lib tc"` 后成功。

仅将 tc 安装到 build/tools/bin，不执行系统 make install。`scripts/with_local_tc.sh` 将项目版本只应用于该命令及其子进程。

实测 WSL 内核 6.18.33.2 下，新工具显示 iproute2-6.15.0，系统原版仍为 6.1.0；在临时网络命名空间中成功配置 1% loss，JSON 返回 `seed: 20261113`。见 [local-tc-capability.log](2026-11-13-experiments-integration/local-tc-capability.log)。源码、构建产物和依赖不作为本组协议实现提交，复现步骤见 [LOCAL_TC.md](../LOCAL_TC.md)。

普通批量工具验证与 tc 能力验证已经通过；固定种子多场景脚本的接入结果随后追加，正式 24 次矩阵及竞争流量实验仍按计划继续。实际 VPS、跨机器传输和 LPCAQ 自适应参数模块仍未完成。
