# 小型 Ubuntu / Debian 服务器部署

由持有服务器的组员操作。按 1 vCPU、1 GiB 内存、15 GB SSD 准备，先运行单组、小文件实验。真实可用内存、磁盘、权限和内核能力以上机检查为准。

## 1. 连接服务器

Windows 使用 PowerShell 的 SSH，Mac 使用终端的 SSH。地址、用户名、端口采用服务器所有者提供的信息。

```sh
ssh -p PORT USER@HOST
```

此命令中的 PORT、USER、HOST 是待替换项。第一次连接时由服务器所有者核对主机指纹。若账号为 root，后续 sudo 前缀可省略。

## 2. 准备工作目录

在服务器终端中查看系统及剩余资源。

```sh
cat /etc/os-release
free -h
df -h .
```

首次使用、尚无 Git 时，先安装 Git。

```sh
sudo apt-get update
sudo apt-get install -y --no-install-recommends git
```

在该账号的项目目录克隆。首次克隆只执行一次，已有目录则使用后面的更新步骤。

```sh
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/haohao0803/network-programming.git
cd network-programming
sudo sh scripts/server_setup.sh
```

安装脚本适用于 Ubuntu / Debian，安装 C 编译器、Make、Python 3、Git、iproute2 和 util-linux。每次失败后根据错误处理，再重新执行即可。资源有限，构建使用单任务 make。

## 3. 构建与验收

以项目目录所有者运行构建。

```sh
make
make check
sudo python3 scripts/check_server.py
```

make check 检查默认网络中的本机 UDP 收发、非法端口与无响应超时。默认网络若有异常，仍可单独执行 check_server.py，看隔离网络中的行为。

服务器检查脚本重新编译一个临时示例，然后创建临时网络命名空间，在其中检查以下项目。

1. UDP 双向收发、非法端口和有界超时。
2. 加入 netem 5 ms 延迟后再次检查 UDP 收发。
3. 创建一对 veth，验证后续搭建双端拓扑所需的内核能力。

结束时打印 PASS Server prerequisites for the current UDP starter 表示当前隔离环境预检通过。它验证了现有探测示例和实验环境，可靠文件传输与正式性能评估按 PLAN.md 继续开发。

所有网络变更在新建命名空间内执行，检查进程退出后该环境随之释放。服务器内部的探测数据通过回环接口交换。当前阶段远程操作使用已有 SSH 入口。

如需保存终端结果，在服务器的 Bash 中运行以下命令，保留命令原始退出状态。

```bash
mkdir -p results
set -o pipefail
sudo python3 scripts/check_server.py 2>&1 | tee "results/server-check-$(date +%Y%m%d-%H%M%S).log"
```

## 4. 遇到错误

| 错误 | 处理 |
| --- | --- |
| sudo 提示无权限 | 由服务器所有者使用具备权限的账号运行安装与隔离检查 |
| apt 无法获取软件包 | 记录完整报错，检查服务器的软件源、DNS 和外网连接 |
| 缺少 cc、make、ip、tc 或 unshare | 重新执行安装脚本并确认安装成功 |
| unshare 提示 Operation not permitted | 确认使用 sudo，并请提供商确认网络命名空间权限，容器型 VPS 可能受限制 |
| tc 提示 netem 不可用，或 veth 创建失败 | 请提供商或管理员确认对应内核功能 |
| 默认网络超时，但隔离检查通过 | 使用隔离实验环境继续开发，另行记录默认网络问题 |
| 编译或 UDP 检查失败 | 保存最先出现的错误及完整检查结果，连同提交号反馈给小组 |

## 5. 团队使用与更新

四个人使用 GitHub 分支与 PR 协作，部署账号负责更新已合并的 main。更新前检查服务器目录有无未提交工作。

```sh
git status --short
git pull --ff-only
make
make check
sudo python3 scripts/check_server.py
```

已有修改时先由修改者提交或妥善保存，再更新。正式实验中固定提交号、文件、参数和运行次数，按顺序执行各组实验并记录服务器负载。试验数据先保留在 results/，正式报告需要的数据另行整理并注明来源。

## 实际部署记录

目标服务器的安装、默认网络测试、隔离网络预检和资源检查，均由服务器所有者执行后填写。

- [ ] 已确认系统、资源和管理员权限。
- [ ] 安装与构建完成。
- [ ] 默认网络 make check 完成并记录结果。
- [ ] 隔离网络检查通过。
- [ ] 已保存提交号与检查日志。

参考 [unshare 手册](https://man7.org/linux/man-pages/man1/unshare.1.html) 和 [netem 手册](https://man7.org/linux/man-pages/man8/tc-netem.8.html)。
