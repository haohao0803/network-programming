# 项目专用 tc 6.15.0

Ubuntu 24.04 自带的 iproute2 6.1.0 在本机不接受 netem `seed` 参数。项目选择单独构建 tc 6.15.0，将程序放在 `build/tools/bin/tc`。系统 `/usr` 下的工具保持原版本，只有通过本页入口执行的命令会优先使用项目版本。

源码来自 Linux 官方发布站点：

- https://mirrors.edge.kernel.org/pub/linux/utils/net/iproute2/iproute2-6.15.0.tar.xz
- 本次下载 SHA-256：`8041854a882583ad5263466736c9c8c68c74b1a35754ab770d23343f947528fb`

源码和二进制放在忽略的 build 目录中，不作为本组自己编写的代码提交。服务器应在自身 Linux 环境重新构建，不能直接使用 Mac 或其他系统的二进制。

## Ubuntu / Debian 构建

在仓库根目录执行。需要普通账户可写项目目录，软件包安装才使用 sudo。

```sh
sudo apt-get install -y --no-install-recommends build-essential pkg-config bison flex curl xz-utils
mkdir -p build/toolchain
curl --fail --location --output build/toolchain/iproute2-6.15.0.tar.xz \
  https://mirrors.edge.kernel.org/pub/linux/utils/net/iproute2/iproute2-6.15.0.tar.xz
printf '%s\n' '8041854a882583ad5263466736c9c8c68c74b1a35754ab770d23343f947528fb  build/toolchain/iproute2-6.15.0.tar.xz' | sha256sum -c -
tar -xf build/toolchain/iproute2-6.15.0.tar.xz -C build/toolchain
(cd build/toolchain/iproute2-6.15.0 && ./configure && make -j1 SUBDIRS="lib tc")
mkdir -p build/tools/bin
install -m 0755 build/toolchain/iproute2-6.15.0/tc/tc build/tools/bin/tc
sh scripts/with_local_tc.sh
```

构建使用一个任务，避免小服务器内存压力。未执行 `make install`。可选库未安装时相应非本项目功能不启用；是否满足 netem 实验，以实际命令验证为准。

## 验证当前内核

```sh
sudo sh scripts/with_local_tc.sh unshare --net sh -c 'ip link set lo up && tc qdisc add dev lo root netem loss random 1% seed 20261113 && tc -j -s qdisc show dev lo'
```

只有新建网络命名空间的 lo 会改变，命令退出后释放该环境。CLI 支持不等于内核支持；必须看到设置成功及返回的配置，才可继续使用固定种子。此检查不证明实际 VPS 有相同能力。

## 使用组员的实验工具

```sh
sudo sh scripts/with_local_tc.sh python3 scripts/run_netem.py \
  --file test-data/demo/payload-1048576.bin --output results/netem-new --seed 20261113
```

输出目录必须尚未存在。先小文件、`--repeats 1` 预跑，再固定正式文件和参数。新工具只对本次命令和子进程生效；直接运行系统 tc 仍可能是 6.1.0。

以 root 在另一个用户的仓库中运行时，Git 可能拒绝读取提交信息；元数据中 null 不代表干净版本。正式数据应由仓库所属账户运行，或在受控运行环境中仅对这一已知仓库配置信任，再确认提交号与工作区状态已记录。不得事后把缺失字段填成未经验证的版本。
